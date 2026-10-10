from forza_tracker.rivals_sectors import SectorTracker


def feed(tracker, value, identity, *, paused=False):
    # Simulate regular observations between game checkpoint notifications.
    import copy
    previous = getattr(tracker, '_fixture_previous', None)
    if previous and value and previous['race_clock'] < value['race_clock']:
        clock = previous['race_clock'] + .1
        while clock < value['race_clock'] - .001:
            intermediate = copy.deepcopy(previous)
            intermediate['race_clock'] = clock
            tracker.update(intermediate, identity, paused=paused)
            clock += .1
    tracker.update(value, identity, paused=paused)
    tracker._fixture_previous = copy.deepcopy(value)


IDENTITY = ('driver', 'dirt:sekibe-scramble', 'instance', 1, 1150, 'B', 600)


def sample(index, lap, start, crossing, race=None, *, dirty=False):
    return {'record_id': 'r', 'car_id': 'c', 'dirty': dirty,
            'race_clock': crossing if race is None else race, 'lap_start_clock': start,
            'checkpoints': {'crossing_index': index, 'lap_index': lap,
                            'crossing_race_clock': crossing,
                            'crossing_lap_clock': crossing - start,
                            'rewind_active': False, 'rewind_affected': False}}


def finish(index, lap, old_start, crossing, *, dirty=False):
    result = sample(index, lap, crossing, crossing, dirty=dirty)
    result['checkpoints']['crossing_lap_clock'] = crossing - old_start
    return result


def arm(tracker, *, count=12):
    feed(tracker, sample(100, 4, 40, 50), IDENTITY)
    feed(tracker, finish(101, 5, 40, 60), IDENTITY)


def lap(tracker, count=12, step=1.0, *, first_index=101, lap_index=5, start=60):
    for i in range(1, count):
        feed(tracker, sample(first_index+i, lap_index, start, start+i*step), IDENTITY)
    feed(tracker, finish(first_index+count, lap_index+1, start, start+count*step), IDENTITY)


def test_learns_dynamic_count_from_clean_lap_and_compares_three_sectors():
    tracker = SectorTracker()
    arm(tracker)
    assert tracker.snapshot()['status'] == 'learning'
    lap(tracker)
    state = tracker.snapshot()
    assert state['checkpoint_count'] == 12
    assert state['boundaries'] == [4, 8, 12]
    assert [s['reference_ms'] for s in state['sectors']] == [4000, 4000, 4000]
    for i in range(1, 5):
        feed(tracker, sample(113+i, 6, 72, 72+i*.9), IDENTITY)
    first = tracker.snapshot()['sectors'][0]
    assert first['duration_ms'] == 3600
    assert first['delta_ms'] == -400
    assert first['state'] == 'completed'
    for i in range(5, 9):
        feed(tracker, sample(113+i, 6, 72, 75.6+(i-4)*1.1), IDENTITY)
    assert tracker.snapshot()['sectors'][1]['delta_ms'] == 400


def test_layout_is_learned_for_other_counts_without_modulo_or_sekibe_assumption():
    tracker = SectorTracker()
    arm(tracker)
    lap(tracker, count=10)
    assert tracker.snapshot()['boundaries'] == [4, 7, 10]
    assert tracker.snapshot()['checkpoint_count'] == 10


def test_dirty_lap_stops_clock_and_splits_until_next_clean_epoch():
    tracker = SectorTracker()
    arm(tracker)
    lap(tracker)
    feed(tracker, sample(114, 6, 72, 73, dirty=True), IDENTITY)
    state = tracker.snapshot()
    assert state['status'] == 'invalid'
    assert all(s['elapsed_ms'] is None and s['delta_ms'] is None for s in state['sectors'])
    feed(tracker, sample(115, 6, 72, 74), IDENTITY)
    assert tracker.snapshot()['status'] == 'invalid'
    # While disabled, only observation metadata is kept to recognize a finish.
    feed(tracker, sample(124, 6, 72, 83, dirty=True), IDENTITY)
    feed(tracker, finish(125, 7, 72, 84), IDENTITY)
    assert tracker.snapshot()['status'] == 'tracking'
    assert [s['reference_ms'] for s in tracker.snapshot()['sectors']] == [4000]*3


def test_gap_or_rewind_discards_partial_but_does_not_replace_last_clean_reference():
    tracker = SectorTracker()
    arm(tracker)
    lap(tracker)
    feed(tracker, sample(115, 6, 72, 74), IDENTITY)  # skipped114
    assert tracker.snapshot()['status'] == 'waiting_finish'
    assert tracker.snapshot()['last_split'] is None
    feed(tracker, sample(124, 6, 72, 83), IDENTITY)
    feed(tracker, finish(125, 7, 72, 84), IDENTITY)
    value = sample(126, 7, 84, 85)
    value['checkpoints']['rewind_active'] = True
    feed(tracker, value, IDENTITY)
    assert tracker.snapshot()['status'] == 'invalid'
    assert [s['reference_ms'] for s in tracker.snapshot()['sectors']] == [4000]*3


def test_unknown_or_car_change_cannot_keep_stale_live_deltas():
    tracker = SectorTracker()
    arm(tracker)
    lap(tracker)
    feed(tracker, None, IDENTITY)
    assert tracker.snapshot()['status'] == 'waiting_finish'
    feed(tracker, sample(113, 6, 72, 72, race=72.1), (*IDENTITY[:-2], 'A', 700))
    assert all(s['reference_ms'] is None for s in tracker.snapshot()['sectors'])


def test_finishing_timestamp_belongs_to_outgoing_lap_and_repeat_is_not_a_split():
    tracker = SectorTracker()
    arm(tracker)
    lap(tracker)
    end = finish(113, 6, 60, 72)
    feed(tracker, end, IDENTITY)
    assert tracker.snapshot()['checkpoint_progress'] == 0
    assert tracker.snapshot()['sectors'][0]['elapsed_ms'] == 0


def test_invalid_carryover_waits_another_finish_and_pause_does_not_add_crossings():
    tracker = SectorTracker()
    arm(tracker)
    feed(tracker, sample(112, 5, 60, 71, dirty=True), IDENTITY)
    end = finish(113, 6, 60, 72, dirty=True)
    feed(tracker, end, IDENTITY)
    feed(tracker, end, IDENTITY, paused=True)
    assert tracker.snapshot()['status'] == 'invalid'
    assert tracker.snapshot()['checkpoint_progress'] is None


def test_changed_layout_is_relearned_without_reusing_wrong_reference():
    tracker = SectorTracker()
    arm(tracker)
    lap(tracker, count=10)
    lap(tracker, count=12, first_index=111, lap_index=6, start=70)
    assert tracker.snapshot()['checkpoint_count'] == 12
    assert tracker.snapshot()['boundaries'] == [4, 8, 12]


def test_poll_gap_and_bad_clock_do_not_produce_a_clean_reference():
    tracker = SectorTracker()
    arm(tracker)
    tracker.update(sample(102, 5, 60, 61), IDENTITY)  # direct1s sampling gap
    assert tracker.snapshot()['status'] == 'waiting_finish'
    bad = sample(103, 5, 60, 62)
    bad['race_clock'] = float('nan')
    tracker.update(bad, IDENTITY)
    assert all(s['reference_ms'] is None for s in tracker.snapshot()['sectors'])
