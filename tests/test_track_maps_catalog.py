from __future__ import annotations

import json
from pathlib import Path

from forza_tracker.track_maps.catalog import find_track_map, load_track_maps


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
                    "aliases": ["Autopista Circuit"],
                    "image": "images/road/highway-circuit.png",
                    "source": "forza-rivals-small-thumbnail",
                    "crop": {"x": 483, "y": 967, "width": 314, "height": 196},
                    "captured_at": "2026-07-12T21:34:35+00:00",
                }
            ]
        ),
        encoding="utf-8",
    )


def test_load_track_maps_resolves_local_image_path(tmp_path: Path) -> None:
    _write_catalog(tmp_path)

    maps = load_track_maps(tmp_path)

    assert len(maps) == 1
    assert maps[0].id == "road:highway-circuit"
    assert maps[0].display_name("en") == "Highway Circuit"
    assert maps[0].display_name("es") == "Circuito de autopista"
    assert maps[0].aliases == ("Autopista Circuit",)
    assert maps[0].image_path == tmp_path / "images" / "road" / "highway-circuit.png"
    assert maps[0].image_path.exists()


def test_find_track_map_matches_ocr_name_slug_and_discipline(tmp_path: Path) -> None:
    _write_catalog(tmp_path)

    by_name = find_track_map("circuito autopista", discipline="road", root=tmp_path)
    by_slug = find_track_map("Highway Circuit", root=tmp_path)
    wrong_discipline = find_track_map("Circuito de autopista", discipline="dirt", root=tmp_path)

    assert by_name is not None
    assert by_name.slug == "highway-circuit"
    assert by_slug is not None
    assert by_slug.id == "road:highway-circuit"
    assert wrong_discipline is None


def test_find_track_map_does_not_choose_between_duplicate_names_without_discipline(tmp_path: Path) -> None:
    _write_catalog(tmp_path)
    images = tmp_path / "images" / "dirt"
    images.mkdir(parents=True)
    (images / "highway-circuit.png").write_bytes(b"png")
    metadata_path = tmp_path / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata.append(
        {
            "id": "dirt:highway-circuit",
            "discipline": "dirt",
            "index": 1,
            "slug": "highway-circuit",
            "name_es": "Circuito de autopista",
            "image": "images/dirt/highway-circuit.png",
            "source": "forza-rivals-small-thumbnail",
        }
    )
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    assert find_track_map("Circuito de autopista", root=tmp_path) is None


def test_find_track_map_matches_route_type_variants_by_distinctive_name(tmp_path: Path) -> None:
    _write_catalog(tmp_path)
    images = tmp_path / "images" / "cross_country"
    images.mkdir(parents=True)
    (images / "takashiro-cross-country.png").write_bytes(b"png")
    metadata_path = tmp_path / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata.append(
        {
            "id": "cross-country:takashiro-cross-country",
            "discipline": "cross-country",
            "index": 11,
            "slug": "takashiro-cross-country",
            "name_en": "Takashiro Cross Country",
            "name_es": "Campo a traves de Takashiro",
            "image": "images/cross_country/takashiro-cross-country.png",
            "source": "forza-rivals-small-thumbnail",
        }
    )
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    track_map = find_track_map("Tramo de Takashiro", discipline="cross-country", root=tmp_path)

    assert track_map is not None
    assert track_map.slug == "takashiro-cross-country"


def test_find_track_map_ignores_generic_discipline_words_between_same_discipline_maps(tmp_path: Path) -> None:
    _write_catalog(tmp_path)
    images = tmp_path / "images" / "dirt"
    images.mkdir(parents=True)
    (images / "chiheisen-scramble.png").write_bytes(b"png")
    (images / "kawazu-nanadaru-scramble.png").write_bytes(b"png")
    metadata_path = tmp_path / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata.extend(
        [
            {
                "id": "dirt:chiheisen-scramble",
                "discipline": "dirt",
                "index": 1,
                "slug": "chiheisen-scramble",
                "name_es": "Circuito de tierra de Chiheisen",
                "image": "images/dirt/chiheisen-scramble.png",
                "source": "forza-rivals-small-thumbnail",
            },
            {
                "id": "dirt:kawazu-nanadaru-scramble",
                "discipline": "dirt",
                "index": 2,
                "slug": "kawazu-nanadaru-scramble",
                "name_es": "Circuito de tierra de Kawazu Nanadaru",
                "image": "images/dirt/kawazu-nanadaru-scramble.png",
                "source": "forza-rivals-small-thumbnail",
            },
        ]
    )
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    track_map = find_track_map("Circuito de tierra de Kawazu Nanadaru", discipline="dirt", root=tmp_path)

    assert track_map is not None
    assert track_map.slug == "kawazu-nanadaru-scramble"


def test_find_track_map_returns_none_when_image_is_missing(tmp_path: Path) -> None:
    _write_catalog(tmp_path)
    (tmp_path / "images" / "road" / "highway-circuit.png").unlink()

    assert find_track_map("Circuito de autopista", root=tmp_path) is None


def test_packaged_track_maps_catalog_contains_all_current_rivals_events() -> None:
    maps = load_track_maps()

    assert len(maps) == 77
    assert find_track_map("Highway Circuit") is not None
    assert find_track_map("Tokyo City Docks Charge") is not None


def test_every_packaged_track_resolves_spanish_english_and_slug_to_same_image() -> None:
    maps = load_track_maps()

    assert len(maps) == 77
    for track in maps:
        assert track.name_es, track.id
        assert track.name_en, track.id
        for candidate in (track.name_es, track.name_en, track.slug):
            resolved = find_track_map(candidate, discipline=track.discipline)
            assert resolved is not None, (track.id, candidate)
            assert resolved.id == track.id
            assert resolved.image_path == track.image_path


def test_corrected_english_track_names_keep_legacy_aliases() -> None:
    expected = {
        "Narai-juku Circuit": "Narai-Juku Circuit",
        "Legendary Island Circuit": "Legend Island Circuit",
        "Tokyo Train Sprint": "Tokyo Railway Sprint",
        "Tayaki Scramble": "Taiyaki Scramble",
        "Cherry Fields Trail": "Cherry Field Trail",
    }

    for legacy, canonical in expected.items():
        resolved = find_track_map(legacy)
        assert resolved is not None, legacy
        assert resolved.name_en == canonical
