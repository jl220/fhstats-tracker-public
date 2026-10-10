from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ScanOptions:
    record_video: bool = True
    preserve_duplicates: bool = True


@dataclass(frozen=True)
class GarageCar:
    capture_index: int
    display_name: str
    year: int | None
    make: str | None
    model: str | None
    car_class: str | None
    performance_index: int | None
    canonical_id: str | None
    confidence: str
    raw_ocr: str
    image_path: str
    duplicate_group: str | None
    quantity: int = 1


@dataclass(frozen=True)
class GarageSnapshot:
    scan_id: str
    created_at: str
    completed_at: str | None
    completed: bool
    options: ScanOptions
    cars: tuple[GarageCar, ...] = ()
    video_path: str | None = None
    expected_count: int | None = None
    observed_count: int = 0
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, **asdict(self)}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> GarageSnapshot:
        version = payload.get("schema_version")
        if version != SCHEMA_VERSION:
            raise ValueError(f"Version de manifiesto de garaje no compatible: {version!r}")
        options_payload = payload.get("options")
        cars_payload = payload.get("cars")
        if not isinstance(options_payload, dict) or not isinstance(cars_payload, list):
            raise ValueError("El manifiesto de garaje no tiene una estructura valida.")
        return cls(
            scan_id=str(payload["scan_id"]),
            created_at=str(payload["created_at"]),
            completed_at=str(payload["completed_at"]) if payload.get("completed_at") else None,
            completed=bool(payload.get("completed", False)),
            options=ScanOptions(**options_payload),
            cars=tuple(GarageCar(**item) for item in cars_payload),
            video_path=str(payload["video_path"]) if payload.get("video_path") else None,
            expected_count=int(payload["expected_count"]) if payload.get("expected_count") is not None else None,
            observed_count=int(payload.get("observed_count", 0)),
            warning=str(payload["warning"]) if payload.get("warning") else None,
        )
