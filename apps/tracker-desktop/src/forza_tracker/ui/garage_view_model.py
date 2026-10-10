from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from forza_tracker.garage.models import GarageCar, GarageSnapshot


@dataclass(frozen=True)
class GarageCardView:
    capture_index: int
    title: str
    subtitle: str
    class_pi: str
    quantity: int
    confidence: str
    image_path: Path
    car: GarageCar


def build_garage_cards(
    snapshot: GarageSnapshot,
    *,
    snapshot_root: Path | None = None,
    query: str = "",
    manufacturer: str = "",
    class_name: str = "",
    confidence: str = "",
) -> tuple[GarageCardView, ...]:
    normalized_query = _normalize(query)
    normalized_manufacturer = _normalize(manufacturer)
    normalized_class = _normalize(class_name)
    normalized_confidence = _normalize(confidence)
    root = snapshot_root.resolve() if snapshot_root is not None else Path()
    cards: list[GarageCardView] = []

    for car in sorted(snapshot.cars, key=lambda item: item.capture_index):
        searchable = _normalize(" ".join(filter(None, (car.display_name, car.make, car.model))))
        if normalized_query and normalized_query not in searchable:
            continue
        if normalized_manufacturer and _normalize(car.make or "") != normalized_manufacturer:
            continue
        if normalized_class and _normalize(car.car_class or "") != normalized_class:
            continue
        if normalized_confidence and _normalize(car.confidence) != normalized_confidence:
            continue
        image_path = Path(car.image_path)
        if snapshot_root is not None and not image_path.is_absolute():
            image_path = (root / image_path).resolve()
            if not image_path.is_relative_to(root):
                continue
        title = car.model or car.display_name or "Coche sin reconocer"
        subtitle = " ".join(part for part in (str(car.year) if car.year else "", car.make or "") if part)
        class_pi = " ".join(
            part for part in (car.car_class or "", str(car.performance_index) if car.performance_index else "") if part
        )
        cards.append(
            GarageCardView(
                capture_index=car.capture_index,
                title=title,
                subtitle=subtitle,
                class_pi=class_pi,
                quantity=car.quantity,
                confidence=car.confidence,
                image_path=image_path,
                car=car,
            )
        )
    return tuple(cards)


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())
