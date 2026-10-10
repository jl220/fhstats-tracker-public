from datetime import datetime, timezone
from pathlib import Path

from forza_tracker.main import _is_final_championship_result
from forza_tracker.models.result import ParsedResult
from forza_tracker.models.screen_type import ScreenType


def _championship_result(event_number: int | None, event_total: int | None) -> ParsedResult:
    return ParsedResult(
        screenshot_path=Path("championship.png"),
        screen_type=ScreenType.CHAMPIONSHIP_RESULT,
        player="Jolu4327",
        circuit_name=None,
        position=None,
        starting_position=None,
        event_type="online_matchmaking",
        event_number=event_number,
        event_total=event_total,
        discipline=None,
        car=None,
        car_class=None,
        class_crop_path=None,
        championship_position=1,
        progress_percent=None,
        best_lap=None,
        total_time=None,
        row_detection_confidence=None,
        raw_row_text=None,
        timestamp=datetime.now(timezone.utc),
        event_plan=None,
    )


def test_only_final_championship_result_closes_event_context() -> None:
    assert not _is_final_championship_result(_championship_result(1, 3))
    assert not _is_final_championship_result(_championship_result(2, 3))
    assert _is_final_championship_result(_championship_result(3, 3))
    assert not _is_final_championship_result(_championship_result(None, 3))
