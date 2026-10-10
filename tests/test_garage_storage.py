from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from forza_tracker.garage.models import GarageCar, GarageSnapshot, ScanOptions
from forza_tracker.garage.storage import (
    GarageCountDropWarning,
    GarageInvalidDraftError,
    GarageStore,
)


def _car(index: int) -> GarageCar:
    return GarageCar(
        capture_index=index,
        display_name=f"Car {index}",
        year=2026,
        make="FHStats",
        model=f"Model {index}",
        car_class="A",
        performance_index=700,
        canonical_id=str(index),
        confidence="confirmed",
        raw_ocr=f"Car {index} A 700",
        image_path=f"cars/{index:06d}.png",
        duplicate_group=None,
        quantity=1,
    )


def _completed(draft: GarageSnapshot, count: int) -> GarageSnapshot:
    now = datetime.now(UTC).isoformat()
    return replace(
        draft,
        completed=True,
        completed_at=now,
        cars=tuple(_car(index) for index in range(count)),
        expected_count=count,
        observed_count=count,
    )


def _promote_count(store: GarageStore, count: int, *, confirm_count_drop: bool = False) -> None:
    draft = store.create_draft(ScanOptions(record_video=False, preserve_duplicates=True))
    store.write_draft(_completed(draft, count))
    store.promote_draft(confirm_count_drop=confirm_count_drop)


def test_store_keeps_current_and_one_backup(tmp_path: Path) -> None:
    store = GarageStore(tmp_path)

    for count in (100, 101, 102):
        _promote_count(store, count)

    assert len(store.load_slot("current").cars) == 102
    assert len(store.load_slot("backup").cars) == 101
    assert not (tmp_path / ".retiring").exists()
    assert not (tmp_path / ".rotation.json").exists()


def test_store_refuses_abnormal_count_drop(tmp_path: Path) -> None:
    store = GarageStore(tmp_path)
    _promote_count(store, 100)
    draft = store.create_draft(ScanOptions(record_video=False, preserve_duplicates=True))
    store.write_draft(_completed(draft, 60))

    with pytest.raises(GarageCountDropWarning):
        store.promote_draft()

    assert len(store.load_slot("current").cars) == 100
    assert len(store.load_slot("draft").cars) == 60


def test_store_rejects_incomplete_draft(tmp_path: Path) -> None:
    store = GarageStore(tmp_path)
    store.create_draft(ScanOptions(record_video=True, preserve_duplicates=False))

    with pytest.raises(GarageInvalidDraftError):
        store.promote_draft()

    assert store.load_slot("current") is None
