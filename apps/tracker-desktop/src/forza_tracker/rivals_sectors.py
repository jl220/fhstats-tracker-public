"""Measured circuit sectors, calibrated from sequential accepted crossings.

The game supplies crossing clocks. Our three sector cuts are app-defined;
no position interpolation or global checkpoint-number modulo is assumed.
"""
from __future__ import annotations

import copy
import math


def _close(a, b):
    return abs(a - b) <= .001


class SectorTracker:
    def __init__(self):
        self._counts = {}
        self._identity = None
        self._reset()

    def _reset(self):
        self._previous = None
        self._reference = None
        self._count = None
        self._dirty_epoch = None
        self._disarm()

    def _disarm(self, status='waiting_finish'):
        self._anchor = None
        self._crossings = []
        self._completed = []
        self._last_split = None
        self._clock = None
        self._status = status

    @staticmethod
    def _observation(value):
        if not isinstance(value, dict) or type(value.get('dirty')) is not bool:
            return None
        cp = value.get('checkpoints')
        if not isinstance(cp, dict):
            return None
        for key in ('race_clock', 'lap_start_clock'):
            if type(value.get(key)) not in (float, int) or not math.isfinite(value[key]) or value[key] < 0:
                return None
        for key in ('crossing_race_clock', 'crossing_lap_clock'):
            if type(cp.get(key)) not in (float, int) or not math.isfinite(cp[key]) or cp[key] < 0:
                return None
        if (type(cp.get('crossing_index')) is not int or cp['crossing_index'] < -1
                or type(cp.get('lap_index')) is not int or cp['lap_index'] < 0
                or any(type(cp.get(k)) is not bool for k in ('rewind_active', 'rewind_affected'))
                or any(not isinstance(value.get(k), str) or not value[k] for k in ('record_id', 'car_id'))
                or value['race_clock'] < max(value['lap_start_clock'], cp['crossing_race_clock'])):
            return None
        return {**cp, 'race_clock': value['race_clock'], 'lap_start_clock': value['lap_start_clock'],
                'dirty': value['dirty'], 'record_id': value['record_id'], 'car_id': value['car_id']}

    def update(self, value, identity, *, paused=False):
        if identity is None:
            self._identity = None
            self._reset()
            return
        sample = self._observation(value)
        owner = (*identity, sample['record_id'], sample['car_id']) if identity and sample else None
        if owner is not None and owner != self._identity:
            self._identity = owner
            self._reset()
            self._count = self._counts.get(identity[1])
        if sample is None or identity is None:
            self._previous = None
            self._disarm()
            return
        previous, self._previous = self._previous, sample
        epoch = sample['lap_start_clock']
        if sample['dirty'] or sample['rewind_active'] or sample['rewind_affected']:
            self._dirty_epoch = epoch
        if self._dirty_epoch == epoch:
            self._disarm('invalid')
            return
        if previous is None:
            return
        delta = sample['crossing_index'] - previous['crossing_index']
        clock_delta = sample['race_clock'] - previous['race_clock']
        if (clock_delta < 0 or sample['lap_index'] < previous['lap_index'] or delta < 0):
            # A restart can recycle the same pointers. Old reference is unsafe.
            self._reference = None
            self._disarm()
            return
        if clock_delta > .25 + 1e-9:
            self._disarm()
            return
        if delta == 0:
            if (sample['lap_index'] != previous['lap_index'] or epoch != previous['lap_start_clock']
                    or sample['crossing_race_clock'] != previous['crossing_race_clock']
                    or sample['crossing_lap_clock'] != previous['crossing_lap_clock']):
                self._disarm()
            elif self._anchor is not None:
                self._clock = sample['race_clock']
                self._status = 'paused' if paused else 'tracking' if self._count else 'learning'
            return
        if (delta != 1 or sample['crossing_index'] < 0
                or sample['crossing_race_clock'] <= previous['crossing_race_clock']):
            self._disarm()
            return
        is_finish = (sample['lap_index'] == previous['lap_index'] + 1
                     and epoch > previous['lap_start_clock']
                     and _close(epoch, sample['crossing_race_clock'])
                     and _close(sample['crossing_lap_clock'], epoch - previous['lap_start_clock']))
        if self._anchor is None:
            if is_finish:
                self._arm(sample)
            return
        relative = sample['crossing_index'] - self._anchor['crossing_index']
        elapsed = sample['crossing_race_clock'] - self._anchor['lap_start_clock']
        if (relative != len(self._crossings) + 1 or not 1 <= relative <= 1000
                or not _close(elapsed, sample['crossing_lap_clock'])
                or (not is_finish and (sample['lap_index'] != self._anchor['lap_index']
                                       or epoch != self._anchor['lap_start_clock']))):
            self._disarm()
            if is_finish:
                self._arm(sample)
            return
        self._crossings.append(sample['crossing_race_clock'])
        self._clock = sample['race_clock']
        if self._count and relative in self._boundaries():
            boundary = self._boundaries().index(relative)
            start = self._anchor['lap_start_clock'] if boundary == 0 else self._crossings[self._boundaries()[boundary-1]-1]
            duration = round((sample['crossing_race_clock'] - start) * 1000)
            reference = self._reference[boundary] if self._reference else None
            split = {'index': boundary + 1, 'duration_ms': duration,
                     'delta_ms': duration - reference if reference is not None else None}
            self._completed.append(split)
            self._last_split = split
        if is_finish:
            count = len(self._crossings)
            if self._count is not None and count != self._count:
                self._last_split = None
                self._reference = None
            self._count = count
            self._counts[identity[1]] = count
            anchor = self._anchor['lap_start_clock']
            reference = []
            for boundary in self._boundaries():
                end = self._crossings[boundary-1]
                reference.append(round((end-anchor)*1000))
                anchor = end
            self._reference = reference
            self._arm(sample)
        elif self._count and relative >= self._count:
            # A different layout can share a catalog name. Stop comparisons,
            # retain sequential evidence and calibrate its actual finish.
            self._counts.pop(identity[1], None)
            self._count = None
            self._reference = None
            self._completed = []
            self._last_split = None
            self._status = 'learning'

    def _arm(self, sample):
        self._anchor = sample
        self._crossings = []
        self._completed = []
        self._clock = sample['race_clock']
        self._dirty_epoch = None
        self._status = 'tracking' if self._count else 'learning'

    def _boundaries(self):
        count = self._count or 0
        sectors = min(3, count)
        return [(count*i + sectors-1)//sectors for i in range(1, sectors+1)]

    def snapshot(self):
        boundaries = self._boundaries()
        rows = []
        for index in range(len(boundaries) or 3):
            completed = self._completed[index] if index < len(self._completed) else None
            running = self._anchor is not None and self._count is not None and index == len(self._completed)
            start = None
            if running:
                start = (self._anchor['lap_start_clock'] if index == 0
                         else self._crossings[boundaries[index-1]-1])
            rows.append({'index': index+1,
                         'duration_ms': completed['duration_ms'] if completed else None,
                         'elapsed_ms': round(max(0, self._clock-start)*1000) if running else None,
                         'reference_ms': self._reference[index] if self._reference else None,
                         'delta_ms': completed['delta_ms'] if completed else None,
                         'state': 'completed' if completed else 'running' if running else 'waiting'})
        return copy.deepcopy({'status': self._status, 'checkpoint_count': self._count,
                              'boundaries': boundaries, 'sectors': rows,
                              'checkpoint_progress': len(self._crossings) if self._anchor else None,
                              'last_split': self._last_split})
