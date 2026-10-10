from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from forza_tracker.garage.export import export_fhstats_package
from forza_tracker.garage.models import GarageCar, GarageSnapshot, ScanOptions


def _car(
    source: Path,
    index: int,
    *,
    canonical_id: str | None,
    confidence: str,
) -> GarageCar:
    Image.new("RGB", (80, 40), (index * 30, 50, 80)).save(source)
    return GarageCar(
        capture_index=index,
        display_name="1992 Nissan Skyline GT-R" if canonical_id else "Unknown OCR",
        year=1992 if canonical_id else None,
        make="Nissan" if canonical_id else None,
        model="Skyline GT-R" if canonical_id else None,
        car_class="A",
        performance_index=700,
        canonical_id=canonical_id,
        confidence=confidence,
        raw_ocr="Unknown OCR" if canonical_id is None else "1992 Nissan Skyline GT-R",
        image_path=str(source),
        duplicate_group=None,
    )


def test_export_uses_one_confirmed_image_per_model(tmp_path: Path) -> None:
    cars = (
        _car(tmp_path / "second.png", 2, canonical_id="4114", confidence="confirmed"),
        _car(tmp_path / "first.png", 1, canonical_id="4114", confidence="confirmed"),
        _car(tmp_path / "unknown.png", 3, canonical_id=None, confidence="review"),
    )
    snapshot = GarageSnapshot(
        scan_id="scan-export",
        created_at=datetime.now(UTC).isoformat(),
        completed_at=datetime.now(UTC).isoformat(),
        completed=True,
        options=ScanOptions(),
        cars=cars,
        expected_count=3,
        observed_count=3,
    )

    package = export_fhstats_package(snapshot, tmp_path / "export")

    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    unresolved = json.loads((package / "unresolved.json").read_text(encoding="utf-8"))
    assert [item["canonical_id"] for item in manifest["cars"]] == ["4114"]
    assert manifest["cars"][0]["capture_index"] == 1
    assert len(list((package / "originals").glob("*.png"))) == 1
    assert unresolved[0]["confidence"] == "review"
