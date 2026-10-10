from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import forza_tracker.main as tracker_main
from forza_tracker.main import _build_race_payload
from forza_tracker.models.result import ParsedResult
from forza_tracker.models.screen_type import ScreenType


def test_build_race_payload_combines_grid_race_and_championship() -> None:
    timestamp = datetime(2026, 6, 15, 15, 47, 26, tzinfo=timezone.utc)
    grid = ParsedResult(
        screenshot_path=Path("captures/grid.png"),
        screen_type=ScreenType.STARTING_GRID,
        player="Jolu4327",
        circuit_name="Carrera de los cedros",
        position=None,
        starting_position=11,
        event_type=None,
        event_number=3,
        event_total=3,
        discipline="callejera",
        car="Ford GT '05",
        car_class="A 700",
        class_crop_path=None,
        championship_position=None,
        progress_percent=None,
        best_lap=None,
        total_time=None,
        row_detection_confidence=None,
        raw_row_text=None,
        timestamp=timestamp,
    )
    race = ParsedResult(
        screenshot_path=Path("captures/race.png"),
        screen_type=ScreenType.RACE_RESULT,
        player="Jolu4327",
        circuit_name="Carrera de los cedros",
        position=2,
        starting_position=None,
        event_type=None,
        event_number=None,
        event_total=None,
        discipline=None,
        car="Ford GT '05",
        car_class="A 700",
        class_crop_path=None,
        championship_position=None,
        progress_percent=100,
        best_lap=None,
        total_time="02:07,938",
        row_detection_confidence=0.9,
        raw_row_text=None,
        timestamp=timestamp,
    )
    championship = ParsedResult(
        screenshot_path=Path("captures/champ.png"),
        screen_type=ScreenType.CHAMPIONSHIP_RESULT,
        player="Jolu4327",
        circuit_name="Carrera de los cedros",
        position=None,
        starting_position=None,
        event_type=None,
        event_number=None,
        event_total=None,
        discipline=None,
        car="Ford GT '05",
        car_class="A 700",
        class_crop_path=None,
        championship_position=2,
        progress_percent=None,
        best_lap=None,
        total_time=None,
        row_detection_confidence=None,
        raw_row_text=None,
        timestamp=timestamp,
    )

    payload = _build_race_payload(grid, race, championship)

    assert payload["event_number"] == 3
    assert payload["event_total"] == 3
    assert payload["discipline"] == "callejera"
    assert payload["start_position"] == 11
    assert payload["finish_position"] == 2
    assert payload["championship_position"] == 2


class _RecordingApiClient:
    def __init__(self, *, failures: int = 0) -> None:
        self.payloads: list[dict[str, Any]] = []
        self.failures = failures

    def upload_race(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(payload)
        if self.failures:
            self.failures -= 1
            raise RuntimeError("temporary upload failure")
        return {"id": len(self.payloads)}


class _RecordingEventBus:
    def __init__(self) -> None:
        self.events: list[object] = []

    def publish(self, event: object) -> None:
        self.events.append(event)


def _race_result(screen_type: ScreenType, *, championship_position: int | None = None) -> ParsedResult:
    return ParsedResult(
        screenshot_path=Path(f"captures/{screen_type.value}.png"),
        screen_type=screen_type,
        player="Jolu4327",
        circuit_name="Carrera de los cedros",
        position=2 if screen_type == ScreenType.RACE_RESULT else None,
        starting_position=None,
        event_type="online_matchmaking",
        event_number=1,
        event_total=3,
        discipline="carretera",
        car="Ford GT '05",
        car_class="A 700",
        class_crop_path=None,
        championship_position=championship_position,
        progress_percent=100 if screen_type == ScreenType.RACE_RESULT else None,
        best_lap="00:41,200" if screen_type == ScreenType.RACE_RESULT else None,
        total_time="02:07,938" if screen_type == ScreenType.RACE_RESULT else None,
        row_detection_confidence=0.9 if screen_type == ScreenType.RACE_RESULT else None,
        raw_row_text=None,
        timestamp=datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc),
    )


def test_race_snapshot_is_sent_immediately_and_enriched_after_championship() -> None:
    client = _RecordingApiClient()
    event_bus = _RecordingEventBus()
    race = _race_result(ScreenType.RACE_RESULT)
    championship = _race_result(ScreenType.CHAMPIONSHIP_RESULT, championship_position=2)
    state = tracker_main._RaceUploadState(
        enabled=True,
        api_client=client,
        event_bus=event_bus,
    )

    state.record_race_result(race)
    state.enrich_championship(championship)

    assert state.uploaded is True
    assert [payload["championship_position"] for payload in client.payloads] == [None, 2]
    assert len(event_bus.events) == 2


def test_failed_immediate_upload_retries_before_advancing_to_the_next_grid() -> None:
    client = _RecordingApiClient(failures=1)
    event_bus = _RecordingEventBus()
    race = _race_result(ScreenType.RACE_RESULT)
    next_grid = _race_result(ScreenType.STARTING_GRID)
    state = tracker_main._RaceUploadState(
        enabled=True,
        api_client=client,
        event_bus=event_bus,
    )

    state.record_race_result(race)
    assert state.uploaded is False

    state.advance_to_grid(next_grid)

    assert state.uploaded is True
    assert state.starting_grid is next_grid
    assert len(client.payloads) == 2
    assert len(event_bus.events) == 1
