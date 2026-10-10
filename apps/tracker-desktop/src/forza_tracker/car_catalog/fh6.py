from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from forza_tracker.paths import application_root


FH6_CAR_CATALOG_PATH = application_root() / "data" / "canonical" / "cars" / "fh6_car_ordinals.csv"


@dataclass(frozen=True)
class FH6Car:
    car_ordinal: int
    display_name: str
    confidence: str


def lookup_fh6_car(car_ordinal: int, path: Path = FH6_CAR_CATALOG_PATH) -> FH6Car | None:
    return load_fh6_car_catalog(path).get(int(car_ordinal))


def load_fh6_car_catalog(path: Path = FH6_CAR_CATALOG_PATH) -> dict[int, FH6Car]:
    if path == FH6_CAR_CATALOG_PATH:
        try:
            signature = path.stat().st_mtime_ns
        except OSError:
            return {}
        return _load_default_catalog(signature)
    return _load_catalog(path)


@lru_cache(maxsize=2)
def _load_default_catalog(_signature: int) -> dict[int, FH6Car]:
    return _load_catalog(FH6_CAR_CATALOG_PATH)


def _load_catalog(path: Path) -> dict[int, FH6Car]:
    cars: dict[int, FH6Car] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file, delimiter=";")
        for row in reader:
            car = _car_from_row(row)
            if car is not None:
                cars[car.car_ordinal] = car
    return cars


def _car_from_row(row: dict[str, str]) -> FH6Car | None:
    car_id = (row.get("car_id") or "").strip()
    if not car_id.isdigit():
        return None
    return FH6Car(
        car_ordinal=int(car_id),
        display_name=(row.get("display_name") or "").strip(),
        confidence=(row.get("confidence") or "").strip(),
    )
