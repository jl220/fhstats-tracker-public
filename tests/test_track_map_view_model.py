from __future__ import annotations

import json
from pathlib import Path

from forza_tracker.ui.track_map_view_model import build_track_map_panel


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


def test_track_map_panel_resolves_detected_circuit_in_english(tmp_path: Path) -> None:
    _write_catalog(tmp_path)

    panel = build_track_map_panel(
        "circuito autopista",
        discipline="road",
        root=tmp_path,
        language="en",
    )

    assert panel.status == "found"
    assert panel.title == "Highway Circuit"
    assert panel.subtitle == "Road · forza-rivals-small-thumbnail"
    assert panel.image_path == tmp_path / "images" / "road" / "highway-circuit.png"


def test_track_map_panel_resolves_detected_circuit_in_spanish(tmp_path: Path) -> None:
    _write_catalog(tmp_path)

    panel = build_track_map_panel(
        "Highway Circuit",
        discipline="road",
        root=tmp_path,
        language="es",
    )

    assert panel.status == "found"
    assert panel.title == "Circuito de autopista"
    assert panel.image_path == tmp_path / "images" / "road" / "highway-circuit.png"


def test_track_map_panel_has_empty_state_without_detected_circuit(tmp_path: Path) -> None:
    panel = build_track_map_panel(None, root=tmp_path)

    assert panel.status == "empty"
    assert panel.title == "Circuit map"
    assert panel.subtitle == "Waiting for a circuit detected by OCR or telemetry."
    assert panel.image_path is None


def test_track_map_panel_reports_missing_map_for_unknown_circuit(tmp_path: Path) -> None:
    _write_catalog(tmp_path)

    panel = build_track_map_panel("Circuito inventado", discipline="road", root=tmp_path)

    assert panel.status == "missing"
    assert panel.title == "Circuito inventado"
    assert panel.subtitle == "Local map not available yet."
    assert panel.image_path is None
