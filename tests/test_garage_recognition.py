from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from forza_tracker.garage.models import GarageCar
from forza_tracker.garage.models import GarageSnapshot, ScanOptions
from forza_tracker.garage.processor import process_draft, reprocess_snapshot_matches, save_alias
from forza_tracker.garage.recognition import (
    apply_duplicate_policy,
    card_text_to_catalog_query,
    match_catalog,
    normalize_car_text,
    parse_class_pi,
)


def _catalog(*names: str) -> dict[int, SimpleNamespace]:
    return {
        index: SimpleNamespace(car_ordinal=index, display_name=name, confidence="test")
        for index, name in enumerate(names, start=4114)
    }


def _car(canonical_id: str, index: int) -> GarageCar:
    return GarageCar(
        capture_index=index,
        display_name="1992 Nissan Skyline GT-R",
        year=1992,
        make="Nissan",
        model="Skyline GT-R",
        car_class="A",
        performance_index=700,
        canonical_id=canonical_id,
        confidence="confirmed",
        raw_ocr="",
        image_path=f"raw/{index:06d}-car.png",
        duplicate_group=None,
    )


def test_normalize_car_text_removes_accents_punctuation_and_spacing() -> None:
    assert normalize_car_text("  2016   ABARTH  695 Bipósto!! ") == "2016 abarth 695 biposto"


def test_parse_class_pi_reads_forza_classes() -> None:
    assert parse_class_pi("S1 800") == ("S1", 800)
    assert parse_class_pi("R\n998") == ("R", 998)


def test_card_text_is_reordered_to_catalog_format() -> None:
    parsed = card_text_to_catalog_query("INTEGRA A-SPEC\n2023 ACURA")

    assert parsed.query == "2023 ACURA INTEGRA A-SPEC"
    assert parsed.year == 2023
    assert parsed.make == "ACURA"
    assert parsed.model == "INTEGRA A-SPEC"


def test_card_text_strips_forza_visual_delimiters() -> None:
    parsed = card_text_to_catalog_query("| 695 BIPOSTO |\n2016 ABARTH")

    assert parsed.query == "2016 ABARTH 695 BIPOSTO"
    assert parsed.make == "ABARTH"
    assert parsed.model == "695 BIPOSTO"
    assert normalize_car_text(parsed.query) == "2016 abarth 695 biposto"


def test_exact_catalog_match_is_confirmed() -> None:
    result = match_catalog(
        "2023 Acura Integra A-Spec",
        _catalog("2023 Acura Integra A-Spec", "2001 Acura Integra Type R"),
        {},
    )

    assert result.canonical_id == "4114"
    assert result.confidence == "confirmed"


def test_exact_catalog_tokens_can_be_reordered() -> None:
    result = match_catalog(
        "2021 Toyota GR Yaris",
        _catalog("2021 Toyota Yaris GR", "2020 Toyota Yaris GR"),
        {},
    )

    assert result.canonical_id == "4114"
    assert result.confidence == "confirmed"


def test_exact_catalog_tokens_can_have_different_spacing() -> None:
    result = match_catalog(
        "2023 Porsche 911 GT3RS",
        _catalog("2023 Porsche 911 GT 3 RS", "2022 Porsche 911 GT 3 RS"),
        {},
    )

    assert result.canonical_id == "4114"
    assert result.confidence == "confirmed"


def test_unique_catalog_base_name_accepts_game_trim_suffix() -> None:
    result = match_catalog(
        "2023 Acura Integra A-Spec",
        _catalog("2023 Acura Integra", "2001 Acura Integra Type R"),
        {},
    )

    assert result.canonical_id == "4114"
    assert result.confidence == "confirmed"


def test_ambiguous_fuzzy_match_requires_review() -> None:
    result = match_catalog(
        "2023 Acura Integra A-Spec Type",
        _catalog("2023 Acura Integra A-Spec Type S", "2023 Acura Integra A-Spec Type R"),
        {},
    )

    assert result.canonical_id is None
    assert result.confidence == "review"


def test_alias_wins_before_fuzzy_matching() -> None:
    aliases = {"1992 nissan skyllne gt r": "4114"}
    result = match_catalog(
        "1992 Nissan Skyllne GT-R",
        _catalog("1992 Nissan Skyline GT-R", "1993 Nissan Skyline GT-R"),
        aliases,
    )

    assert result.canonical_id == "4114"
    assert result.confidence == "confirmed"


def test_group_duplicates_increments_quantity() -> None:
    grouped = apply_duplicate_policy([_car("4114", 1), _car("4114", 2)], preserve_duplicates=False)

    assert len(grouped) == 1
    assert grouped[0].quantity == 2
    assert grouped[0].duplicate_group == "4114"


def test_preserve_duplicates_keeps_every_capture() -> None:
    cars = [_car("4114", 1), replace(_car("4114", 2), display_name="Same model, other livery")]

    preserved = apply_duplicate_policy(cars, preserve_duplicates=True)

    assert len(preserved) == 2
    assert [car.capture_index for car in preserved] == [1, 2]


def test_process_draft_recognizes_saved_capture_after_navigation(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    image = np.full((80, 160, 3), 220, dtype=np.uint8)
    for suffix in ("car", "name"):
        assert cv2.imwrite(str(raw_dir / f"000001-{suffix}.png"), image)
    pi_image = np.zeros((40, 100, 3), dtype=np.uint8)
    pi_image[:, :40] = (0, 200, 255)
    assert cv2.imwrite(str(raw_dir / "000001-pi.png"), pi_image)
    (raw_dir / "captures.json").write_text(
        json.dumps(
            [
                {
                    "capture_index": 1,
                    "car_image": "000001-car.png",
                    "name_image": "000001-name.png",
                    "class_pi_image": "000001-pi.png",
                }
            ]
        ),
        encoding="utf-8",
    )
    snapshot = GarageSnapshot(
        scan_id="scan-1",
        created_at=datetime.now(UTC).isoformat(),
        completed_at=None,
        completed=False,
        options=ScanOptions(record_video=False, preserve_duplicates=True),
        expected_count=1,
        observed_count=1,
    )
    responses = iter(["INTEGRA A-SPEC\n2023 ACURA"] * 3 + ["484"] * 3)

    completed = process_draft(
        snapshot,
        tmp_path,
        _catalog("2023 Acura Integra A-Spec"),
        {},
        lambda _image, _psm: next(responses),
    )

    assert completed.completed is True
    assert completed.completed_at is not None
    assert completed.observed_count == 1
    assert completed.cars[0].canonical_id == "4114"
    assert completed.cars[0].car_class == "C"
    assert completed.cars[0].performance_index == 484
    assert completed.cars[0].image_path == "raw/000001-car.png"


def test_reprocess_snapshot_reuses_saved_raw_ocr_without_recapturing() -> None:
    snapshot = GarageSnapshot(
        scan_id="scan-real",
        created_at=datetime.now(UTC).isoformat(),
        completed_at=datetime.now(UTC).isoformat(),
        completed=True,
        options=ScanOptions(record_video=True, preserve_duplicates=True),
        cars=(
            GarageCar(
                capture_index=1,
                display_name="2016 ABARTH | 695 BIPOSTO |",
                year=2016,
                make="ABARTH",
                model="| 695 BIPOSTO |",
                car_class="B",
                performance_index=540,
                canonical_id=None,
                confidence="review",
                raw_ocr="| 695 BIPOSTO |\n2016 ABARTH\nB 540",
                image_path="raw/000001-car.png",
                duplicate_group=None,
            ),
        ),
        expected_count=1,
        observed_count=1,
    )

    reprocessed = reprocess_snapshot_matches(
        snapshot,
        _catalog("2016 Abarth 695 Biposto"),
        {},
    )

    assert reprocessed.cars[0].canonical_id == "4114"
    assert reprocessed.cars[0].confidence == "confirmed"
    assert reprocessed.cars[0].display_name == "2016 Abarth 695 Biposto"
    assert reprocessed.cars[0].model == "695 BIPOSTO"
    assert reprocessed.cars[0].performance_index == 540


def test_save_alias_normalizes_key_and_preserves_existing_entries(tmp_path: Path) -> None:
    path = tmp_path / "aliases.json"
    path.write_text('{"old alias": "1"}', encoding="utf-8")

    aliases = save_alias(path, "1992 Nissan Skyllne GT-R", "4114")

    assert aliases == {"old alias": "1", "1992 nissan skyllne gt r": "4114"}
    assert json.loads(path.read_text(encoding="utf-8")) == aliases
