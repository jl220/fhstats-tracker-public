from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from forza_tracker.models.screen_type import ScreenType


@dataclass(frozen=True)
class ResultDetected:
    screenshot_path: Path
    confidence: float
    template_name: str | None
    screen_type: ScreenType
    timestamp: datetime


@dataclass(frozen=True)
class ResultParsed:
    screenshot_path: Path
    screen_type: ScreenType
    player: str | None
    circuit_name: str | None
    position: int | None
    starting_position: int | None
    event_type: str | None
    event_number: int | None
    event_total: int | None
    discipline: str | None
    car: str | None
    car_class: str | None
    class_crop_path: Path | None
    championship_position: int | None
    progress_percent: int | None
    best_lap: str | None
    total_time: str | None
    row_detection_confidence: float | None
    raw_row_text: str | None
    timestamp: datetime
    event_plan: list[str] | None = None


@dataclass(frozen=True)
class ResultUploaded:
    backend_id: str
    timestamp: datetime
