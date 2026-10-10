from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def performance_class(pi: int) -> str:
    if not 100 <= pi <= 999:
        raise ValueError("El PI debe estar en el rango 100..999.")
    for maximum, label in (
        (400, "D"),
        (500, "C"),
        (600, "B"),
        (700, "A"),
        (800, "S1"),
        (900, "S2"),
        (998, "R"),
        (999, "X"),
    ):
        if pi <= maximum:
            return label
    raise AssertionError("Rango de PI no cubierto")


@dataclass(frozen=True)
class TuneMetadata:
    creator: str
    car_id: int
    car: str | None
    tune_name: str
    created_at: datetime
    tune_key: str


@dataclass(frozen=True)
class OrderedTuneCard:
    metadata: TuneMetadata
    share_code: str


@dataclass(frozen=True)
class TuneExportRecord:
    creator: str
    car_id: int
    car: str | None
    tune_name: str
    share_code: str
    created_at: datetime
    performance_index: int
    tune_key: str

    def __post_init__(self) -> None:
        if (
            len(self.share_code) != 9
            or not self.share_code.isascii()
            or not self.share_code.isdigit()
        ):
            raise ValueError(
                "El código compartido debe contener exactamente nueve dígitos ASCII."
            )
        performance_class(self.performance_index)
        if (
            len(self.tune_key) != 32
            or not self.tune_key.isascii()
            or any(character not in "0123456789abcdef" for character in self.tune_key)
        ):
            raise ValueError("La clave de tuneo debe contener 32 dígitos hexadecimales.")
        if self.created_at.tzinfo is None:
            raise ValueError("La fecha de creación debe incluir zona horaria.")

    @property
    def performance_class(self) -> str:
        return performance_class(self.performance_index)

    def to_dict(self) -> dict[str, Any]:
        return {
            "creator": self.creator,
            "car_id": self.car_id,
            "car": self.car,
            "tune_name": self.tune_name,
            "share_code": self.share_code,
            "created_at": _utc_text(self.created_at),
            "class": self.performance_class,
            "performance_index": self.performance_index,
            "tune_key": self.tune_key,
        }


@dataclass
class ExportDocument:
    creator: str
    batch_size: int
    started_at: datetime
    updated_at: datetime
    status: str = "running"
    tunes: list[TuneExportRecord] = field(default_factory=list)
    error: dict[str, str] | None = None

    @classmethod
    def start(cls, creator: str, batch_size: int) -> "ExportDocument":
        if not creator.strip():
            raise ValueError("El creador no puede estar vacío.")
        if batch_size <= 0:
            raise ValueError("El tamaño de bloque debe ser positivo.")
        now = datetime.now(timezone.utc)
        return cls(
            creator=creator.strip(),
            batch_size=batch_size,
            started_at=now,
            updated_at=now,
        )


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
