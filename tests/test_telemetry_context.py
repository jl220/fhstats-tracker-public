from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from forza_tracker.models.result import ParsedResult
from forza_tracker.models.screen_type import ScreenType
from forza_tracker.telemetry.context import apply_latest_telemetry_context, apply_telemetry_context


NOW = datetime(2026, 7, 12, 18, 0, tzinfo=timezone.utc)


def _race_result() -> ParsedResult:
    return ParsedResult(
        screenshot_path=Path("capture.png"),
        screen_type=ScreenType.RACE_RESULT,
        player="Jolu4327",
        circuit_name="OCR Circuit",
        position=8,
        starting_position=11,
        event_type="Open",
        event_number=2,
        event_total=3,
        discipline="Road",
        car="Wrong OCR Car",
        car_class="S1 900",
        class_crop_path=None,
        championship_position=None,
        progress_percent=None,
        best_lap="01:10.000",
        total_time="03:30.000",
        row_detection_confidence=0.31,
        raw_row_text="another player row",
        timestamp=NOW,
        event_plan=["OCR Circuit"],
    )


def _status(*, packet_time: datetime = NOW, position: int = 1) -> dict[str, object]:
    return {
        "state": "connected",
        "connected": True,
        "game": "fh6",
        "last_packet_at": packet_time.isoformat(),
        "race_position": position,
        "car_display_name": "1984 Honda CRXMugen",
        "car_class": "B",
        "performance_index": 600,
    }


def test_telemetry_disabled_preserves_ocr_result() -> None:
    parsed = _race_result()

    corrected = apply_telemetry_context(
        parsed,
        _status(),
        enabled=False,
        stale_after_seconds=3.0,
        now=NOW,
    )

    assert corrected == parsed


def test_fresh_telemetry_corrects_own_race_result_identity() -> None:
    corrected = apply_telemetry_context(
        _race_result(),
        _status(position=1),
        enabled=True,
        stale_after_seconds=3.0,
        now=NOW,
    )

    assert corrected.position == 1
    assert corrected.car == "1984 Honda CRXMugen"
    assert corrected.car_class == "B 600"
    assert corrected.circuit_name == "OCR Circuit"
    assert corrected.event_number == 2
    assert corrected.total_time == "03:30.000"


def test_stale_telemetry_preserves_ocr_result() -> None:
    parsed = _race_result()

    corrected = apply_telemetry_context(
        parsed,
        _status(packet_time=NOW - timedelta(seconds=10), position=1),
        enabled=True,
        stale_after_seconds=3.0,
        now=NOW,
    )

    assert corrected == parsed


def test_invalid_telemetry_position_does_not_replace_ocr_position() -> None:
    corrected = apply_telemetry_context(
        _race_result(),
        _status(position=0),
        enabled=True,
        stale_after_seconds=3.0,
        now=NOW,
    )

    assert corrected.position == 8
    assert corrected.car == "1984 Honda CRXMugen"
    assert corrected.car_class == "B 600"


def test_telemetry_context_does_not_change_non_race_screens() -> None:
    parsed = _race_result()
    grid = ParsedResult(**{**parsed.to_json_dict(), "screen_type": ScreenType.STARTING_GRID, "timestamp": NOW})

    corrected = apply_telemetry_context(
        grid,
        _status(position=1),
        enabled=True,
        stale_after_seconds=3.0,
        now=NOW,
    )

    assert corrected == grid


def test_latest_telemetry_context_reads_status_when_enabled() -> None:
    corrected = apply_latest_telemetry_context(
        _race_result(),
        SimpleNamespace(enabled=True, stale_after_seconds=3.0),
        status_reader=lambda: _status(position=2),
        now=NOW,
    )

    assert corrected.position == 2
    assert corrected.car == "1984 Honda CRXMugen"


def test_latest_telemetry_context_does_not_read_status_when_disabled() -> None:
    def fail_reader() -> dict[str, object]:
        raise AssertionError("disabled telemetry must not read runtime status")

    corrected = apply_latest_telemetry_context(
        _race_result(),
        SimpleNamespace(enabled=False, stale_after_seconds=3.0),
        status_reader=fail_reader,
        now=NOW,
    )

    assert corrected == _race_result()
