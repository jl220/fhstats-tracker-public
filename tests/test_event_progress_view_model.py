from __future__ import annotations

import json
from pathlib import Path

from forza_tracker.localization import configure_language
from forza_tracker.ui.event_progress_view_model import build_event_progress_rows, format_event_times


def test_event_times_keep_total_and_best_lap_compact() -> None:
    configure_language("en")
    assert format_event_times("02:10,374", "00:51,220") == "02:10,374 · L 00:51,220"
    assert format_event_times("02:10,374", None) == "02:10,374"

    configure_language("es")
    assert format_event_times("02:10,374", "00:51,220") == "02:10,374 · V 00:51,220"
    configure_language("en")


def test_event_progress_rows_keep_each_slot_map(tmp_path: Path) -> None:
    _write_catalog(tmp_path)

    rows = build_event_progress_rows(
        [
            {"index": 1, "circuit": "Circuito de autopista", "discipline": "road"},
            {"index": 2, "circuit": "Circuito desconocido", "discipline": "road"},
        ],
        map_root=tmp_path,
    )

    assert rows[0].map_panel.status == "found"
    assert rows[1].map_panel.status == "missing"


def test_event_progress_rows_keep_empty_state_without_a_circuit() -> None:
    rows = build_event_progress_rows(
        [{"index": 1, "circuit": "", "discipline": "road"}],
    )

    assert rows[0].map_panel.status == "empty"


def test_event_progress_rows_do_not_guess_between_disciplines(tmp_path: Path) -> None:
    _write_ambiguous_catalog(tmp_path)

    rows = build_event_progress_rows(
        [
            {"index": 1, "circuit": "Tramo de Takashiro", "discipline": ""},
            {"index": 2, "circuit": "Tramo de Takashiro", "discipline": "dirt"},
        ],
        map_root=tmp_path,
    )

    assert rows[0].map_panel.status == "missing"
    assert rows[0].map_panel.image_path is None
    assert rows[1].map_panel.image_path == tmp_path / "images" / "dirt" / "takashiro-trail.png"


def test_event_progress_rows_translate_spanish_discipline_from_saved_history(tmp_path: Path) -> None:
    _write_ambiguous_catalog(tmp_path)

    rows = build_event_progress_rows(
        [{"index": 1, "circuit": "Tramo de Takashiro", "discipline": "tierra"}],
        map_root=tmp_path,
    )

    assert rows[0].map_panel.status == "found"
    assert rows[0].map_panel.image_path == tmp_path / "images" / "dirt" / "takashiro-trail.png"


def test_event_progress_rows_resolve_unique_map_by_name_without_discipline(tmp_path: Path) -> None:
    _write_catalog(tmp_path)

    rows = build_event_progress_rows(
        [{"index": 1, "circuit": "Circuito de autopista", "discipline": ""}],
        map_root=tmp_path,
    )

    assert rows[0].map_panel.status == "found"
    assert rows[0].map_panel.image_path == tmp_path / "images" / "road" / "highway-circuit.png"


def test_event_progress_rows_use_confirmed_recommendation_event_as_discipline_fallback(tmp_path: Path) -> None:
    _write_ambiguous_catalog(tmp_path)

    rows = build_event_progress_rows(
        [{"index": 1, "circuit": "Tramo de Takashiro", "discipline": ""}],
        recommendation_events=[
            {
                "requested_name": "Tramo de Takashiro",
                "name": "Tramo de Takashiro",
                "discipline": "dirt",
                "matched": True,
            }
        ],
        map_root=tmp_path,
    )

    assert rows[0].map_panel.status == "found"
    assert rows[0].map_panel.image_path == tmp_path / "images" / "dirt" / "takashiro-trail.png"


def _write_catalog(root: Path) -> None:
    images = root / "images" / "road"
    images.mkdir(parents=True)
    (images / "highway-circuit.png").write_bytes(b"png")
    (root / "metadata.json").write_text(
        json.dumps(
            [
                {
                    "id": "road:highway-circuit",
                    "discipline": "road",
                    "index": 1,
                    "slug": "highway-circuit",
                    "name_en": "Highway Circuit",
                    "name_es": "Circuito de autopista",
                    "image": "images/road/highway-circuit.png",
                    "source": "forza-rivals-small-thumbnail",
                }
            ]
        ),
        encoding="utf-8",
    )


def _write_ambiguous_catalog(root: Path) -> None:
    for discipline, image in (("road", "takashiro-sprint.png"), ("dirt", "takashiro-trail.png")):
        images = root / "images" / discipline
        images.mkdir(parents=True, exist_ok=True)
        (images / image).write_bytes(b"png")
    (root / "metadata.json").write_text(
        json.dumps(
            [
                {
                    "id": "road:takashiro-sprint",
                    "discipline": "road",
                    "index": 1,
                    "slug": "takashiro-sprint",
                    "name_es": "Tramo de Takashiro",
                    "image": "images/road/takashiro-sprint.png",
                    "source": "forza-rivals-small-thumbnail",
                },
                {
                    "id": "dirt:takashiro-trail",
                    "discipline": "dirt",
                    "index": 1,
                    "slug": "takashiro-trail",
                    "name_es": "Tramo de Takashiro",
                    "image": "images/dirt/takashiro-trail.png",
                    "source": "forza-rivals-small-thumbnail",
                },
            ]
        ),
        encoding="utf-8",
    )
