from datetime import datetime, timedelta, timezone
import json
import threading
import time
import pytest
from types import SimpleNamespace

from forza_tracker.rivals_history import RivalsStore
from forza_tracker.rivals_runtime import EventContextGate, fresh_telemetry
from forza_tracker.rivals_runtime import RivalsRuntime
from forza_tracker.rivals_snapshot import SnapshotCatalog


def test_metacars_only_cache_falls_back_to_each_bundled_rivals_file(tmp_path):
    from forza_tracker.rivals_runtime import seed_snapshot_files
    cache, bundled, target = (tmp_path / name for name in ('cache', 'bundled', 'personal'))
    cache.mkdir()
    bundled.mkdir()
    target.mkdir()
    (cache / 'tierlist-cars.ts').write_text('metacars')
    for discipline in ('road', 'street', 'dirt', 'cross-country'):
        (bundled / f'{discipline}-rivals.json').write_text(discipline)
    (cache / 'road-rivals.json').write_text('cached road')
    (target / 'dirt-rivals.json').write_text('personal dirt')
    seed_snapshot_files(cache, bundled, target)
    assert (target / 'road-rivals.json').read_text() == 'cached road'
    assert (target / 'street-rivals.json').read_text() == 'street'
    assert (target / 'cross-country-rivals.json').read_text() == 'cross-country'
    assert (target / 'dirt-rivals.json').read_text() == 'personal dirt'


NOW = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)


def online(phase='starting_grid', updated=NOW, event_id='online-1'):
    return SimpleNamespace(phase=phase, updated_at=updated, event_id=event_id)


def test_stale_zero_and_future_telemetry_do_not_become_laps():
    packet = {'connected': True, 'last_packet_at': NOW.isoformat(), 'car_ordinal': 1155}
    assert fresh_telemetry(packet, NOW) is packet
    assert fresh_telemetry(packet, NOW + timedelta(seconds=4)) is None
    assert fresh_telemetry(packet, NOW - timedelta(seconds=4)) is None
    assert fresh_telemetry({'connected': False, 'last_packet_at': NOW.isoformat()}, NOW) is None
    assert fresh_telemetry({'connected': True, 'last_packet_at': 'broken'}, NOW) is None
    assert fresh_telemetry({}, NOW) is None


def test_old_online_is_not_resurrected_after_rivals_or_read_loss():
    gate = EventContextGate(started_at=NOW)
    old = online(updated=NOW - timedelta(minutes=3))
    assert gate.select(False, old, True, NOW) == 'waiting'
    current = online()
    assert gate.select(False, current, True, NOW) == 'online'
    assert gate.select(True, current, True, NOW) == 'rivals'
    assert gate.select(None, current, True, NOW) == 'unknown'
    assert gate.select(False, current, True, NOW) == 'waiting'
    assert gate.select(False, online(event_id='online-2', updated=NOW + timedelta(seconds=1)), True, NOW + timedelta(seconds=1)) == 'online'


def test_online_requires_capture_and_expires_or_closes():
    gate = EventContextGate(started_at=NOW)
    assert gate.select(False, online(), False, NOW) == 'waiting'
    assert gate.select(False, online(), True, NOW) == 'online'
    assert gate.select(False, online(phase='closed'), True, NOW) == 'waiting'
    assert gate.select(False, online(), True, NOW + timedelta(minutes=40)) == 'waiting'


def test_no_process_and_read_loss_do_not_claim_online():
    gate = EventContextGate(started_at=NOW)
    assert gate.select(None, online(), True, NOW, memory_status='not_running') == 'waiting'
    assert gate.select(None, online(), True, NOW, memory_status='read_error') == 'unknown'


class ControlledMemoryReader:
    def __init__(self, *, active=True, process_id=101, status='active'):
        self._lock = threading.Lock()
        self._reading = {'active': active, 'process_id': process_id, 'status': status}
        self.closed = threading.Event()

    def set(self, **changes):
        with self._lock:
            self._reading.update(changes)

    def sample(self):
        with self._lock:
            return dict(self._reading)

    def close(self):
        self.closed.set()


class BlockingMemoryReader(ControlledMemoryReader):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._block_next = threading.Event()
        self.sample_blocked = threading.Event()
        self.release_sample = threading.Event()

    def block_next_sample(self):
        self.release_sample.clear()
        self.sample_blocked.clear()
        self._block_next.set()

    def sample(self):
        reading = super().sample()
        if self._block_next.is_set():
            self._block_next.clear()
            self.sample_blocked.set()
            assert self.release_sample.wait(5.0)
        return reading

    def close(self):
        self.release_sample.set()
        super().close()


class ControlledTelemetry:
    def __init__(self):
        self._lock = threading.Lock()
        self._packet = {'connected': False}

    def emit(self, timestamp_ms, *, lap_number=0, current_lap_seconds=0.0,
             last_lap_seconds=0.0, current_race_time_seconds=None,
             is_race_on=True, car_ordinal=22, car_class='A',
             performance_index=700, car_display_name='Subaru Impreza 22B'):
        with self._lock:
            self._packet = {
                'connected': True,
                'last_packet_at': datetime.now(timezone.utc).isoformat(),
                'timestamp_ms': timestamp_ms,
                'is_race_on': is_race_on,
                'car_ordinal': car_ordinal,
                'car_class': car_class,
                'performance_index': performance_index,
                'car_display_name': car_display_name,
                'current_lap_seconds': current_lap_seconds,
                'last_lap_seconds': last_lap_seconds,
                'lap_number': lap_number,
                'current_race_time_seconds': (
                    current_lap_seconds if current_race_time_seconds is None
                    else current_race_time_seconds
                ),
            }

    def __call__(self):
        with self._lock:
            return dict(self._packet)


def eventually(predicate, *, timeout=5.0, message='condition was not met'):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except (KeyError, IndexError, TypeError):
            pass
        time.sleep(0.01)
    raise AssertionError(f'{message}; last value: {last!r}')


def write_catalog(root, count=50):
    root.mkdir(parents=True, exist_ok=True)
    entries = [
        {'rank': rank, 'player': f'Driver {rank}', 'player_id': f'id-{rank}',
         'time': f"0:{(53_000 + rank * 100) / 1000:06.3f}"}
        for rank in range(1, count + 1)
    ]
    payload = {
        'mode': 'rivals',
        'discipline': 'dirt',
        'events': [{
            'slug': 'sekibe-scramble',
            'name_en': 'Sekibe Scramble',
            'name_es': 'Circuito de tierra de Sekibe',
            'event_kind': 'circuit',
            'classes': [{
                'class': 'A', 'label': 'A 700',
                'captured_at': '2026-08-11T09:35:27+02:00',
                'entries': entries,
            }],
        }],
    }
    (root / 'dirt-rivals.json').write_text(json.dumps(payload), encoding='utf-8')


def start_runtime(tmp_path, *, profile='driver-a', reader=None, telemetry=None):
    catalog_root = tmp_path / 'catalog'
    write_catalog(catalog_root)
    reader = reader or ControlledMemoryReader()
    telemetry = telemetry or ControlledTelemetry()
    runtime = RivalsRuntime(tmp_path, catalog_root, profile, reader=reader,
                            telemetry_reader=telemetry)
    runtime.start()
    eventually(lambda: runtime.snapshot()['events'])
    return runtime, reader, telemetry


def select_sekibe(runtime):
    runtime.select_event('dirt:sekibe-scramble')
    eventually(lambda: (runtime.snapshot()['current'].get('event') or {}).get('event_id')
               == 'dirt:sekibe-scramble')


def emit_and_wait(runtime, telemetry, timestamp_ms, **values):
    telemetry.emit(timestamp_ms, **values)
    expected = values.get('current_lap_seconds', 0.0)
    return eventually(
        lambda: runtime.snapshot()['current'].get('current_lap_seconds') == expected,
        message=f'telemetry packet {timestamp_ms} was not consumed',
    )


def complete_lap(runtime, telemetry, timestamp_ms, lap_number, seconds):
    before = len(runtime.snapshot()['attempts'])
    telemetry.emit(timestamp_ms, lap_number=lap_number,
                   current_lap_seconds=0.1, last_lap_seconds=seconds,
                   current_race_time_seconds=lap_number * 60.0 + 0.1)
    return eventually(
        lambda: runtime.snapshot()['attempts']
        if len(runtime.snapshot()['attempts']) == before + 1 else None,
        message=f'lap {lap_number} was not persisted',
    )


def stop_runtime(runtime, reader):
    runtime.stop()
    assert reader.closed.wait(5.0)
    eventually(lambda: runtime._thread is not None and not runtime._thread.is_alive(),
               message='runtime worker did not stop')


def test_runtime_replays_three_laps_ignores_manual_edits_and_reopens_automatic_best(tmp_path):
    runtime, reader, telemetry = start_runtime(tmp_path)
    select_sekibe(runtime)
    emit_and_wait(runtime, telemetry, 1, current_lap_seconds=8.0,
                  current_race_time_seconds=8.0)

    complete_lap(runtime, telemetry, 2, 1, 55.646556)
    emit_and_wait(runtime, telemetry, 3, lap_number=1, current_lap_seconds=2.0,
                  current_race_time_seconds=62.0)
    complete_lap(runtime, telemetry, 4, 2, 54.315502)
    emit_and_wait(runtime, telemetry, 5, lap_number=2, current_lap_seconds=2.0,
                  current_race_time_seconds=122.0)
    complete_lap(runtime, telemetry, 6, 3, 54.158180)

    by_time = {attempt['time_ms']: attempt for attempt in runtime.snapshot()['attempts']}
    assert set(by_time) == {55_647, 54_316, 54_158}
    for time_ms, validity in ((55_647, 'invalid'), (54_316, 'clean'), (54_158, 'invalid')):
        runtime._commands.put(('validity', (by_time[time_ms]['attempt_id'], validity)))
    eventually(lambda: runtime._commands.empty())
    assert {a['time_ms']: a['validity'] for a in runtime.snapshot()['attempts']} == {
        55_647: 'clean', 54_316: 'clean', 54_158: 'clean'}
    assert not hasattr(runtime, 'confirm_validity')
    assert eventually(lambda: runtime.snapshot()['bests'])[0]['time_ms'] == 54_158
    stop_runtime(runtime, reader)

    reopened_reader = ControlledMemoryReader(active=False, process_id=None,
                                               status='not_running')
    reopened, reopened_reader, _ = start_runtime(
        tmp_path, reader=reopened_reader, telemetry=ControlledTelemetry())
    assert eventually(lambda: len(
        [attempt for attempt in reopened.snapshot()['attempts'] if attempt['completed']]
    ) == 3)
    assert reopened.snapshot()['bests'][0]['time_ms'] == 54_158
    assert reopened.snapshot()['bests'][0]['validity'] == 'clean'
    stop_runtime(reopened, reopened_reader)


def test_selection_clears_on_context_exit_and_process_restart(tmp_path):
    runtime, reader, telemetry = start_runtime(tmp_path)
    select_sekibe(runtime)
    emit_and_wait(runtime, telemetry, 1, current_lap_seconds=3.0)

    reader.set(active=False, status='inactive')
    eventually(lambda: runtime.snapshot()['active'] is False)
    eventually(lambda: runtime.snapshot()['current'].get('event') is None)

    reader.set(active=True, process_id=101, status='active')
    select_sekibe(runtime)
    emit_and_wait(runtime, telemetry, 2, current_lap_seconds=4.0)
    reader.set(process_id=202)
    eventually(lambda: runtime.snapshot().get('process_id') == 202)
    assert eventually(lambda: runtime.snapshot()['current'].get('event') is None)
    stop_runtime(runtime, reader)


def test_unknown_memory_gap_retains_selection_without_completing_across_gap(tmp_path):
    runtime, reader, telemetry = start_runtime(tmp_path)
    select_sekibe(runtime)
    emit_and_wait(runtime, telemetry, 1, current_lap_seconds=10.0)

    reader.set(active=None, status='read_error')
    eventually(lambda: runtime.snapshot()['active'] is None)
    telemetry.emit(2, lap_number=1, current_lap_seconds=0.1,
                   last_lap_seconds=55.0, current_race_time_seconds=55.0)
    reader.set(active=True, status='active')
    eventually(lambda: runtime.snapshot()['active'] is True)
    eventually(lambda: runtime.snapshot()['current'].get('current_lap_seconds') == 0.1)

    assert runtime.snapshot()['current']['event']['event_id'] == 'dirt:sekibe-scramble'
    assert runtime.snapshot()['attempts'] == []
    complete_lap(runtime, telemetry, 3, 2, 54.0)
    assert len(runtime.snapshot()['attempts']) == 1
    stop_runtime(runtime, reader)


def test_refresh_runs_off_worker_while_telemetry_lap_is_captured(
        monkeypatch, tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def blocked_refresh(self, discipline):
        entered.set()
        assert release.wait(5.0)
        return {'status': 'unchanged', 'discipline': discipline}

    monkeypatch.setattr(SnapshotCatalog, 'refresh', blocked_refresh)
    runtime, reader, telemetry = start_runtime(tmp_path)
    select_sekibe(runtime)
    emit_and_wait(runtime, telemetry, 1, current_lap_seconds=5.0)
    runtime.refresh_snapshot('dirt')
    assert entered.wait(5.0)

    attempts = complete_lap(runtime, telemetry, 2, 1, 53.5)
    assert attempts[0]['time_ms'] == 53_500
    assert runtime.snapshot()['refresh']['status'] == 'refreshing'
    release.set()
    eventually(lambda: runtime.snapshot()['refresh']['status'] == 'unchanged')
    stop_runtime(runtime, reader)


def test_profile_commands_are_isolated_and_new_profile_requires_event_selection(tmp_path):
    runtime, reader, telemetry = start_runtime(tmp_path, profile='driver-a')
    select_sekibe(runtime)
    emit_and_wait(runtime, telemetry, 1, current_lap_seconds=5.0)
    attempts = complete_lap(runtime, telemetry, 2, 1, 54.0)
    attempt_id = attempts[0]['attempt_id']

    runtime.set_profile('driver-b')
    eventually(lambda: runtime.profile_id == 'driver-b' and runtime.snapshot()['attempts'] == [])
    assert runtime.snapshot()['current'].get('event') is None
    runtime._commands.put(('validity', (attempt_id, 'clean')))
    eventually(lambda: runtime.snapshot()['revision'] > 1)
    assert runtime.snapshot()['attempts'] == []

    runtime.set_profile('driver-a')
    eventually(lambda: runtime.profile_id == 'driver-a' and
               len([a for a in runtime.snapshot()['attempts'] if a['completed']]) == 1)
    completed = next(a for a in runtime.snapshot()['attempts'] if a['completed'])
    assert completed['validity'] == 'clean'
    assert runtime.snapshot()['current'].get('event') is None
    stop_runtime(runtime, reader)


def test_stop_persists_an_observed_incomplete_attempt(tmp_path):
    runtime, reader, telemetry = start_runtime(tmp_path)
    select_sekibe(runtime)
    emit_and_wait(runtime, telemetry, 1, current_lap_seconds=12.0)

    stop_runtime(runtime, reader)

    stored = RivalsStore(
        tmp_path / 'data' / 'rivals' / 'personal.sqlite3'
    ).list_attempts('driver-a')
    assert len(stored) == 1
    assert stored[0]['completed'] is False
    assert stored[0]['observed_partial'] is True


def test_snapshot_is_a_copy_that_callers_cannot_mutate(tmp_path):
    runtime, reader, _ = start_runtime(tmp_path)

    caller_copy = runtime.snapshot()
    caller_copy['events'].clear()
    caller_copy['current']['event'] = {'event_id': 'invented'}

    worker_copy = runtime.snapshot()
    assert worker_copy['events'][0]['event_id'] == 'dirt:sekibe-scramble'
    assert worker_copy['current'].get('event') is None
    stop_runtime(runtime, reader)


def test_current_board_and_published_result_do_not_require_a_local_best(tmp_path):
    runtime, reader, telemetry = start_runtime(tmp_path, profile='Driver 25')
    try:
        select_sekibe(runtime)
        emit_and_wait(runtime, telemetry, 1, current_lap_seconds=2., car_class='A')
        state = eventually(lambda: runtime.snapshot() if runtime.snapshot().get('snapshot') else None)
        assert state['best'] is None
        assert state['snapshot']['player_result']['rank'] == 25
        assert state['snapshot']['references']['top_50_ms'] == 58000
        emit_and_wait(runtime, telemetry, 2, current_lap_seconds=2.1, car_class='S1')
        eventually(lambda: runtime.snapshot().get('snapshot') is None)
    finally:
        stop_runtime(runtime, reader)


def test_profile_switch_and_stale_validity_command_are_atomic_and_isolated(tmp_path):
    reader = BlockingMemoryReader()
    runtime, reader, telemetry = start_runtime(
        tmp_path, profile='driver-a', reader=reader)
    select_sekibe(runtime)
    emit_and_wait(runtime, telemetry, 1, current_lap_seconds=5.0)
    attempts = complete_lap(runtime, telemetry, 2, 1, 54.0)
    attempt_id = attempts[0]['attempt_id']

    reader.block_next_sample()
    assert reader.sample_blocked.wait(5.0)
    runtime.set_profile('driver-b')
    runtime._commands.put(('validity', (attempt_id, 'clean')))
    reader.release_sample.set()
    eventually(lambda: runtime.profile_id == 'driver-b')

    old_attempt = eventually(lambda: next(
        (attempt for attempt in RivalsStore(
            tmp_path / 'data' / 'rivals' / 'personal.sqlite3'
        ).list_attempts('driver-a') if attempt['attempt_id'] == attempt_id),
        None,
    ))
    assert old_attempt['validity'] == 'clean'
    assert [entry['validity'] for entry in old_attempt['validity_history']] == ['clean']
    stop_runtime(runtime, reader)


def test_stop_ignores_queued_validity_edits_before_joining_worker(tmp_path):
    reader = BlockingMemoryReader()
    runtime, reader, telemetry = start_runtime(
        tmp_path, profile='driver-a', reader=reader)
    select_sekibe(runtime)
    emit_and_wait(runtime, telemetry, 1, current_lap_seconds=5.0)
    attempts = complete_lap(runtime, telemetry, 2, 1, 54.0)
    attempt_id = attempts[0]['attempt_id']

    reader.block_next_sample()
    assert reader.sample_blocked.wait(5.0)
    runtime._commands.put(('validity', (attempt_id, 'invalid')))
    stop_runtime(runtime, reader)

    stored = next(
        attempt for attempt in RivalsStore(
            tmp_path / 'data' / 'rivals' / 'personal.sqlite3'
        ).list_attempts('driver-a')
        if attempt['attempt_id'] == attempt_id
    )
    assert stored['validity'] == 'clean'
    assert [entry['validity'] for entry in stored['validity_history']] == [
        'clean',
    ]


def memory_event(route=69, name='Circuito de tierra de Sekibe', instance='event-1'):
    return {'route_id': route, 'name': name, 'instance_id': instance}


@pytest.mark.parametrize('name', ['Epreuve mixte de Sekibe', 'Кольцевой кросс Сэкибэ'])
def test_translated_memory_event_automatically_persists_canonical_lap(tmp_path, name):
    reader = ControlledMemoryReader()
    reader.set(event=memory_event(route=999, name=name))
    runtime, reader, telemetry = start_runtime(tmp_path, reader=reader)
    try:
        eventually(lambda: (runtime.snapshot()['current'].get('event') or {}).get('event_id')
                   == 'dirt:sekibe-scramble')
        assert runtime.snapshot()['current']['event_source'] == 'memory'
        emit_and_wait(runtime, telemetry, 1, current_lap_seconds=5.0)
        rows = complete_lap(runtime, telemetry, 2, 1, 54.0)
        assert rows[0]['event_id'] == 'dirt:sekibe-scramble'
        assert rows[0]['event_name'] == 'Circuito de tierra de Sekibe'
        assert rows[0]['time_ms'] == 54_000
    finally:
        stop_runtime(runtime, reader)
    stored = RivalsStore(tmp_path / 'data' / 'rivals' / 'personal.sqlite3').list_attempts('driver-a')
    assert any(row['completed'] and row['event_id'] == 'dirt:sekibe-scramble'
               and row['time_ms'] == 54_000 for row in stored)


def test_event_resolution_uses_validated_ids_and_unique_exact_catalog_names(tmp_path):
    from forza_tracker.rivals_runtime import resolve_memory_event
    write_catalog(tmp_path)
    events = SnapshotCatalog(tmp_path).events()
    sekibe = events[0]
    assert resolve_memory_event(memory_event(), events) == sekibe
    assert resolve_memory_event(memory_event(name='セキベ'), events) == sekibe
    assert resolve_memory_event(memory_event(route=999, name='  SEKIBE  Scramble '), events) == sekibe
    assert resolve_memory_event(memory_event(route=999, name='Unknown track'), events) is None
    assert resolve_memory_event(memory_event(), events, build='other') is None
    hirosaki = {**sekibe, 'event_id': 'dirt:hirosaki-scramble', 'event_name': 'Hirosaki Scramble',
                'name_en': 'Hirosaki Scramble', 'name_es': 'Circuito de tierra de Hirosaki'}
    assert resolve_memory_event(memory_event(name=hirosaki['name_es']), [sekibe, hirosaki]) is None
    assert resolve_memory_event(memory_event(route=999), [sekibe, {**sekibe, 'event_id': 'duplicate'}]) is None
    for malformed in (None, {}, {'route_id': True, 'name': 'Sekibe Scramble', 'instance_id': 'x'}):
        assert resolve_memory_event(malformed, events) is None


def test_runtime_automatically_selects_and_clears_lost_memory_event(tmp_path):
    reader = ControlledMemoryReader()
    reader.set(event=memory_event())
    runtime, reader, telemetry = start_runtime(tmp_path, reader=reader)
    try:
        eventually(lambda: (runtime.snapshot()['current'].get('event') or {}).get('event_id') == 'dirt:sekibe-scramble')
        emit_and_wait(runtime, telemetry, 1, current_lap_seconds=5.0)
        assert runtime.snapshot()['current']['event_source'] == 'memory'
        reader.set(event=None, event_status='read_error')
        eventually(lambda: runtime.snapshot()['current'].get('event') is None)
        reader.set(event=memory_event(instance='event-2'))
        eventually(lambda: runtime.snapshot()['current'].get('event_source') == 'memory')
        emit_and_wait(runtime, telemetry, 2, lap_number=1, current_lap_seconds=0.1,
                      current_race_time_seconds=55.1, last_lap_seconds=55.0)
        assert not any(a['completed'] for a in runtime.snapshot()['attempts'])
    finally:
        stop_runtime(runtime, reader)


def test_unresolved_event_manual_choice_expires_when_raw_identity_changes(tmp_path):
    reader = ControlledMemoryReader()
    reader.set(event=memory_event(route=901, name='Unknown track'))
    runtime, reader, telemetry = start_runtime(tmp_path, reader=reader)
    try:
        assert runtime.snapshot()['current'].get('event') is None
        assert runtime.snapshot()['current']['raw_event']['name'] == 'Unknown track'
        select_sekibe(runtime)
        emit_and_wait(runtime, telemetry, 1, current_lap_seconds=5.0)
        assert runtime.snapshot()['current']['event_source'] == 'manual'
        reader.set(event=memory_event(route=902, name='Another track', instance='event-2'))
        eventually(lambda: runtime.snapshot()['current'].get('event') is None)
        assert runtime.snapshot()['current']['raw_event']['name'] == 'Another track'
    finally:
        stop_runtime(runtime, reader)


def test_memory_event_instance_replacement_cannot_finish_previous_attempt(tmp_path):
    reader = ControlledMemoryReader()
    reader.set(event=memory_event())
    runtime, reader, telemetry = start_runtime(tmp_path, reader=reader)
    try:
        emit_and_wait(runtime, telemetry, 1, current_lap_seconds=5.0)
        old_session = runtime.snapshot()['current']['session_id']
        reader.set(event=memory_event(instance='replacement'))
        eventually(lambda: runtime.snapshot()['current'].get('session_id') != old_session)
        assert runtime.snapshot()['current']['event_source'] == 'memory'
        assert runtime.snapshot()['attempts'][0]['completed'] is False
        assert runtime.snapshot()['attempts'][0]['event_source'] == 'memory'
    finally:
        stop_runtime(runtime, reader)


def test_profile_switch_reselects_current_automatic_event_with_no_old_attempts(tmp_path):
    reader = ControlledMemoryReader()
    reader.set(event=memory_event())
    runtime, reader, telemetry = start_runtime(tmp_path, reader=reader)
    try:
        emit_and_wait(runtime, telemetry, 1, current_lap_seconds=5.0)
        runtime.set_profile('new-driver')
        eventually(lambda: runtime.profile_id == 'new-driver')
        telemetry.emit(2, current_lap_seconds=5.1)
        eventually(lambda: runtime.profile_id == 'new-driver' and runtime.snapshot()['current'].get('profile_id') == 'new-driver')
        assert runtime.snapshot()['current']['event_source'] == 'memory'
        assert runtime.snapshot()['attempts'] == []
    finally:
        stop_runtime(runtime, reader)


def test_runtime_persists_sampled_dirty_without_manual_confirmation(tmp_path):
    reader = ControlledMemoryReader()
    reader.set(event=memory_event(), validity={'dirty': True, 'race_clock': 5.0,
               'lap_start_clock': 0.0, 'record_id': 'r', 'car_id': 'c'})
    runtime, reader, telemetry = start_runtime(tmp_path, reader=reader)
    try:
        emit_and_wait(runtime, telemetry, 1, current_lap_seconds=5.0)
        eventually(lambda: runtime.snapshot()['current'].get('validity') == 'invalid')
        reader.set(validity={'dirty': False, 'race_clock': 60.1,
                   'lap_start_clock': 60.0, 'record_id': 'r', 'car_id': 'c'})
        rows = complete_lap(runtime, telemetry, 2, 1, 60.0)
        assert rows[0]['validity'] == 'invalid'
        assert rows[0]['validity_source'] == 'memory_sampled'
        assert rows[0]['event_source'] == 'memory'
        assert rows[0]['validity_history'][0]['validity_source'] == 'memory_sampled'
        assert runtime.snapshot()['bests'] == []
        runtime._commands.put(('validity', (rows[0]['attempt_id'], 'clean')))
        eventually(lambda: runtime._commands.empty())
        stored = next(row for row in RivalsStore(
            tmp_path / 'data' / 'rivals' / 'personal.sqlite3'
        ).list_attempts('driver-a') if row['attempt_id'] == rows[0]['attempt_id'])
        assert stored['validity'] == 'invalid'
        assert len(stored['validity_history']) == 1
        assert runtime.snapshot()['bests'] == []
    finally:
        stop_runtime(runtime, reader)


def test_sector_frame_matching_accepts_only_coherent_outgoing_udp_at_finish():
    from forza_tracker.rivals_runtime import sector_memory_observation
    value = {'dirty': False, 'race_clock': 60.01, 'lap_start_clock': 60.0,
             'record_id': 'r', 'car_id': 'c', 'checkpoints': {'lap_index': 1,
             'crossing_race_clock': 60.0, 'crossing_lap_clock': 60.0}}
    packet = {'current_race_time_seconds': 59.99, 'current_lap_seconds': 59.99, 'lap_number': 0}
    assert sector_memory_observation(value, packet) is not None
    assert sector_memory_observation(value, {**packet, 'current_race_time_seconds': 55.0}) is None
    assert sector_memory_observation(value, {**packet, 'lap_number': 5}) is None
    value['checkpoints']['crossing_race_clock'] = 59.0
    assert sector_memory_observation(value, packet) is None
