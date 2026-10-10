from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TRACKER_SRC = REPOSITORY_ROOT / "apps" / "tracker-desktop" / "src"
if str(TRACKER_SRC) not in sys.path:
    sys.path.insert(0, str(TRACKER_SRC))

from forza_tracker.car_catalog.media import (  # noqa: E402
    CarMediaArtifact,
    CarMediaSource,
    convert_thumbnail,
    convert_thumbnails_batch,
    discover_fh6_thumbnail_archives,
    inventory_fh6_thumbnails,
    load_car_names,
    validate_catalog_coverage,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build FH6 official car media assets.")
    commands = parser.add_subparsers(dest="command", required=True)
    inventory = commands.add_parser("inventory", help="Inventory stock thumbnails in the installed game.")
    inventory.add_argument("--content-root", type=Path, required=True)
    inventory.add_argument("--report", type=Path, required=True)
    sample = commands.add_parser("sample", help="Convert one stock thumbnail from each FH6 archive.")
    sample.add_argument("--content-root", type=Path, required=True)
    sample.add_argument("--tool-root", type=Path, required=True)
    sample.add_argument("--output-root", type=Path, required=True)
    build = commands.add_parser("build", help="Build the complete canonical and web FH6 car media pack.")
    build.add_argument("--content-root", type=Path, required=True)
    build.add_argument("--catalog", type=Path, required=True)
    build.add_argument("--aliases", type=Path, required=True)
    build.add_argument("--tool-root", type=Path, required=True)
    build.add_argument("--output-root", type=Path, required=True)
    build.add_argument("--canonical-manifest", type=Path, required=True)
    build.add_argument("--web-root", type=Path, required=True)
    build.add_argument(
        "--game-version",
        help="Override the version read from MicrosoftGame.Config.",
    )
    return parser


def _write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_fingerprint(sources: tuple[CarMediaSource, ...]) -> str:
    digest = hashlib.sha256()
    for source in sorted(sources, key=lambda item: item.car_id):
        digest.update(
            (
                f"{source.car_id}\0{source.archive}\0{source.entry}\0"
                f"{source.size}\0{source.sha256}\n"
            ).encode("utf-8")
        )
    return digest.hexdigest()


def _detect_game_version(content_root: Path) -> str:
    config = content_root / "MicrosoftGame.Config"
    if not config.is_file():
        raise FileNotFoundError(
            f"FH6 game version unavailable; pass --game-version or provide {config}"
        )
    identity = ElementTree.parse(config).getroot().find("Identity")
    version = identity.get("Version", "").strip() if identity is not None else ""
    if not version:
        raise ValueError(f"FH6 Identity version missing from {config}")
    return version


def _validate_staged_artifacts(
    artifacts: tuple[CarMediaArtifact, ...],
    web_root: Path,
) -> None:
    total_bytes = 0
    for artifact in artifacts:
        filename = Path(artifact.web_path).name
        image = web_root / filename
        if not image.is_file():
            raise ValueError(f"Missing staged FH6 image for car {artifact.car_id}: {filename}")
        actual_size = image.stat().st_size
        if actual_size != artifact.web_size or _sha256_file(image) != artifact.web_sha256:
            raise ValueError(f"Staged FH6 image integrity mismatch for car {artifact.car_id}")
        if artifact.width <= 0 or artifact.height <= 0 or artifact.width > 640 or artifact.height > 360:
            raise ValueError(f"Invalid staged FH6 dimensions for car {artifact.car_id}")
        if not artifact.has_alpha:
            raise ValueError(f"Staged FH6 image has no alpha channel for car {artifact.car_id}")
        if artifact.oversize or actual_size > 120 * 1024:
            raise ValueError(f"Staged FH6 image exceeds 120 KiB for car {artifact.car_id}")
        total_bytes += actual_size
    if total_bytes > 90 * 1024 * 1024:
        raise ValueError(f"FH6 web image pack exceeds 90 MiB: {total_bytes} bytes")


def _publish_staged_assets(
    artifacts: tuple[CarMediaArtifact, ...],
    staging_root: Path,
    live_root: Path,
) -> None:
    live_root.mkdir(parents=True, exist_ok=True)
    for artifact in artifacts:
        filename = Path(artifact.web_path).name
        temporary = live_root / f".{filename}.publish.tmp"
        shutil.copyfile(staging_root / filename, temporary)
        temporary.replace(live_root / filename)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "inventory":
        sources = inventory_fh6_thumbnails(args.content_root)
        _write_json_atomic(
            args.report,
            {
                "source_count": len(sources),
                "cars": [asdict(source) for source in sources],
            },
        )
        print(f"Inventoried {len(sources)} FH6 stock thumbnails -> {args.report}")
        return 0
    if args.command == "sample":
        sources = inventory_fh6_thumbnails(args.content_root)
        selected = []
        for archive_name in discover_fh6_thumbnail_archives(args.content_root):
            selected.append(next(source for source in sources if source.archive == archive_name))
        texconv = args.tool_root / "app" / "win-x64" / "ForzaTools.TexConv.exe"
        if not texconv.is_file():
            raise FileNotFoundError(f"TexConv 1.0.1 not found: {texconv}")
        artifacts = [
            convert_thumbnail(
                source,
                texconv=texconv,
                content_root=args.content_root,
                master_root=args.output_root / "masters",
                web_root=args.output_root / "sample-web",
            )
            for source in selected
        ]
        report = args.output_root / "sample-report.json"
        _write_json_atomic(
            report,
            {
                "artifact_count": len(artifacts),
                "cars": [asdict(artifact) for artifact in artifacts],
            },
        )
        print(f"Converted {len(artifacts)} FH6 thumbnail samples -> {report}")
        return 0
    if args.command == "build":
        sources = inventory_fh6_thumbnails(args.content_root)
        names = load_car_names(args.catalog)
        aliases_payload = json.loads(args.aliases.read_text(encoding="utf-8"))
        if not isinstance(aliases_payload, dict) or not all(
            isinstance(alias, str) and isinstance(car_id, int)
            for alias, car_id in aliases_payload.items()
        ):
            raise ValueError("FH6 Rivals aliases must be a JSON object of name-to-integer-ID pairs")
        aliases: dict[str, int] = aliases_payload
        coverage = validate_catalog_coverage(sources, names, aliases, set())
        if not coverage.is_complete:
            raise ValueError(f"Incomplete FH6 car media coverage: {asdict(coverage)}")

        texconv = args.tool_root / "app" / "win-x64" / "ForzaTools.TexConv.exe"
        if not texconv.is_file():
            raise FileNotFoundError(f"TexConv 1.0.1 not found: {texconv}")
        args.output_root.mkdir(parents=True, exist_ok=True)
        staging_context = tempfile.TemporaryDirectory(
            prefix=".fh6-web-staging-",
            dir=args.output_root,
        )
        staging_web_root = Path(staging_context.name)
        artifacts = convert_thumbnails_batch(
            sources,
            texconv=texconv,
            content_root=args.content_root,
            master_root=args.output_root / "masters",
            web_root=staging_web_root,
        )
        _validate_staged_artifacts(artifacts, staging_web_root)
        artifact_by_id = {artifact.car_id: artifact for artifact in artifacts}
        if set(artifact_by_id) != {source.car_id for source in sources}:
            raise ValueError("FH6 converter did not return exactly one artifact per source car")
        canonical_cars = []
        web_cars = []
        for source in sources:
            name = names[source.car_id]
            artifact = artifact_by_id[source.car_id]
            canonical_cars.append(
                {
                    "car_id": source.car_id,
                    "display_name": name.display_name,
                    "confidence": name.confidence,
                    "source": asdict(source),
                    "media": asdict(artifact),
                }
            )
            web_cars.append(
                {
                    "carId": source.car_id,
                    "displayName": name.display_name,
                    "width": artifact.width,
                    "height": artifact.height,
                    "hasAlpha": artifact.has_alpha,
                    "src": artifact.web_path,
                    "bytes": artifact.web_size,
                    "sha256": artifact.web_sha256,
                }
            )

        total_web_bytes = sum(artifact.web_size for artifact in artifacts)
        game_version = args.game_version or _detect_game_version(args.content_root)
        source_fingerprint = _source_fingerprint(sources)
        generated_at = datetime.now(UTC).isoformat()
        canonical_payload = {
            "schema_version": 1,
            "generated_at": generated_at,
            "game_version": game_version,
            "source_fingerprint": source_fingerprint,
            "source_count": len(sources),
            "total_web_bytes": total_web_bytes,
            "converter": {
                "name": "ForzaTools.TexConv",
                "version": "1.0.1",
                "sha256": _sha256_file(texconv),
            },
            "cars": canonical_cars,
        }
        web_payload = {
            "schemaVersion": 1,
            "sourceCount": len(sources),
            "totalWebBytes": total_web_bytes,
            "cars": web_cars,
        }

        web_image_root = args.web_root / "public" / "images" / "cars" / "fh6"
        expected_web_files = {Path(artifact.web_path).name for artifact in artifacts}
        _publish_staged_assets(artifacts, staging_web_root, web_image_root)
        _write_json_atomic(
            args.canonical_manifest,
            canonical_payload,
        )
        _write_json_atomic(args.web_root / "data" / "fh6-rivals-image-aliases.json", aliases)
        _write_json_atomic(args.web_root / "data" / "fh6-car-media.json", web_payload)
        for candidate in web_image_root.glob("*.webp"):
            if candidate.name not in expected_web_files:
                candidate.unlink()
        staging_context.cleanup()
        print(
            f"Built {len(artifacts)} FH6 car images "
            f"({total_web_bytes / 1024 / 1024:.1f} MiB) -> {args.web_root}"
        )
        return 0
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
