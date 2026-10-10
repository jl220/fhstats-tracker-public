from forza_tracker.ui.rivals_panel import utc_stamp
import gc
import pytest


@pytest.fixture(autouse=True)
def release_tk_cycles_on_ui_thread():
    yield
    # Destroyed Tk widgets can form callback cycles. Collect them on this
    # thread before later runtime tests start background sampling workers.
    gc.collect()


def test_home_keeps_saved_event_bests_visible_before_car_telemetry_then_uses_current_car(monkeypatch):
    import tkinter as tk
    from types import SimpleNamespace
    from forza_tracker.ui import rivals_panel
    from forza_tracker import localization
    monkeypatch.setattr(rivals_panel, 'build_track_map_panel', lambda *a, **k: None)
    event = {'event_id': 'dirt:sekibe-scramble', 'event_name': 'Sekibe Scramble',
             'name_en': 'Sekibe Scramble', 'name_es': 'Circuito de tierra de Sekibe',
             'discipline': 'dirt', 'event_kind': 'circuit'}
    alfa = {'attempt_id': 'alfa', 'event_id': event['event_id'], 'completed': True,
            'validity': 'clean', 'car_ordinal': 3494, 'car_name': 'Alfa Romeo 33S',
            'car_class': 'S1', 'time_ms': 51528,
            'current_comparison': {'status': 'outside_top50'}}
    ultima = {**alfa, 'attempt_id': 'ultima', 'car_ordinal': 2569, 'car_name': 'Ultima Evolution Coupe',
              'car_class': 'S2', 'time_ms': 53056}
    other = {**alfa, 'attempt_id': 'other', 'event_id': 'dirt:hirosaki-scramble', 'time_ms': 1000}
    root = tk.Tk()
    root.withdraw()
    try:
        for language in ('es', 'en'):
            monkeypatch.setattr(localization, '_active_language', language)
            monkeypatch.setattr(rivals_panel, 'current_language', lambda: language)
            panel = rivals_panel.RivalsLivePanel(root, SimpleNamespace(), lambda: None)
            state = {'current': {'event': event}, 'events': [event], 'bests': [alfa, ultima, other]}
            panel.render(state)
            value = panel.metrics['best'][0].cget('text')
            assert 'S1 · 0:51.528' in value
            assert 'S2 · 0:53.056' in value
            assert '0:01.000' not in value
            assert 'Alfa Romeo' in panel.metrics['best'][1].cget('text')
            assert 'Ultima' in panel.metrics['best'][1].cget('text')
            assert panel.metric_titles['best'].cget('text') == (
                'MEJORES GUARDADAS · ESTE EVENTO' if language == 'es' else 'SAVED BESTS · THIS EVENT')
            assert panel.rank.cget('text') == ('Tus mejores guardadas en este evento' if language == 'es' else 'Your saved bests at this event')
            assert 'top 50' in panel.gap.cget('text')
            # A different detected car has no best: never label another car's time as its own.
            panel.render({**state, 'current': {'event': event, 'car_ordinal': 999, 'car_class': 'S1'}})
            assert panel.metrics['best'][0].cget('text') == '—'
            assert panel.rank.cget('text') == ('Aún sin mejor limpia para este coche y clase' if language == 'es' else 'No clean best for this car and class yet')
            # The matched current car restores the normal single-best card and comparison.
            panel.render({**state, 'current': {'event': event, 'car_ordinal': 3494, 'car_class': 'S1'}, 'best': alfa})
            assert panel.metrics['best'][0].cget('text') == '0:51.528'
            assert panel.metric_titles['best'].cget('text') == ('MEJOR LIMPIA · ESTE COCHE' if language == 'es' else 'CLEAN BEST · THIS CAR')
            # Leaving/changing event clears the fallback rather than leaking Sekibe's records.
            panel.render({'current': {'event': {**event, 'event_id': 'dirt:unknown'}}, 'bests': [alfa, ultima]})
            assert panel.metrics['best'][0].cget('text') == '—'
            assert '51.528' not in panel.gap.cget('text')
            panel.destroy()
    finally:
        root.destroy()


def test_history_shows_snapshot_rank_for_each_time_in_both_languages_and_modes(monkeypatch):
    import tkinter as tk
    from types import SimpleNamespace
    from forza_tracker.rivals_snapshot import compare_attempt
    from forza_tracker.ui import rivals_panel

    board = {'event_id': 'dirt:sekibe-scramble', 'car_class': 'S1',
             'coverage': 'complete_top50', 'captured_at': '2026-09-11T20:00:00+00:00',
             'entries': [{'rank': i, 'time_ms': 49000 + i * 100} for i in range(1, 51)]}
    base = {'event_id': board['event_id'], 'event_name': 'Sekibe Scramble',
            'car_name': 'Alfa Romeo', 'car_class': 'S1', 'performance_index': 800,
            'captured_at': '2026-09-12T19:00:00+00:00', 'completed': True, 'validity': 'clean'}
    cases = [
        ('rank', 51450, {}, board, '≈25', '≈25'),
        ('outside', 55000, {}, board, 'Fuera del top 50', 'Outside top 50'),
        ('cutoff', 54000, {}, board, 'Empate en top 50', 'Tied at top 50'),
        ('tie', 51500, {}, board, '≈25 · empate', '≈25 · tie'),
        ('missing', 51450, {}, None, 'Sin snapshot', 'No snapshot'),
        ('partial', 51450, {}, {**board, 'coverage': 'partial'}, 'Snapshot parcial', 'Partial snapshot'),
        ('invalid', 51450, {'validity': 'invalid'}, board, 'No válida', 'Not eligible'),
        ('incomplete', None, {'completed': False}, board, 'Incompleto', 'Incomplete'),
    ]
    rows = []
    for key, time_ms, changes, snapshot, *_ in cases:
        row = {**base, 'attempt_id': key, 'time_ms': time_ms, **changes}
        row['current_comparison'] = compare_attempt(row, snapshot)
        # The table must use the available snapshot, not an older stored comparison.
        row['original_comparison'] = {'status': 'estimated_rank', 'estimated_rank': 1}
        rows.append(row)
    root = tk.Tk()
    root.withdraw()
    try:
        for language, expected_index in [('es', 4), ('en', 5)]:
            monkeypatch.setattr(rivals_panel, 'current_language', lambda: language)
            panel = rivals_panel.RivalsHistoryPanel(root, SimpleNamespace())
            assert not hasattr(panel, 'validity_buttons')
            assert not hasattr(panel, '_confirm')
            state = {'revision': 1, 'attempts': rows, 'bests': rows[:6]}
            panel.render(state)
            assert panel.tree.heading('rank', 'text') == ('Puesto · snapshot' if language == 'es' else 'Rank · snapshot')
            for mode, shown in [('bests', cases[:6]), ('attempts', cases)]:
                panel._set_mode(mode)
                for case in shown:
                    assert panel.tree.set(case[0], 'rank') == case[expected_index]
            changed = {**rows[0], 'current_comparison': {'status': 'outside_top50'}}
            panel.tree.selection_set('rank')
            panel.render({**state, 'revision': 2, 'attempts': [changed]})
            assert panel.tree.set('rank', 'rank') == ('Fuera del top 50' if language == 'es' else 'Outside top 50')
            assert panel.tree.selection() == ('rank',)
            panel.destroy()
    finally:
        root.destroy()


def test_live_clock_advances_between_samples_but_stops_on_pause_loss_and_dirty():
    from forza_tracker.ui.rivals_panel import live_clock_values
    state = {'active': True, 'telemetry_fresh': True,
             'current': {'current_lap_seconds': 10, 'validity': 'unknown'},
             'clock': {'seconds': 10, 'sampled_at': 100, 'advancing': True}}
    assert live_clock_values(state, 100.033) == (10033, 33)
    assert live_clock_values(state, 100.066) == (10066, 66)
    assert live_clock_values(state, 101) == (10100, 100)  # bounded, never keeps running on lost data
    state['clock']['advancing'] = False
    assert live_clock_values(state, 100.066) == (10000, 0)
    state['current']['validity'] = 'invalid'
    assert live_clock_values(state, 100.066) == (None, 0)
    state['current']['validity'] = 'unknown'
    state['telemetry_fresh'] = False
    assert live_clock_values(state, 100.066) == (None, 0)


def test_live_clock_resets_to_game_time_at_new_lap_and_never_changes_stored_results():
    from forza_tracker.ui.rivals_panel import live_clock_values
    last = {'time_ms': 53487}
    state = {'active': True, 'telemetry_fresh': True, 'last': last,
             'current': {'current_lap_seconds': .02},
             'clock': {'seconds': .02, 'sampled_at': 100, 'advancing': False}}
    assert live_clock_values(state, 100.05) == (20, 0)
    assert last == {'time_ms': 53487}


def test_published_snapshot_result_is_shown_without_local_clean_best_in_both_languages(monkeypatch):
    import tkinter as tk
    from types import SimpleNamespace
    from forza_tracker.ui import rivals_panel
    monkeypatch.setattr(rivals_panel, 'build_track_map_panel', lambda *a, **k: None)
    root = tk.Tk()
    root.withdraw()
    runtime = SimpleNamespace(select_event=lambda _: None, confirm_validity=lambda *a: None,
                              refresh_snapshot=lambda _: None)
    state = {'current': {}, 'snapshot': {'car_class': 'S1', 'captured_at': '2026-09-11T20:06:50+00:00',
             'references': {'top_50_ms': 50762}, 'coverage': 'complete_top50',
             'player_result': {'rank': 25, 'player': 'Jolu4327', 'time_ms': 50379, 'car': 'Alfa Romeo 33S'}}}
    try:
        for lang, heading in [('es', 'Tu marca en el snapshot · puesto 25'), ('en', 'Your snapshot result · rank 25')]:
            monkeypatch.setattr(rivals_panel, 'current_language', lambda: lang)
            panel = rivals_panel.RivalsLivePanel(root, runtime, lambda: None)
            panel.render(state)
            assert panel.rank.cget('text') == heading
            assert '0:50.379' in panel.published_result.cget('text')
            assert 'Alfa Romeo 33S' in panel.published_result.cget('text')
            assert 'S1' in panel.published_result.cget('text')
            assert '0:50.762' in panel.references.cget('text')
            assert '2026-09-11' in panel.snapshot_info.cget('text')
            assert panel.metrics['best'][0].cget('text') == '—'
            panel.render({'current': {}})
            assert panel.published_result.cget('text') == ''
            panel.destroy()
    finally:
        root.destroy()


def test_validity_uses_plain_binary_labels_in_both_languages(monkeypatch):
    from forza_tracker.ui import rivals_panel
    row = {'completed': True, 'validity': 'clean', 'validity_source': 'memory_sampled'}
    for language, expected in [('es', 'Limpia'), ('en', 'Clean')]:
        monkeypatch.setattr(rivals_panel, 'current_language', lambda: language)
        assert rivals_panel.validity_text(row) == expected


def test_capture_offset_is_converted_before_being_labelled_utc():
    assert utc_stamp('2026-09-12T16:03:04+02:00') == '2026-09-12 14:03:04 UTC'
    assert utc_stamp('2026-09-12T14:03:04Z') == '2026-09-12 14:03:04 UTC'
    assert utc_stamp('broken') == '—'
    assert utc_stamp('2026-09-12T14:03:04') == '—'


def test_live_panel_switches_between_automatic_and_fallback_in_both_languages(monkeypatch):
    import tkinter as tk
    from types import SimpleNamespace
    from forza_tracker.ui import rivals_panel
    monkeypatch.setattr(rivals_panel, 'build_track_map_panel', lambda *a, **kw: None)
    root = tk.Tk()
    root.withdraw()
    runtime = SimpleNamespace(select_event=lambda _: None, confirm_validity=lambda *a: None,
                              refresh_snapshot=lambda _: None)
    try:
        for language, title in [('es', 'Circuito de tierra de Sekibe'), ('en', 'Sekibe Scramble')]:
            monkeypatch.setattr(rivals_panel, 'current_language', lambda: language)
            panel = rivals_panel.RivalsLivePanel(root, runtime, lambda: None)
            event = {'event_id': 'dirt:sekibe-scramble', 'event_name': 'Sekibe Scramble',
                     'name_en': 'Sekibe Scramble', 'name_es': 'Circuito de tierra de Sekibe',
                     'discipline': 'dirt', 'event_kind': 'circuit'}
            row = {'attempt_id': 'a', 'completed': True, 'validity': 'clean',
                   'validity_source': 'memory_sampled', 'time_ms': 55000}
            panel.render({'current': {'event': event, 'event_source': 'memory', 'validity': 'invalid',
                                     'current_lap_seconds': 3.0}, 'events': [event],
                          'last': row, 'best': row, 'telemetry_fresh': True})
            assert panel.title.cget('text') == title
            assert panel.assisted.winfo_manager() == ''
            assert not hasattr(panel, 'validity_row')
            assert not hasattr(panel, '_confirm')
            assert panel.metrics['current'][0].cget('text') == '—'
            panel.render({'current': {'event': None, 'raw_event': {'name': 'Unknown course'}}, 'events': [event]})
            assert panel.title.cget('text') == 'Unknown course'
            assert panel.assisted.winfo_manager() == 'pack'
            assert panel.event.get() == ''
            pending = next(iter(panel._event_options))
            panel.event.set(pending)
            panel.render({'current': {'event': None, 'raw_event': {'name': 'Unknown course'}}, 'events': [event]})
            assert panel.event.get() == pending
            panel.destroy()
    finally:
        root.destroy()


def test_sector_delta_signs_have_readable_text_and_colours():
    from forza_tracker.ui.rivals_panel import sector_delta
    from forza_tracker.ui.theme import COLORS
    assert sector_delta(-125) == ('-0.125 s', COLORS['accent'])
    assert sector_delta(250) == ('+0.250 s', COLORS['danger'])
    assert sector_delta(0) == ('±0.000 s', COLORS['muted'])
    assert sector_delta(None) == ('—', COLORS['muted'])


def test_finished_sector_split_is_rendered_then_cleared_for_invalid_and_unavailable_states(monkeypatch):
    import copy
    import tkinter as tk
    from types import SimpleNamespace

    from forza_tracker.rivals_sectors import SectorTracker
    from forza_tracker.ui import rivals_panel
    from forza_tracker.ui.theme import COLORS

    identity = ('driver', 'dirt:sekibe-scramble', 'instance', 1, 1150, 'B', 600)

    def sample(index, lap, start, crossing):
        return {'record_id': 'r', 'car_id': 'c', 'dirty': False, 'race_clock': crossing,
                'lap_start_clock': start, 'checkpoints': {'crossing_index': index, 'lap_index': lap,
                'crossing_race_clock': crossing, 'crossing_lap_clock': crossing - start,
                'rewind_active': False, 'rewind_affected': False}}

    def finish(index, lap, old_start, crossing):
        value = sample(index, lap, crossing, crossing)
        value['checkpoints']['crossing_lap_clock'] = crossing - old_start
        return value

    previous = None

    def feed(tracker, value):
        nonlocal previous
        if previous and previous['race_clock'] < value['race_clock']:
            clock = previous['race_clock'] + .1
            while clock < value['race_clock'] - .001:
                intermediate = copy.deepcopy(previous)
                intermediate['race_clock'] = clock
                tracker.update(intermediate, identity)
                clock += .1
        tracker.update(value, identity)
        previous = copy.deepcopy(value)

    tracker = SectorTracker()
    feed(tracker, sample(100, 4, 40, 50))
    feed(tracker, finish(101, 5, 40, 60))
    for i in range(1, 12):
        feed(tracker, sample(101 + i, 5, 60, 60 + i))
    feed(tracker, finish(113, 6, 60, 72))
    for i in range(1, 12):
        feed(tracker, sample(113 + i, 6, 72, 72 + i * .9))
    feed(tracker, finish(125, 7, 72, 82.8))
    sectors = tracker.snapshot()
    assert sectors['last_split'] == {'index': 3, 'duration_ms': 3600, 'delta_ms': -400}

    monkeypatch.setattr(rivals_panel, 'build_track_map_panel', lambda *a, **kw: None)
    root = tk.Tk()
    root.withdraw()
    runtime = SimpleNamespace(select_event=lambda _: None, confirm_validity=lambda *a: None,
                              refresh_snapshot=lambda _: None)
    event = {'event_id': 'dirt:sekibe-scramble', 'event_name': 'Sekibe Scramble',
             'name_en': 'Sekibe Scramble', 'name_es': 'Circuito de tierra de Sekibe',
             'discipline': 'dirt', 'event_kind': 'circuit'}
    try:
        for language, expected in [('es', 'Último sector completado · S3 · 0:03.600'),
                                   ('en', 'Last completed sector · S3 · 0:03.600')]:
            monkeypatch.setattr(rivals_panel, 'current_language', lambda: language)
            panel = rivals_panel.RivalsLivePanel(root, runtime, lambda: None)
            current = {'event': event, 'event_source': 'memory', 'validity': 'clean',
                       'current_lap_seconds': 0.0, 'sectors': sectors}
            panel.render({'current': current, 'events': [event], 'telemetry_fresh': True})
            assert panel.last_split_label.cget('text') == expected
            assert panel.last_split_delta.cget('text') == '-0.400 s'
            assert panel.last_split_delta.cget('fg') == COLORS['accent']

            for unavailable in ({**sectors, 'status': 'invalid'},
                                {**sectors, 'status': 'waiting_finish'},
                                {**sectors, 'last_split': None}):
                current['sectors'] = unavailable
                panel.render({'current': current, 'events': [event], 'telemetry_fresh': True})
                assert panel.last_split_label.cget('text') == ''
                assert panel.last_split_delta.cget('text') == ''
                assert panel.last_split_delta.cget('fg') == COLORS['muted']
            panel.destroy()
    finally:
        root.destroy()
