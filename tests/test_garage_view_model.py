from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from forza_tracker.garage.models import GarageCar, GarageSnapshot, ScanOptions
from forza_tracker.ui.garage_view_model import build_garage_cards


def _snapshot() -> GarageSnapshot:
    cars = (
        GarageCar(3, "2023 Acura Integra", 2023, "Acura", "Integra A-Spec", "C", 484, "3", "confirmed", "", "raw/3.png", None),
        GarageCar(1, "1992 Nissan Skyline GT-R", 1992, "Nissan", "Skyline GT-R", "A", 700, "1", "review", "", "raw/1.png", None),
        GarageCar(2, "2001 Acura Integra Type R", 2001, "Acura", "Integra Type R", "B", 600, "2", "confirmed", "", "raw/2.png", None, 2),
    )
    return GarageSnapshot(
        scan_id="scan",
        created_at=datetime.now(UTC).isoformat(),
        completed_at=datetime.now(UTC).isoformat(),
        completed=True,
        options=ScanOptions(),
        cars=cars,
        expected_count=4,
        observed_count=4,
    )


def test_garage_cards_default_to_capture_order_and_resolve_images(tmp_path: Path) -> None:
    cards = build_garage_cards(_snapshot(), snapshot_root=tmp_path)

    assert [card.capture_index for card in cards] == [1, 2, 3]
    assert cards[0].image_path == (tmp_path / "raw/1.png").resolve()
    assert cards[1].quantity == 2


def test_garage_cards_search_make_and_model() -> None:
    assert [card.capture_index for card in build_garage_cards(_snapshot(), query="nissan skyline")] == [1]
    assert [card.capture_index for card in build_garage_cards(_snapshot(), query="integra")] == [2, 3]


def test_garage_cards_filter_manufacturer_class_and_review() -> None:
    assert [card.capture_index for card in build_garage_cards(_snapshot(), manufacturer="acura")] == [2, 3]
    assert [card.capture_index for card in build_garage_cards(_snapshot(), class_name="B")] == [2]
    assert [card.capture_index for card in build_garage_cards(_snapshot(), confidence="review")] == [1]
