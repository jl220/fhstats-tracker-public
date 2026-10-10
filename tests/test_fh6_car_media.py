from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from PIL import Image

from forza_tracker.car_catalog.media import (
    CanonicalCarName,
    CarMediaArtifact,
    CarMediaSource,
    CarMediaInventoryError,
    convert_thumbnails_batch,
    convert_thumbnail,
    discover_fh6_thumbnail_archives,
    inventory_fh6_thumbnails,
    load_car_names,
    validate_catalog_coverage,
    write_web_derivative,
)
from scripts.build_fh6_car_media import _detect_game_version, _source_fingerprint, main


RC4_IDS = {1283, 1297, 1592, 2866, 3282, 3494, 3937, 3954, 4238}


def _write_archives(
    content_root: Path,
    entries_by_archive: dict[str, dict[str, bytes]],
) -> None:
    stripped = content_root / "media" / "stripped"
    stripped.mkdir(parents=True)
    for archive_name, entries in entries_by_archive.items():
        with ZipFile(stripped / archive_name, "w", ZIP_DEFLATED) as archive:
            for entry_name, payload in entries.items():
                archive.writestr(entry_name, payload)


def test_discover_fh6_thumbnail_archives_orders_numeric_rc_names(tmp_path: Path) -> None:
    _write_archives(
        tmp_path,
        {
            "RC4HiRes.zip": {},
            "RC0HiRes.zip": {},
            "RC2HiRes.zip": {},
            "RC1HiRes.zip": {},
            "RC3HiRes.zip": {},
        },
    )

    assert discover_fh6_thumbnail_archives(tmp_path) == (
        "RC0HiRes.zip",
        "RC1HiRes.zip",
        "RC2HiRes.zip",
        "RC3HiRes.zip",
        "RC4HiRes.zip",
    )


def test_discover_fh6_thumbnail_archives_rejects_gap(tmp_path: Path) -> None:
    _write_archives(tmp_path, {"RC0HiRes.zip": {}, "RC2HiRes.zip": {}})

    with pytest.raises(CarMediaInventoryError, match="RC1HiRes.zip"):
        discover_fh6_thumbnail_archives(tmp_path)


def test_discover_fh6_thumbnail_archives_requires_rc0(tmp_path: Path) -> None:
    _write_archives(tmp_path, {"RC1HiRes.zip": {}})

    with pytest.raises(CarMediaInventoryError, match="RC0HiRes.zip"):
        discover_fh6_thumbnail_archives(tmp_path)


def test_inventory_fh6_thumbnails_sorts_ids_and_hashes_payloads(tmp_path: Path) -> None:
    _write_archives(
        tmp_path,
        {
            "RC0HiRes.zip": {"Thumbnail_2470_Big.swatchbin": b"first"},
            "RC1HiRes.zip": {"folder/thumbnail_3198_big.swatchbin": b"second"},
        },
    )

    result = inventory_fh6_thumbnails(tmp_path)

    assert [item.car_id for item in result] == [2470, 3198]
    assert result[0].archive == "RC0HiRes.zip"
    assert result[0].entry == "Thumbnail_2470_Big.swatchbin"
    assert result[0].size == 5
    assert result[0].sha256 == hashlib.sha256(b"first").hexdigest()


def test_inventory_fh6_thumbnails_rejects_duplicate_car_ids(tmp_path: Path) -> None:
    _write_archives(
        tmp_path,
        {
            "RC0HiRes.zip": {"Thumbnail_2470_Big.swatchbin": b"first"},
            "RC1HiRes.zip": {"Thumbnail_2470_Big.swatchbin": b"duplicate"},
        },
    )

    with pytest.raises(CarMediaInventoryError, match="2470"):
        inventory_fh6_thumbnails(tmp_path)


def test_inventory_fh6_thumbnails_rejects_malformed_thumbnail_name(tmp_path: Path) -> None:
    _write_archives(
        tmp_path,
        {"RC0HiRes.zip": {"Thumbnail_not-an-id_Big.swatchbin": b"broken"}},
    )

    with pytest.raises(CarMediaInventoryError, match="Thumbnail_not-an-id_Big.swatchbin"):
        inventory_fh6_thumbnails(tmp_path)


def test_inventory_cli_writes_an_atomic_json_report(tmp_path: Path) -> None:
    _write_archives(
        tmp_path,
        {"RC0HiRes.zip": {"Thumbnail_2470_Big.swatchbin": b"first"}},
    )
    report = tmp_path / "reports" / "inventory.json"

    exit_code = main(
        [
            "inventory",
            "--content-root",
            str(tmp_path),
            "--report",
            str(report),
        ]
    )

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["source_count"] == 1
    assert payload["cars"][0]["car_id"] == 2470
    assert not report.with_suffix(".json.tmp").exists()


def test_write_web_derivative_preserves_alpha_aspect_ratio_and_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Image.new("RGBA", (1024, 512), (0, 0, 0, 0))
    source.paste((220, 20, 60, 255), (160, 150, 860, 390))
    master = tmp_path / "2470.png"
    source.save(master)
    original_save = Image.Image.save
    webp_methods: list[int] = []

    def recording_save(image: Image.Image, fp: object, format: str | None = None, **params: object) -> None:
        if format == "WEBP":
            webp_methods.append(int(params["method"]))
        original_save(image, fp, format=format, **params)

    monkeypatch.setattr(Image.Image, "save", recording_save)

    artifact = write_web_derivative(2470, master, tmp_path / "web")

    output_path = tmp_path / "web" / Path(artifact.web_path).name
    with Image.open(output_path) as output:
        assert output.size == (640, 320)
        assert output.mode == "RGBA"
        assert output.getextrema()[3][0] == 0
    assert artifact.has_alpha is True
    assert artifact.web_size <= 120 * 1024
    assert artifact.oversize is False
    assert output_path.name == f"2470-{artifact.web_sha256[:8]}.webp"
    assert webp_methods and max(webp_methods) <= 4


def test_convert_thumbnail_extracts_to_staging_and_keeps_master(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"synthetic swatch"
    _write_archives(
        tmp_path,
        {"RC0HiRes.zip": {"Thumbnail_2470_Big.swatchbin": payload}},
    )
    source = CarMediaSource(
        car_id=2470,
        archive="RC0HiRes.zip",
        entry="Thumbnail_2470_Big.swatchbin",
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        swatch = Path(command[1])
        image = Image.new("RGBA", (800, 400), (0, 0, 0, 0))
        image.paste((12, 120, 240, 255), (100, 100, 700, 330))
        image.save(swatch.with_suffix(".dds"))
        return subprocess.CompletedProcess(command, 0, "converted", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    artifact = convert_thumbnail(
        source,
        texconv=tmp_path / "ForzaTools.TexConv.exe",
        content_root=tmp_path,
        master_root=tmp_path / "masters",
        web_root=tmp_path / "web",
    )

    assert commands[0][0] == str(tmp_path / "ForzaTools.TexConv.exe")
    assert commands[0][-1] == "-s"
    assert (tmp_path / "masters" / "2470.png").is_file()
    assert artifact.car_id == 2470
    assert artifact.has_alpha is True
    assert not (tmp_path / "masters" / ".staging").exists()


def test_sample_cli_selects_one_thumbnail_from_each_discovered_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_archives(
        tmp_path,
        {
            f"RC{index}HiRes.zip": {
                f"Thumbnail_{2000 + index}_Big.swatchbin": str(index).encode()
            }
            for index in range(5)
        },
    )
    tool_root = tmp_path / "tools"
    texconv = tool_root / "app" / "win-x64" / "ForzaTools.TexConv.exe"
    texconv.parent.mkdir(parents=True)
    texconv.touch()
    converted: list[int] = []

    def fake_convert(source: CarMediaSource, **_: object) -> CarMediaArtifact:
        converted.append(source.car_id)
        return CarMediaArtifact(
            car_id=source.car_id,
            width=640,
            height=320,
            has_alpha=True,
            master_sha256="a" * 64,
            web_path=f"/images/cars/fh6/{source.car_id}-deadbeef.webp",
            web_size=50_000,
            web_sha256="b" * 64,
            oversize=False,
        )

    monkeypatch.setattr("scripts.build_fh6_car_media.convert_thumbnail", fake_convert)

    exit_code = main(
        [
            "sample",
            "--content-root",
            str(tmp_path),
            "--tool-root",
            str(tool_root),
            "--output-root",
            str(tmp_path / "output"),
        ]
    )

    report = json.loads((tmp_path / "output" / "sample-report.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert converted == [2000, 2001, 2002, 2003, 2004]
    assert report["artifact_count"] == 5


def test_installed_fh6_inventory_contains_complete_rc4_pack() -> None:
    content_root = Path(r"C:\XboxGames\Forza Horizon 6\Content")
    if not content_root.is_dir():
        pytest.skip("FH6 Xbox app content is not installed on this machine")

    sources = inventory_fh6_thumbnails(content_root)

    assert len(sources) >= 660
    assert RC4_IDS <= {source.car_id for source in sources}
    assert {source.archive for source in sources if source.car_id in RC4_IDS} == {"RC4HiRes.zip"}


def test_load_car_names_reads_the_canonical_semicolon_catalog(tmp_path: Path) -> None:
    catalog = tmp_path / "cars.csv"
    catalog.write_text(
        "car_id;display_name;confidence\n2470;2020 Example Car;confirmed\n",
        encoding="utf-8",
    )

    names = load_car_names(catalog)

    assert names[2470] == CanonicalCarName(2470, "2020 Example Car", "confirmed")


def test_validate_catalog_coverage_reports_missing_ids_and_rivals_names() -> None:
    sources = (
        CarMediaSource(2470, "RC0HiRes.zip", "one", 1, "a" * 64),
        CarMediaSource(3198, "RC1HiRes.zip", "two", 1, "b" * 64),
    )
    names = {2470: CanonicalCarName(2470, "2020 Example Car", "confirmed")}

    report = validate_catalog_coverage(
        sources,
        names,
        {"example car": 2470},
        {"Example Car", "Missing Rival"},
    )

    assert report.missing_catalog_ids == (3198,)
    assert report.unresolved_rivals_names == ("Missing Rival",)
    assert report.is_complete is False


def test_validate_catalog_coverage_rejects_normalized_alias_collisions() -> None:
    sources = (
        CarMediaSource(2470, "RC0HiRes.zip", "one", 1, "a" * 64),
        CarMediaSource(3198, "RC1HiRes.zip", "two", 1, "b" * 64),
    )
    names = {
        2470: CanonicalCarName(2470, "First Car", "confirmed"),
        3198: CanonicalCarName(3198, "Second Car", "confirmed"),
    }

    report = validate_catalog_coverage(
        sources,
        names,
        {"Car Name": 2470, "car-name": 3198},
        {"Car Name"},
    )

    assert report.ambiguous_aliases == ("car name",)
    assert report.is_complete is False


def test_source_fingerprint_is_stable_and_sensitive_to_sources() -> None:
    sources = (
        CarMediaSource(2470, "RC0HiRes.zip", "one", 1, "a" * 64),
        CarMediaSource(3198, "RC1HiRes.zip", "two", 2, "b" * 64),
    )

    first = _source_fingerprint(sources)
    reordered = _source_fingerprint(tuple(reversed(sources)))
    changed = _source_fingerprint((sources[0], CarMediaSource(3198, "RC1HiRes.zip", "two", 3, "b" * 64)))

    assert first == reordered
    assert first != changed


def test_detect_game_version_reads_microsoft_game_config(tmp_path: Path) -> None:
    (tmp_path / "MicrosoftGame.Config").write_text(
        '<Game configVersion="1"><Identity Name="Microsoft.ForteBaseGame" Version="3.403.798.0" /></Game>',
        encoding="utf-8",
    )

    assert _detect_game_version(tmp_path) == "3.403.798.0"


def test_convert_thumbnails_batch_starts_texconv_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_archives(
        tmp_path,
        {
            "RC0HiRes.zip": {"Thumbnail_2470_Big.swatchbin": b"first"},
            "RC1HiRes.zip": {"Thumbnail_3198_Big.swatchbin": b"second"},
        },
    )
    sources = inventory_fh6_thumbnails(tmp_path)
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        for swatch in Path(command[1]).glob("*.swatchbin"):
            image = Image.new("RGBA", (800, 400), (0, 0, 0, 0))
            image.paste((80, 160, 240, 255), (100, 100, 700, 330))
            image.save(swatch.with_suffix(".dds"))
        return subprocess.CompletedProcess(command, 0, "converted", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    artifacts = convert_thumbnails_batch(
        sources,
        texconv=tmp_path / "ForzaTools.TexConv.exe",
        content_root=tmp_path,
        master_root=tmp_path / "masters",
        web_root=tmp_path / "web",
    )

    assert len(calls) == 1
    assert calls[0][-1] == "-s"
    assert [artifact.car_id for artifact in artifacts] == [2470, 3198]


def test_build_cli_writes_canonical_and_web_manifests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_archives(
        tmp_path,
        {
            archive: {f"Thumbnail_{2000 + index}_Big.swatchbin": archive.encode()}
            for index, archive in enumerate(
                ("RC0HiRes.zip", "RC1HiRes.zip", "RC2HiRes.zip", "RC3HiRes.zip")
            )
        },
    )
    catalog = tmp_path / "cars.csv"
    catalog.write_text(
        "car_id;display_name;confidence\n"
        + "".join(f"{2000 + index};Car {index};confirmed\n" for index in range(4)),
        encoding="utf-8",
    )
    aliases = tmp_path / "aliases.json"
    aliases.write_text(json.dumps({f"Car {index}": 2000 + index for index in range(4)}), encoding="utf-8")
    texconv = tmp_path / "tools" / "app" / "win-x64" / "ForzaTools.TexConv.exe"
    texconv.parent.mkdir(parents=True)
    texconv.write_bytes(b"tool")

    def fake_batch(
        sources: tuple[CarMediaSource, ...],
        *,
        web_root: Path,
        **_: object,
    ) -> tuple[CarMediaArtifact, ...]:
        payload = b"x" * 20_000
        artifacts = tuple(
            CarMediaArtifact(
                source.car_id,
                640,
                320,
                True,
                "a" * 64,
                f"/images/cars/fh6/{source.car_id}-deadbeef.webp",
                20_000,
                hashlib.sha256(payload).hexdigest(),
                False,
            )
            for source in sources
        )
        web_root.mkdir(parents=True, exist_ok=True)
        for artifact in artifacts:
            (web_root / Path(artifact.web_path).name).write_bytes(payload)
        return artifacts

    monkeypatch.setattr("scripts.build_fh6_car_media.convert_thumbnails_batch", fake_batch)
    canonical_manifest = tmp_path / "canonical.json"
    web_root = tmp_path / "web"
    stale_image = web_root / "public" / "images" / "cars" / "fh6" / "2000-stale.webp"
    stale_image.parent.mkdir(parents=True)
    stale_image.write_bytes(b"stale")

    exit_code = main(
        [
            "build",
            "--content-root", str(tmp_path),
            "--catalog", str(catalog),
            "--aliases", str(aliases),
            "--tool-root", str(tmp_path / "tools"),
            "--output-root", str(tmp_path / "output"),
            "--canonical-manifest", str(canonical_manifest),
            "--web-root", str(web_root),
            "--game-version", "test-build-1",
        ]
    )

    canonical = json.loads(canonical_manifest.read_text(encoding="utf-8"))
    web = json.loads((web_root / "data" / "fh6-car-media.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert canonical["source_count"] == 4
    assert canonical["game_version"] == "test-build-1"
    assert canonical["generated_at"].endswith("+00:00")
    assert len(canonical["source_fingerprint"]) == 64
    assert web["totalWebBytes"] == 80_000
    assert len(web["cars"]) == 4
    assert (web_root / "data" / "fh6-rivals-image-aliases.json").is_file()
    assert not stale_image.exists()


def test_build_preserves_previous_web_manifest_and_assets_when_publish_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_archives(
        tmp_path,
        {
            archive: {f"Thumbnail_{2000 + index}_Big.swatchbin": archive.encode()}
            for index, archive in enumerate(
                ("RC0HiRes.zip", "RC1HiRes.zip", "RC2HiRes.zip", "RC3HiRes.zip")
            )
        },
    )
    catalog = tmp_path / "cars.csv"
    catalog.write_text(
        "car_id;display_name;confidence\n"
        + "".join(f"{2000 + index};Car {index};confirmed\n" for index in range(4)),
        encoding="utf-8",
    )
    aliases = tmp_path / "aliases.json"
    aliases.write_text(json.dumps({f"Car {index}": 2000 + index for index in range(4)}), encoding="utf-8")
    texconv = tmp_path / "tools" / "app" / "win-x64" / "ForzaTools.TexConv.exe"
    texconv.parent.mkdir(parents=True)
    texconv.write_bytes(b"tool")
    web_root = tmp_path / "web"
    image_root = web_root / "public" / "images" / "cars" / "fh6"
    image_root.mkdir(parents=True)
    previous_asset = image_root / "2000-previous.webp"
    previous_asset.write_bytes(b"previous")
    previous_manifest = web_root / "data" / "fh6-car-media.json"
    previous_manifest.parent.mkdir(parents=True)
    previous_manifest.write_text('{"previous":true}\n', encoding="utf-8")

    def fake_batch(
        sources: tuple[CarMediaSource, ...],
        *,
        web_root: Path,
        **_: object,
    ) -> tuple[CarMediaArtifact, ...]:
        artifacts = []
        web_root.mkdir(parents=True, exist_ok=True)
        for source in sources:
            payload = f"image-{source.car_id}".encode()
            web_path = f"/images/cars/fh6/{source.car_id}-new.webp"
            (web_root / Path(web_path).name).write_bytes(payload)
            artifacts.append(
                CarMediaArtifact(
                    source.car_id,
                    640,
                    320,
                    True,
                    "a" * 64,
                    web_path,
                    len(payload),
                    hashlib.sha256(payload).hexdigest(),
                    False,
                )
            )
        return tuple(artifacts)

    monkeypatch.setattr("scripts.build_fh6_car_media.convert_thumbnails_batch", fake_batch)
    from scripts import build_fh6_car_media

    original_write = build_fh6_car_media._write_json_atomic

    def fail_web_manifest(path: Path, payload: object) -> None:
        if path == previous_manifest:
            raise OSError("simulated manifest publish failure")
        original_write(path, payload)

    monkeypatch.setattr(build_fh6_car_media, "_write_json_atomic", fail_web_manifest)

    with pytest.raises(OSError, match="simulated manifest"):
        main(
            [
                "build",
                "--content-root", str(tmp_path),
                "--catalog", str(catalog),
                "--aliases", str(aliases),
                "--tool-root", str(tmp_path / "tools"),
                "--output-root", str(tmp_path / "output"),
                "--canonical-manifest", str(tmp_path / "canonical.json"),
                "--web-root", str(web_root),
                "--game-version", "test-build-1",
            ]
        )

    assert previous_manifest.read_text(encoding="utf-8") == '{"previous":true}\n'
    assert previous_asset.read_bytes() == b"previous"
