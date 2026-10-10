from __future__ import annotations

import json
from pathlib import Path

from forza_tracker.ui.history import (
    HistoryEntry,
    HistoryRepository,
    _extract_class,
    _extract_class_from_crop_path,
    build_race_history,
    summarize,
)


def test_history_enriches_championship_with_previous_race_car(tmp_path: Path) -> None:
    history_path = tmp_path / "results.jsonl"
    rows = [
        {"screen_type": "race_result", "position": 2, "car": "SUBARU BRZ", "car_class": "A 700", "timestamp": "2026-06-15T00:13:28+00:00"},
        {"screen_type": "championship_result", "timestamp": "2026-06-15T00:13:48+00:00"},
    ]
    history_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    entries = HistoryRepository(history_path).load()

    assert entries[-1].get("car") == "SUBARU BRZ"
    assert entries[-1].get("car_class") == "A 700"


def test_history_does_not_reuse_event_registration_title_as_circuit(tmp_path: Path) -> None:
    history_path = tmp_path / "results.jsonl"
    rows = [
        {
            "screen_type": "event_registration",
            "event_number": 3,
            "event_total": 3,
            "circuit_name": "Inscripción al evento",
            "event_plan": ["Circuito de tierra de Sekibe", "Tramo de Nukabira", "Tramo de Kinkaku-ji"],
            "timestamp": "2026-06-17T13:39:56+00:00",
        },
        {"screen_type": "starting_grid", "event_number": 3, "event_total": 3, "timestamp": "2026-06-17T13:40:10+00:00"},
    ]
    history_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")

    entries = HistoryRepository(history_path).load()

    assert entries[0].raw["circuit_name"] is None
    assert entries[1].get("circuit_name") == "-"


def test_championship_title_does_not_replace_the_race_circuit() -> None:
    races = build_race_history(
        [
            HistoryEntry(
                {
                    "screen_type": "starting_grid",
                    "event_number": 3,
                    "event_total": 3,
                    "circuit_name": "Carrera por el festival",
                }
            ),
            HistoryEntry(
                {
                    "screen_type": "race_result",
                    "event_number": 3,
                    "event_total": 3,
                    "circuit_name": "Carrera por el festival",
                    "position": 1,
                }
            ),
            HistoryEntry(
                {
                    "screen_type": "championship_result",
                    "event_number": 3,
                    "event_total": 3,
                    "circuit_name": "Campeonato iL.",
                    "championship_position": 1,
                }
            ),
        ]
    )

    assert races[-1].raw["circuit_name"] == "Carrera por el festival"


def test_summary_groups_races_by_car_and_class() -> None:
    entries = [
        {"screen_type": "race_result", "position": 1, "car": "SUBARU BRZ", "car_class": "A 700"},
        {"screen_type": "race_result", "position": 4, "car": "SUBARU BRZ", "car_class": "S1 900"},
        {"screen_type": "race_result", "position": 3, "car": "SUBARU BRZ", "car_class": "A 700"},
    ]

    summary = summarize([HistoryEntry(row) for row in entries])

    assert [(group.name, group.car_class, group.total_races) for group in summary.by_car] == [
        ("SUBARU BRZ", "A 700", 2),
        ("SUBARU BRZ", "S1 900", 1),
    ]


def test_summary_calculates_rates_car_usage_and_positions_gained() -> None:
    entries = [
        {"screen_type": "starting_grid", "starting_position": 8, "car": "SUBARU BRZ", "car_class": "A 700"},
        {"screen_type": "race_result", "position": 1, "car": "SUBARU BRZ", "car_class": "A 700"},
        {"screen_type": "starting_grid", "starting_position": 5, "car": "SUBARU BRZ", "car_class": "A 700"},
        {"screen_type": "race_result", "position": 3, "car": "SUBARU BRZ", "car_class": "A 700"},
        {"screen_type": "starting_grid", "starting_position": 2, "car": "HONDA BEAT", "car_class": "B 600"},
        {"screen_type": "race_result", "position": 5, "car": "HONDA BEAT", "car_class": "B 600"},
    ]

    summary = summarize([HistoryEntry(row) for row in entries])

    assert summary.total_races == 3
    assert round(summary.win_rate, 2) == 33.33
    assert round(summary.podium_rate, 2) == 66.67
    assert summary.avg_positions_gained == 2
    assert summary.best_comeback == 7
    assert summary.unique_cars == 2
    assert summary.favorite_car == "SUBARU BRZ"
    assert summary.favorite_class == "A 700"
    assert summary.by_car[0].avg_positions_gained == 4.5


def test_race_history_deduplicates_repeated_result_captures() -> None:
    entries = [
        HistoryEntry(
            {
                "screen_type": "starting_grid",
                "event_number": 1,
                "event_total": 3,
                "circuit_name": "Circuito de tierra de Sekibe",
                "starting_position": 5,
                "car": "SUBARU BRZ",
                "car_class": "A 700",
                "timestamp": "2026-06-17T18:00:00+00:00",
            }
        ),
        HistoryEntry(
            {
                "screen_type": "race_result",
                "event_number": 1,
                "event_total": 3,
                "circuit_name": "Circuito de tierra de Sekibe",
                "position": 2,
                "car": "SUBARU BRZ",
                "car_class": "A 700",
                "total_time": "03:12.500",
                "best_lap": "01:02.100",
                "timestamp": "2026-06-17T18:05:00+00:00",
                "screenshot_path": "captures/first.png",
            }
        ),
        HistoryEntry(
            {
                "screen_type": "race_result",
                "event_number": 1,
                "event_total": 3,
                "circuit_name": "Circuito de tierra de Sekibe",
                "position": 2,
                "car": "SUBARU BRZ",
                "car_class": "A 700",
                "total_time": "03:12.500",
                "best_lap": "01:02.100",
                "timestamp": "2026-06-17T18:05:09+00:00",
                "screenshot_path": "captures/latest.png",
            }
        ),
    ]

    races = build_race_history(entries)

    assert len(races) == 1
    assert races[0].raw["screenshot_path"] == "captures/latest.png"


def test_extract_class_accepts_compact_ocr_from_mclaren_crop() -> None:
    assert _extract_class("Si 800! 2021McLaren 62¢") == "S1 800"
    assert _extract_class("S1800202162") == "S1 800"
    assert _extract_class("Jolu4327 & McLaren 620R") is None


def test_extract_class_accepts_police_c_ocr_symbols() -> None:
    assert _extract_class("€ 500 2021 Polaris") == "C 500"
    assert _extract_class("¢5002021Polaris") == "C 500"
    assert _extract_class("Polaris RZR Pro") is None


def test_extract_class_from_existing_polaris_crop() -> None:
    crop = Path("captures/class_crops/starting_grid_20260616_153837_class.png")
    if crop.exists():
        assert _extract_class_from_crop_path(crop) == "C 500"
