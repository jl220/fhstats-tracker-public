from forza_tracker.ui.app import should_auto_start_capture


def test_auto_capture_starts_when_game_opens_and_capture_is_idle() -> None:
    assert should_auto_start_capture(
        game_running=True,
        capture_running=False,
        has_valid_player_name=True,
        auto_capture_suppressed=False,
    )


def test_auto_capture_stays_off_after_manual_stop_while_game_keeps_running() -> None:
    assert not should_auto_start_capture(
        game_running=True,
        capture_running=False,
        has_valid_player_name=True,
        auto_capture_suppressed=True,
    )


def test_auto_capture_never_starts_without_player_or_when_already_running() -> None:
    assert not should_auto_start_capture(
        game_running=True,
        capture_running=False,
        has_valid_player_name=False,
        auto_capture_suppressed=False,
    )
    assert not should_auto_start_capture(
        game_running=True,
        capture_running=True,
        has_valid_player_name=True,
        auto_capture_suppressed=False,
    )






