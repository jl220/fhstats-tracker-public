from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from forza_tracker.models.screen_type import ScreenType


EventPlanSlots = tuple[str | None, str | None, str | None]


@dataclass(frozen=True)
class ParsedResult:
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
    event_plan_slots: EventPlanSlots | None = None

    def to_json_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["screen_type"] = self.screen_type.value
        data["screenshot_path"] = str(self.screenshot_path)
        data["class_crop_path"] = None if self.class_crop_path is None else str(self.class_crop_path)
        data["timestamp"] = self.timestamp.isoformat()
        if self.event_plan_slots is not None:
            data["event_plan_slots"] = list(self.event_plan_slots)
        return data
