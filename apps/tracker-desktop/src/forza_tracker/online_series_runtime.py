"""Memory series observations and append-only local evidence, separate from race results."""
import copy
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import threading
from uuid import uuid4

from .online_series_memory import OnlineSeriesMemoryReader

log = logging.getLogger(__name__)


def panel_text(state):
    series = state.get('current') or state.get('last_series')
    if not series:
        return 'Online series\nWaiting for a series'
    heading = 'Observed series' if state.get('current') else 'Last observed series'
    discipline = {'road': 'Road', 'dirt': 'Dirt', 'cross-country': 'Cross Country',
                  'street': 'Street', 'drag': 'Drag', 'touge': 'Touge'}.get(series.get('discipline'))
    lines = [heading + ' · ' + (discipline or 'Discipline pending') + ' · Class pending']
    for i, track in enumerate(series['tracks'], 1):
        name = track['name'] or f"Unknown track (ID {track['track_id']})"
        lines.append(f'{i}. {name}')
    return '\n'.join(lines)


class OnlineSeriesRuntime:
    def __init__(self, data_root, *, reader=None):
        self.root = Path(data_root) / 'data' / 'online-series'
        self.log_path = self.root / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid4().hex[:8] + '.jsonl')
        self.reader = reader
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.last_key = None
        self.state = dict(status='waiting', current=None, last_series=None, class_candidates=[])

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.state)

    def observe(self, reading):
        current = copy.deepcopy(reading.get('series')) if reading.get('status') == 'observed' else None
        key = None
        if current:
            current.pop('raw', None)
            # Until a native restriction-to-series reference is established, never
            # promote a class candidate or use this observation to submit a race.
            current['class_name'] = None
            current['series_identity_verified'] = False
            key = (reading.get('process_id'), tuple(current['track_ids']), current['completed_event_count'])
        if (current and key != self.last_key) or reading.get('class_candidates'):
            now = datetime.now(timezone.utc).isoformat()
            self.root.mkdir(parents=True, exist_ok=True)
            with self.log_path.open('a', encoding='utf-8') as output:
                output.write(json.dumps(dict(observed_at=now, source='process_memory',
                    process_id=reading.get('process_id'), series=current,
                    class_candidates=reading.get('class_candidates', []),
                    upload_enabled=False), ensure_ascii=False, separators=(',', ':')) + '\n')
            if current:
                self.last_key = key
        if current:
            with self.lock:
                self.state['last_series'] = copy.deepcopy(current)
        with self.lock:
            self.state.update(status=reading.get('status', 'unavailable'), current=current,
                              class_candidates=copy.deepcopy(reading.get('class_candidates', [])))

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, name='online-series-memory', daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.reader is not None:
            self.reader.close()
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)

    def _run(self):
        try:
            self.reader = self.reader or OnlineSeriesMemoryReader()
            while not self.stop_event.is_set():
                self.observe(self.reader.sample())
                self.stop_event.wait(.25)
        except Exception:
            log.exception('Online series observation stopped')
            self.observe({'status': 'unavailable'})
        finally:
            if self.reader is not None:
                self.reader.release()
