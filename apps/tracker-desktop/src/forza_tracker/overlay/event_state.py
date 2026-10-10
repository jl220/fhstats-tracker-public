from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Literal

from forza_tracker.models.result import EventPlanSlots
from forza_tracker.paths import user_data_root


OverlayPhase = Literal["registration", "starting_grid", "closed"]
OVERLAY_EVENT_PATH = user_data_root() / "data" / "runtime" / "overlay-event.json"


@dataclass(frozen=True)
class OverlayEventSnapshot:
    schema_version: int
    event_id: str
    detected_at: datetime
    updated_at: datetime
    source_screen: str
    phase: OverlayPhase
    car_class: str | None
    circuits: EventPlanSlots


def write_overlay_event(
    snapshot: OverlayEventSnapshot, path: Path | None = None
) -> None:
    target = path or OVERLAY_EVENT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(snapshot)
    payload["detected_at"] = snapshot.detected_at.isoformat()
    payload["updated_at"] = snapshot.updated_at.isoformat()
    payload["circuits"] = list(snapshot.circuits)
    handle, temporary_name = tempfile.mkstemp(prefix="overlay-event-", suffix=".json", dir=target.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
        os.replace(temporary_name, target)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def read_overlay_event(path: Path | None = None) -> OverlayEventSnapshot | None:
    target = path or OVERLAY_EVENT_PATH
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        circuits = payload["circuits"]
        if not isinstance(circuits, list) or len(circuits) != 3:
            return None
        phase = str(payload["phase"])
        if phase not in {"registration", "starting_grid", "closed"}:
            return None
        return OverlayEventSnapshot(
            schema_version=int(payload["schema_version"]),
            event_id=str(payload["event_id"]),
            detected_at=datetime.fromisoformat(str(payload["detected_at"])),
            updated_at=datetime.fromisoformat(str(payload["updated_at"])),
            source_screen=str(payload["source_screen"]),
            phase=phase,
            car_class=str(payload["car_class"]) if payload.get("car_class") else None,
            circuits=tuple(str(value) if value else None for value in circuits),
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


class OverlayEventPublisher:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or OVERLAY_EVENT_PATH
        self._current = read_overlay_event(self._path)

    def registration(
        self,
        *,
        slots: EventPlanSlots,
        car_class: str | None,
        source_screen: str,
        observed_at: datetime,
    ) -> OverlayEventSnapshot:
        current = self._current
        same = self._is_same_registration(current, slots, observed_at)
        if same and current is not None:
            circuits = tuple(new or old for old, new in zip(current.circuits, slots))
            snapshot = replace(
                current,
                updated_at=observed_at,
                source_screen=source_screen,
                car_class=car_class or current.car_class,
                circuits=circuits,
            )
        else:
            snapshot = OverlayEventSnapshot(
                schema_version=1,
                event_id=uuid.uuid4().hex,
                detected_at=observed_at,
                updated_at=observed_at,
                source_screen=source_screen,
                phase="registration",
                car_class=car_class,
                circuits=slots,
            )
        self._store(snapshot)
        return snapshot

    def mark_phase(
        self, phase: OverlayPhase, *, source_screen: str, observed_at: datetime
    ) -> OverlayEventSnapshot | None:
        if self._current is None:
            return None
        snapshot = replace(
            self._current,
            phase=phase,
            source_screen=source_screen,
            updated_at=observed_at,
        )
        self._store(snapshot)
        return snapshot

    def close(self, *, source_screen: str, observed_at: datetime) -> OverlayEventSnapshot | None:
        return self.mark_phase("closed", source_screen=source_screen, observed_at=observed_at)

    @staticmethod
    def _is_same_registration(
        current: OverlayEventSnapshot | None,
        slots: EventPlanSlots,
        observed_at: datetime,
    ) -> bool:
        if current is None or current.phase != "registration":
            return False
        if (observed_at - current.detected_at).total_seconds() > 35:
            return False
        return not any(old and new and old != new for old, new in zip(current.circuits, slots))

    def _store(self, snapshot: OverlayEventSnapshot) -> None:
        write_overlay_event(snapshot, self._path)
        self._current = snapshot
