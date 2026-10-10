from __future__ import annotations

import json
from pathlib import Path

import forza_tracker.car_catalog.fh6 as fh6_catalog
from forza_tracker.car_catalog.fh6 import load_fh6_car_catalog, lookup_fh6_car


RC4_CARS = {
    1283: "1957 Ford Thunderbird",
    1297: "1988 Mitsubishi Starion ESI-R",
    1592: "1974 Toyota Celica GT",
    2866: "2018 Exomotive Exocet Sport V8 XP-5",
    3282: "1987 Porsche #203 Porsche AG 961",
    3494: "1968 Alfa Romeo Autodelta Tipo 33/2 Daytona",
    3937: "1970 Honda N600",
    3954: "2024 Chevrolet Camaro ZL1",
    4238: "1983 Nissan Skyline 2000 Turbo RS",
}


def test_load_fh6_car_catalog_reads_semicolon_csv(tmp_path: Path) -> None:
    catalog_path = tmp_path / "fh6_car_ordinals.csv"
    catalog_path.write_text(
        "\n".join(
            [
                "car_id;display_name;confidence",
                "4114;1992 Nissan Skyline GT-R;confirmed-by-user-and-game-asset",
            ]
        ),
        encoding="utf-8",
    )

    catalog = load_fh6_car_catalog(catalog_path)

    assert catalog[4114].display_name == "1992 Nissan Skyline GT-R"
    assert catalog[4114].confidence == "confirmed-by-user-and-game-asset"


def test_lookup_fh6_car_returns_confirmed_entries_from_canonical_catalog() -> None:
    skyline = lookup_fh6_car(4114)
    warthog = lookup_fh6_car(2574)

    assert skyline is not None
    assert skyline.display_name == "1992 Nissan Skyline GT-R"
    assert skyline.confidence == "confirmed-by-user-and-game-asset"
    assert warthog is not None
    assert warthog.display_name == "2015 343 Industries Warthog"


def test_lookup_fh6_car_returns_observed_telemetry_entries() -> None:
    crx = lookup_fh6_car(2119)
    camaro = lookup_fh6_car(1064)

    assert crx is not None
    assert crx.display_name == "1984 Honda CRXMugen"
    assert camaro is not None
    assert camaro.display_name == "1979 Chevrolet Camaro Z 28"


def test_lookup_fh6_car_returns_none_for_unknown_id() -> None:
    assert lookup_fh6_car(999999) is None


def test_rc4_catalog_contains_verified_game_asset_identities() -> None:
    catalog = load_fh6_car_catalog()

    assert {car_id: catalog[car_id].display_name for car_id in RC4_CARS} == RC4_CARS
    assert all(
        catalog[car_id].confidence == "confirmed-by-game-asset-and-official-list"
        for car_id in RC4_CARS
    )


def test_rc4_rivals_image_aliases_target_exact_car_ids() -> None:
    aliases_path = Path(__file__).parents[1] / "data" / "canonical" / "cars" / "fh6_rivals_image_aliases.json"
    aliases = json.loads(aliases_path.read_text(encoding="utf-8"))

    assert {name: aliases[name] for name in RC4_CARS.values()} == {
        name: car_id for car_id, name in RC4_CARS.items()
    }


def test_missing_default_catalog_degrades_to_an_empty_lookup(tmp_path: Path, monkeypatch) -> None:
    missing_catalog = tmp_path / "missing-fh6-car-ordinals.csv"
    monkeypatch.setattr(fh6_catalog, "FH6_CAR_CATALOG_PATH", missing_catalog)

    assert load_fh6_car_catalog(missing_catalog) == {}


def test_rivals_image_aliases_keep_ambiguous_editions_on_their_exact_car_ids() -> None:
    aliases_path = Path(__file__).parents[1] / "data" / "canonical" / "cars" / "fh6_rivals_image_aliases.json"
    aliases = json.loads(aliases_path.read_text(encoding="utf-8"))

    assert aliases["#4402 Ultra 4"] == 3603
    assert aliases["AM DB5"] == 1105
    assert aliases["Alfa Romeo TZ2"] == 2161
    assert aliases["Cadillac CT4-V"] == 3719
    assert aliases["Datsun 510"] == 1104
    assert aliases["Ferrari FXX-K WP"] == 4264
    assert aliases["Lancia Delta S4"] == 1661
    assert aliases["Lexus LFA FE"] == 4158
    assert aliases["MB 190 E FE"] == 4169
    assert aliases["Mazda 3"] == 1221
    assert aliases["Mit. Evo. TME"] == 4090
