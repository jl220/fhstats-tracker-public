import json

from forza_tracker.online_series_runtime import OnlineSeriesRuntime, panel_text
from forza_tracker.online_series import resolve_series


def reading(ids=(91, 82, 88)):
    return dict(status='observed', process_id=123,
                series=dict(status='candidate', event_count=3, completed_event_count=0,
                            track_ids=list(ids), class_name=None, series_identity_verified=False,
                            **resolve_series(ids)), class_candidates=[])


def test_observation_saves_english_names_once_and_survives_transient_empty_state(tmp_path):
    runtime = OnlineSeriesRuntime(tmp_path, reader=object())
    runtime.observe(reading())
    runtime.observe(reading())
    state = runtime.snapshot()
    assert state['current']['track_names'][0] == 'Tateyama Alpine Cross Country'
    runtime.observe({'status': 'waiting_for_series'})
    state = runtime.snapshot()
    assert state['current'] is None
    assert state['last_series']['track_ids'] == [91, 82, 88]
    assert panel_text(state).startswith('Last observed series')
    lines = runtime.log_path.read_text(encoding='utf-8').splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])['series']['track_names'][1] == 'Snow Forest Cross Country Circuit'


def test_changed_series_is_appended_and_class_candidates_are_never_promoted(tmp_path):
    runtime = OnlineSeriesRuntime(tmp_path, reader=object())
    runtime.observe(reading())
    second = reading((71, 74, 75))
    second['class_candidates'] = [dict(raw_class_id=4, candidate_name='S1', series_binding_verified=False)]
    runtime.observe(second)
    state = runtime.snapshot()
    assert state['current']['class_name'] is None
    assert 'Class pending' in panel_text(state)
    assert 'S1' not in panel_text(state)
    assert len(runtime.log_path.read_text(encoding='utf-8').splitlines()) == 2
    runtime.observe({'status': 'process_exit'})
    assert runtime.snapshot()['current'] is None


def test_panel_never_fills_unknown_names_from_other_language_or_previous_series(tmp_path):
    runtime = OnlineSeriesRuntime(tmp_path, reader=object())
    runtime.observe(reading((82, 999999, 62)))
    text = panel_text(runtime.snapshot())
    assert 'Unknown track (ID 999999)' in text
    assert 'Snow Forest Cross Country Circuit' in text
    assert 'Discipline pending' in text


def test_later_class_transition_is_preserved_without_attributing_it_to_old_series(tmp_path):
    runtime = OnlineSeriesRuntime(tmp_path, reader=object())
    runtime.observe(reading())
    transition = dict(raw_class_id=4, candidate_name='S1', series_binding_verified=False)
    same = reading()
    same['class_candidates'] = [transition]
    runtime.observe(same)
    runtime.observe(dict(status='waiting_for_series', class_candidates=[{**transition, 'raw_class_id': 3, 'candidate_name': None}]))
    rows = [json.loads(x) for x in runtime.log_path.read_text(encoding='utf-8').splitlines()]
    assert len(rows) == 3
    assert rows[1]['series']['class_name'] is None
    assert rows[1]['class_candidates'][0]['raw_class_id'] == 4
    assert rows[2]['series'] is None
    assert rows[2]['class_candidates'][0]['raw_class_id'] == 3
