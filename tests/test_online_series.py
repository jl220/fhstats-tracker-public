import struct

from forza_tracker.online_series import resolve_track, resolve_series, read_series_state


def test_captured_ids_use_official_english_names_and_real_route_ids():
    assert resolve_track(82).name == 'Snow Forest Cross Country Circuit'
    assert resolve_track(88).name == 'Soni Highlands Cross Country'
    assert resolve_track(91).name == 'Tateyama Alpine Cross Country'
    assert resolve_track(74).route_id == 2211
    assert resolve_track(82).name_key == 'CareerTrackInfo.IDS_DisplayName_dd59030db6db45d48112883f5f63861a'


def test_disciplines_come_from_game_race_modes_and_specials_do_not_follow_menu_offsets():
    for ids, expected in [([71, 74, 75], 'dirt'), ([91, 82, 88], 'cross-country'),
                          ([102, 103, 107], 'street'), ([20, 21, 22], 'road')]:
        result = resolve_series(ids)
        assert result['discipline'] == expected
        assert result['discipline_source'] == 'game_catalog_from_memory_ids'
    assert resolve_track(202).name == 'The Gauntlet'
    assert resolve_track(200).name == 'The Titan'
    assert resolve_track(201).name == 'The Goliath'


def test_unknown_or_mixed_ids_preserve_order_and_do_not_invent_a_discipline():
    result = resolve_series([82, 999999, 62])
    assert [t['track_id'] for t in result['tracks']] == [82, 999999, 62]
    assert result['tracks'][1]['name'] is None
    assert result['discipline'] is None
    for value in (True, '82', -1, 82.0):
        assert resolve_track(value) is None


def test_app_language_does_not_translate_the_online_catalog():
    from forza_tracker.localization import configure_language
    try:
        for lang in ('es', 'fr', 'en'):
            configure_language(lang)
            assert resolve_track(103).name == 'Norikura Descent'
    finally:
        configure_language('en')


def state_memory():
    raw = bytearray(0x200)
    for offset, table in ((0x20, 0x710000), (0xd0, 0x710000), (0x110, 0x720000)):
        struct.pack_into('<Q', raw, offset, table)
    raw[0x38], raw[0xe8] = 3, 1
    struct.pack_into('<10I', raw, 0x128, 71, 74, 75, *([0] * 7))
    return raw


def test_native_state_resolves_all_names_but_does_not_certify_active_series_or_class():
    raw = state_memory()
    result = read_series_state(lambda a, n: bytes(raw[:n]), 0x800000, 0x710000, 0x720000)
    assert result['status'] == 'candidate'
    assert result['track_names'] == ['Waterfall Trail', 'Airfield Trail', 'Cherry Field Trail']
    assert result['discipline'] == 'dirt'
    assert result['completed_event_count'] == 1
    assert result['class_name'] is None
    assert result['series_identity_verified'] is False


def test_native_state_does_not_report_empty_stale_or_incoherent_memory_as_active():
    raw = state_memory()
    raw[0x38] = raw[0xe8] = 0
    assert read_series_state(lambda a, n: bytes(raw[:n]), 0x800000, 0x710000, 0x720000)['status'] == 'empty'
    raw = state_memory()
    raw[0x110] ^= 1
    assert read_series_state(lambda a, n: bytes(raw[:n]), 0x800000, 0x710000, 0x720000)['status'] == 'identity_changed'
    raw = state_memory()
    calls = 0
    def changing(a, n):
        nonlocal calls
        calls += 1
        if calls > 1:
            raw[0x128] ^= 1
        return bytes(raw[:n])
    result = read_series_state(changing, 0x800000, 0x710000, 0x720000)
    assert result['status'] == 'changed_during_read'
    assert 'track_names' not in result
