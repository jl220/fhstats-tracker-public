"""Memory based circuit observation, independent of official Rivals."""
from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import datetime, timezone
from uuid import UUID, uuid4

CLASS_BOUNDS = {'D': (100, 400), 'C': (401, 500), 'B': (501, 600),
                'A': (601, 700), 'S1': (701, 800), 'S2': (801, 900), 'R': (901, 998)}


def evidence_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


class CleanLapCapture:
    """Accumulate invalidation before the game resets its flags at the line."""

    def __init__(self, profile_id: str, *, max_gap=.75):
        self.profile_id, self.max_gap = profile_id, max_gap
        self.reset()

    def reset(self):
        self._identity = self._previous = None
        self._complete_start = False
        self._dirty = False
        self._evidence = []
        self._clock_reset_at = None
        self._clock_reset = None
        self.current = {'status': 'waiting', 'dirty': None}

    def _validated(self, r):
        if not isinstance(r, dict) or r.get('status') != 'available' or r.get('identity_verified') is not True:
            return None
        if r.get('participant_count') != 1 or r.get('profile_id') != self.profile_id:
            return None
        try:
            guid = str(UUID(r['blueprint_guid']))
            code = r['blueprint_code']
            if code is not None and (not isinstance(code, str) or len(code) != 9 or not code.isascii() or not code.isdigit()):
                return None
            for field in ('race_clock', 'lap_start_clock', 'last_lap_seconds'):
                value = r[field]
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    return None
            if r['race_clock'] < r['lap_start_clock'] or r['last_lap_seconds'] > 86400:
                return None
            if type(r['completed_laps']) is not int or not 0 <= r['completed_laps'] <= 10000:
                return None
            low, high = CLASS_BOUNDS[r['car_class']]
            if type(r['performance_index']) is not int or not low <= r['performance_index'] <= high:
                return None
            if type(r['car_ordinal']) is not int or r['car_ordinal'] <= 0:
                return None
            flags = bytes.fromhex(r['flags'])
            if len(flags) != 7 or any(f not in (0, 1) for f in flags):
                return None
            identity = (self.profile_id, guid, code, r['car_ordinal'], r['car_class'],
                        r['performance_index'], r['process_id'], r['record_id'], r['car_id'])
            if not r['record_id'] or not r['car_id'] or type(r['process_id']) is not int:
                return None
            return identity, any(flags[i] for i in (0, 3, 4, 5, 6))
        except (KeyError, ValueError, TypeError):
            return None

    def update(self, reading, *, observed_at=None):
        now = time.monotonic() if observed_at is None else observed_at
        valid = self._validated(reading)
        if valid is None:
            self.reset()
            self.current['status'] = (reading or {}).get('status', 'unavailable')
            return []
        identity, dirty = valid
        r = reading
        old = self._previous
        contiguous = bool(old and identity == self._identity and 0 <= now-old['at'] <= self.max_gap)
        results = []
        if not contiguous:
            self.reset()
            self._complete_start = r['race_clock']-r['lap_start_clock'] <= .15
        else:
            boundary = r['completed_laps'] == old['completed_laps'] + 1
            full_zero = (r['race_clock'] == r['lap_start_clock'] == r['last_lap_seconds'] == 0
                         and r['completed_laps'] == 0 and r['flags'] == '00000000000000')
            reset_baseline = full_zero and (self._clock_reset_at is not None or old['race_clock'] > 0)
            if reset_baseline:
                # A restart may reuse the same participant and record. Require
                # a sustained complete timing reset, never just a clock drop.
                if self._clock_reset_at is None:
                    self._clock_reset_at = now
                    self._clock_reset = dict(previous_clock=old['race_clock'],
                                             first_observed_at=now, observed_seconds=0.)
                self._clock_reset['observed_seconds'] = now-self._clock_reset_at
                self._complete_start = now-self._clock_reset_at >= 1.
                self._dirty = not self._complete_start
                self._evidence = []
            elif boundary:
                duration = r['lap_start_clock'] - old['lap_start_clock']
                measured = r['last_lap_seconds']
                coherent = (r['race_clock'] >= old['race_clock'] and duration > 0
                            and .001 <= measured <= 86400 and abs(duration-measured) <= .002)
                if self._complete_start and not self._dirty and coherent:
                    evidence = dict(schema=1, source='process_memory', identity=list(identity),
                                    samples=self._evidence, crossing=self._row(r, now),
                                    measured_seconds=measured, clean_observed=True,
                                    active_blueprint_context=r.get('identity_evidence'),
                                    gamertag=r.get('gamertag'),metadata_source=r.get('metadata_source'),
                                    car_metadata_evidence=r.get('car_metadata_evidence'))
                    if self._clock_reset is not None:
                        evidence['clock_reset'] = dict(self._clock_reset)
                    results.append(dict(attempt_id=str(uuid4()), profile_id=self.profile_id,
                                        blueprint_guid=identity[1], blueprint_code=identity[2],
                                        car_ordinal=r['car_ordinal'], car_class=r['car_class'],
                                        performance_index=r['performance_index'],
                                        time_ms=int(math.floor(measured*1000+.5)),
                                        recorded_at=datetime.now(timezone.utc).isoformat(),
                                        evidence_sha256=hashlib.sha256(evidence_bytes(evidence)).hexdigest(),
                                        evidence=evidence, identity_source=r.get('identity_source', 'memory')))
                self._complete_start = coherent
                self._dirty = False
                self._evidence = []
                self._clock_reset = None
            elif (r['completed_laps'] != old['completed_laps']
                  or r['lap_start_clock'] != old['lap_start_clock']):
                # Crossing the start line before lap one is a new timing baseline.
                self._complete_start = bool(r['completed_laps'] == old['completed_laps'] == 0
                                            and old['lap_start_clock'] == 0
                                            and 0 <= r['race_clock']-r['lap_start_clock'] <= .15)
                self._dirty = False
                self._evidence = []
            elif r['race_clock'] < old['race_clock'] - .000001:
                # Rewinding can precede the game's dirty flag by several seconds.
                self._dirty = True
            if not full_zero:
                self._clock_reset_at = None
        self._identity = identity
        self._dirty = self._dirty or dirty
        entry = self._row(r, now)
        if not self._evidence or entry[1:] != self._evidence[-1][1:]:
            if len(self._evidence) >= 40000:
                self._complete_start = False
            else:
                self._evidence.append(entry)
        self._previous = dict(at=now, race_clock=r['race_clock'], lap_start_clock=r['lap_start_clock'],
                              completed_laps=r['completed_laps'])
        self.current = dict(status='observing', dirty=self._dirty, complete_start=self._complete_start,
                            lap=r['completed_laps']+1, seconds=r['race_clock']-r['lap_start_clock'],
                            blueprint_code=r['blueprint_code'], car_ordinal=r['car_ordinal'], car_class=r['car_class'])
        return results

    @staticmethod
    def _row(r, at):
        return [at, r['race_clock'], r['lap_start_clock'], r['completed_laps'], r['flags']]
