"""Background coordination for personal Rivals observation.

Only the worker touches SQLite, catalogs and memory. Tk consumes copies and
submits commands; changing pages does not start or stop lap observation.
"""
from __future__ import annotations

import copy
import logging
import math
import queue
import shutil
import threading
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from forza_tracker.telemetry.status import read_telemetry_status
from forza_tracker.paths import application_root
from forza_tracker.rivals_history import sector_memory_observation

log = logging.getLogger(__name__)

VALIDATED_EVENT_BUILD = '3.440.853.0'
VALIDATED_ROUTE_IDS = {69: 'dirt:sekibe-scramble', 62: 'dirt:hirosaki-scramble'}


def _event_name(value) -> str:
    return ' '.join(unicodedata.normalize('NFKC', str(value or '')).casefold().split())


def memory_event_key(event):
    if not isinstance(event, dict):
        return None
    route, name, instance = (event.get(k) for k in ('route_id', 'name', 'instance_id'))
    if (type(route) is not int or not 0 <= route <= 0xffffff
            or not isinstance(name, str) or not name.strip()
            or not isinstance(instance, str) or not instance):
        return None
    return route, instance


def resolve_memory_event(raw, events, *, build=VALIDATED_EVENT_BUILD):
    """Resolve only validated build IDs or unique exact catalog names."""
    if memory_event_key(raw) is None or build != VALIDATED_EVENT_BUILD:
        return None
    name = _event_name(raw['name'])
    matches = {e['event_id']: e for e in events if name in {
        _event_name(e.get(k)) for k in ('event_name', 'name_es', 'name_en', 'name_fr', 'name_ru')}}
    expected = VALIDATED_ROUTE_IDS.get(raw['route_id'])
    if expected:
        # A recognized different name is evidence of an inconsistent read.
        # Other languages can still resolve using the validated route ID.
        if matches and set(matches) != {expected}:
            return None
        return next((e for e in events if e['event_id'] == expected), None)
    return next(iter(matches.values())) if len(matches) == 1 else None


def seed_snapshot_files(catalog_root: Path, bundled_root: Path, target_root: Path):
    """A partial shared cache must not hide the other bundled disciplines."""
    target_root.mkdir(parents=True, exist_ok=True)
    for discipline in ('road', 'street', 'dirt', 'cross-country'):
        filename = f'{discipline}-rivals.json'
        source, target = catalog_root / filename, target_root / filename
        if not source.is_file():
            source = bundled_root / filename
        if source.is_file() and not target.exists():
            shutil.copy2(source, target)


def fresh_telemetry(packet: dict, now: datetime) -> dict | None:
    if not packet.get('connected'):
        return None
    try:
        captured = datetime.fromisoformat(str(packet['last_packet_at']))
        age = (now - captured).total_seconds()
    except (KeyError, TypeError, ValueError):
        return None
    return packet if -0.25 <= age <= 2.0 else None


class EventContextGate:
    """Require current positive Online evidence; Rivals loss is not Online."""

    def __init__(self, started_at: datetime | None = None):
        self.started_at = started_at or datetime.now(timezone.utc)
        self._blocked_online: set[str] = set()

    def select(self, active: bool | None, online, capture_active: bool,
               now: datetime, *, memory_status: str = '') -> str:
        event_id = getattr(online, 'event_id', '')
        if active is True:
            if event_id:
                self._blocked_online.add(event_id)
            return 'rivals'
        if active is None:
            return 'waiting' if memory_status in {'not_running', 'no_process', 'process_exit', 'closed'} else 'unknown'
        if not capture_active or online is None or online.phase == 'closed':
            return 'waiting'
        try:
            age = (now - online.updated_at).total_seconds()
            current_run = online.updated_at >= self.started_at
        except (TypeError, ValueError):
            return 'waiting'
        if current_run and 0 <= age <= 1800 and event_id not in self._blocked_online:
            return 'online'
        return 'waiting'


class RivalsRuntime:
    def __init__(self, data_root: Path, catalog_root: Path, profile_id: str,
                 *, reader=None, telemetry_reader=read_telemetry_status):
        self.data_root, self.catalog_root = Path(data_root), Path(catalog_root)
        self.profile_id = profile_id or 'local'
        self._reader = reader
        self._telemetry_reader = telemetry_reader
        self._stop = threading.Event()
        self._commands: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._refresh_thread: threading.Thread | None = None
        self._state = {'active': None, 'status': 'scanning', 'current': {},
                       'attempts': [], 'bests': [], 'events': [], 'revision': 0,
                       'telemetry_fresh': False, 'refresh': {}, 'error': ''}

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name='rivals-personal', daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._reader is not None:
            self._reader.close()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)

    def snapshot(self, *, live_only=False) -> dict:
        with self._lock:
            if live_only:
                return copy.deepcopy({key: self._state.get(key) for key in
                                      ('active', 'current', 'telemetry_fresh', 'clock')})
            return copy.deepcopy(self._state)

    def select_event(self, event_id: str):
        self._commands.put(('event', event_id))

    def refresh_snapshot(self, discipline: str):
        self._commands.put(('refresh', discipline))

    def set_profile(self, profile_id: str):
        self._commands.put(('profile', profile_id or 'local'))

    def _publish(self, **changes):
        with self._lock:
            self._state.update(changes)

    def _run(self):
        from forza_tracker.rivals_history import LapTracker, RivalsStore
        from forza_tracker.rivals_memory import RivalsMemoryReader
        from forza_tracker.rivals_snapshot import SnapshotCatalog, compare_attempt, snapshot_summary
        from forza_tracker.rivals_sectors import SectorTracker

        try:
            store = RivalsStore(self.data_root / 'data' / 'rivals' / 'personal.sqlite3')
            store.classify_pending(self.profile_id)
            snapshot_root = self.data_root / 'data' / 'rivals' / 'snapshots'
            seed_snapshot_files(self.catalog_root, application_root() / 'data' / 'recommender', snapshot_root)
            catalog = SnapshotCatalog(snapshot_root)
            tracker = LapTracker(self.profile_id)
            sectors = SectorTracker()
            self._reader = self._reader or RivalsMemoryReader()
            events = catalog.events()
            selected = None
            selected_source = None
            previous_event_key = None
            last_telemetry_key = None
            wait_for_new_packet = False
            previous_active = None
            previous_pid = None
            dirty = True
            revision = 0
            attempts, bests = [], []
            previous_clock = None
            snapshot_key = None
            current_snapshot = None

            def persist(items):
                nonlocal dirty
                for attempt in items:
                    store.save_attempt(attempt)
                    board = catalog.board(attempt['event_id'], attempt['car_class'])
                    store.record_comparison(attempt['attempt_id'], compare_attempt(attempt, board))
                    dirty = True

            while not self._stop.is_set():
                reading = self._reader.sample()
                sampled_at = time.monotonic()
                if self._stop.is_set():
                    break
                active, pid = reading.get('active'), reading.get('process_id')
                # An actual exit or process replacement invalidates the manually
                # selected event. Unknown readings retain identity but break observation.
                if ((active is False and previous_active is not False)
                        or (previous_pid is not None and pid is not None and pid != previous_pid)
                        or (previous_pid is not None and reading.get('status') == 'process_exit')):
                    persist(tracker.finish())
                    tracker = LapTracker(self.profile_id)
                    selected = None
                    selected_source = None
                    previous_event_key = None
                    wait_for_new_packet = True
                if pid is not None:
                    previous_pid = pid
                elif reading.get('status') == 'process_exit':
                    previous_pid = None
                previous_active = active
                raw_event = reading.get('event') if active is True else None
                raw_key = memory_event_key(raw_event)
                resolved = resolve_memory_event(raw_event, events)
                if ((previous_event_key is not None and raw_key != previous_event_key)
                        or (selected_source == 'manual' and raw_key != previous_event_key)
                        or (selected_source == 'memory' and resolved != selected)):
                    persist(tracker.finish())
                    tracker = LapTracker(self.profile_id)
                    selected = None
                    selected_source = None
                    wait_for_new_packet = True
                previous_event_key = raw_key
                while not self._commands.empty():
                    command, value = self._commands.get_nowait()
                    try:
                        if command == 'event':
                            event = next((e for e in events if e['event_id'] == value), None)
                            if active is not True or event is None or resolved is not None:
                                self._publish(error='event_requires_rivals')
                                continue
                            persist(tracker.finish())
                            tracker = LapTracker(self.profile_id)
                            tracker.select_event(event['event_id'], event['event_name'], event['event_kind'], event_source='manual')
                            selected = event
                            selected_source = 'manual'
                            wait_for_new_packet = True
                        elif command == 'refresh':
                            if self._refresh_thread is None or not self._refresh_thread.is_alive():
                                self._publish(refresh={'status': 'refreshing', 'discipline': value})
                                def download(discipline=value):
                                    result = SnapshotCatalog(snapshot_root).refresh(discipline)
                                    if not self._stop.is_set():
                                        self._commands.put(('refreshed', result))
                                self._refresh_thread = threading.Thread(target=download, name='rivals-snapshot', daemon=True)
                                self._refresh_thread.start()
                        elif command == 'refreshed':
                            catalog = SnapshotCatalog(snapshot_root)
                            events = catalog.events()
                            self._publish(refresh=value)
                            dirty = True
                        elif command == 'profile' and value != self.profile_id:
                            persist(tracker.finish())
                            self.profile_id = value
                            store.classify_pending(self.profile_id)
                            tracker = LapTracker(value)
                            selected = None
                            selected_source = None
                            wait_for_new_packet = True
                            dirty = True
                        self._publish(error='')
                    except (OSError, ValueError, KeyError) as exc:
                        log.warning('Rivals command failed: %s', exc)
                        self._publish(error='command_failed')
                # Profile changes and fresh catalogs also receive the current
                # event automatically; manual fallback never overrides it.
                resolved = resolve_memory_event(raw_event, events)
                if resolved is not None and (selected != resolved or selected_source != 'memory'):
                    persist(tracker.finish())
                    tracker = LapTracker(self.profile_id)
                    tracker.select_event(resolved['event_id'], resolved['event_name'], resolved['event_kind'], event_source='memory')
                    selected, selected_source = resolved, 'memory'
                    wait_for_new_packet = True
                packet = self._telemetry_reader()
                now = datetime.now(timezone.utc)  # after reading avoids negative file age
                fresh = fresh_telemetry(packet, now)
                packet_key = (pid, packet.get('timestamp_ms'))
                if wait_for_new_packet:
                    if packet_key == last_telemetry_key:
                        fresh = None
                    elif fresh is not None:
                        wait_for_new_packet = False
                last_telemetry_key = packet_key
                persist(tracker.update(active, fresh, process_id=pid,
                                       memory_validity=reading.get('validity') if selected else None))
                if dirty:
                    attempts = store.list_attempts(self.profile_id)
                    bests = store.best_attempts(self.profile_id)
                    for row in attempts:
                        row['current_comparison'] = compare_attempt(row, catalog.board(row['event_id'], row['car_class']))
                    by_id = {a['attempt_id']: a for a in attempts}
                    bests = [by_id.get(a['attempt_id'], a) for a in bests]
                    revision += 1
                    dirty = False
                current = dict(tracker.current or {})
                current['event'] = selected
                current['event_source'] = selected_source
                current['raw_event'] = raw_event if raw_key is not None else None
                identity = (self.profile_id, selected['event_id'], raw_key, pid,
                            current.get('car_ordinal'), current.get('car_class'), current.get('performance_index')) if (
                                selected and raw_key and current.get('car_ordinal') and active is True) else None
                observation = None
                paused = fresh is not None and not fresh.get('is_race_on')
                if identity and fresh is not None:
                    observation = (reading.get('validity') if paused
                                   else sector_memory_observation(reading.get('validity'), fresh))
                sectors.update(observation, identity, paused=paused)
                current['sectors'] = sectors.snapshot()
                # Board lookup hashes a catalog: cache by selection/catalog,
                # never repeat it at the memory sampling frequency.
                board_key = (id(catalog), self.profile_id, selected['event_id'] if selected else None,
                             current.get('car_class'))
                if board_key != snapshot_key:
                    snapshot_key = board_key
                    current_snapshot = snapshot_summary(catalog.board(selected['event_id'], current['car_class']),
                                                        self.profile_id) if selected and current.get('car_class') else None
                # Presentation only. Saved attempts and sector boundaries keep
                # their measured game times; Tk can animate between samples.
                clock = None
                if observation is not None and identity is not None and not paused:
                    clock_key = (identity, observation['record_id'], observation['car_id'], observation['lap_start_clock'])
                    race_clock = observation['race_clock']
                    clock = {'seconds': race_clock - observation['lap_start_clock'],
                             'sampled_at': sampled_at,
                             'advancing': bool(previous_clock and previous_clock[0] == clock_key
                                               and 0 < race_clock - previous_clock[1] <= .25)}
                    previous_clock = (clock_key, race_clock)
                else:
                    previous_clock = None
                current_bests = [a for a in bests if selected and a['event_id'] == selected['event_id']
                                 and a['car_ordinal'] == current.get('car_ordinal')
                                 and a['car_class'] == current.get('car_class')]
                session_attempts = [a for a in attempts if a['session_id'] == current.get('session_id') and a.get('completed')]
                self._publish(**reading, current=current, events=events, attempts=attempts,
                              bests=bests, best=current_bests[0] if current_bests else None,
                              last=session_attempts[0] if session_attempts else None, clock=clock,
                              snapshot=current_snapshot,
                              telemetry_fresh=fresh is not None and bool(fresh.get('is_race_on'))
                              and bool(fresh.get('car_ordinal')), revision=revision)
                self._stop.wait(0.05)
            # Drain pending profile changes before the daemon can be torn down.
            # Closing never starts a new download or a new observed session.
            while not self._commands.empty():
                command, value = self._commands.get_nowait()
                if command == 'profile':
                    self.profile_id = value
            persist(tracker.finish())
        except Exception:
            log.exception('Personal Rivals observation stopped')
            self._publish(active=None, status='error', error='capture_failed')
        finally:
            if self._reader is not None:
                self._reader.close()
