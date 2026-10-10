from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile

from PIL import Image


_ARCHIVE_PATTERN = re.compile(r"^RC(0|[1-9][0-9]*)HiRes\.zip$", re.IGNORECASE)
_THUMBNAIL_PATTERN = re.compile(r"^Thumbnail_(\d+)_Big\.swatchbin$", re.IGNORECASE)


class CarMediaInventoryError(RuntimeError):
    pass


class CarMediaConversionError(RuntimeError):
    pass


@dataclass(frozen=True)
class CarMediaSource:
    car_id: int
    archive: str
    entry: str
    size: int
    sha256: str


@dataclass(frozen=True)
class CarMediaArtifact:
    car_id: int
    width: int
    height: int
    has_alpha: bool
    master_sha256: str
    web_path: str
    web_size: int
    web_sha256: str
    oversize: bool


@dataclass(frozen=True)
class CanonicalCarName:
    car_id: int
    display_name: str
    confidence: str


@dataclass(frozen=True)
class CoverageReport:
    missing_catalog_ids: tuple[int, ...]
    orphan_catalog_ids: tuple[int, ...]
    unresolved_rivals_names: tuple[str, ...]
    invalid_alias_ids: tuple[int, ...]
    ambiguous_aliases: tuple[str, ...]

    @property
    def is_complete(self) -> bool:
        return not (
            self.missing_catalog_ids
            or self.orphan_catalog_ids
            or self.unresolved_rivals_names
            or self.invalid_alias_ids
            or self.ambiguous_aliases
        )


def discover_fh6_thumbnail_archives(content_root: Path) -> tuple[str, ...]:
    stripped_root = content_root / "media" / "stripped"
    if not stripped_root.is_dir():
        raise CarMediaInventoryError(f"Missing FH6 stripped media directory: {stripped_root}")

    archives: dict[int, str] = {}
    for candidate in stripped_root.iterdir():
        if not candidate.is_file():
            continue
        match = _ARCHIVE_PATTERN.fullmatch(candidate.name)
        if match is None:
            continue
        index = int(match.group(1))
        if index in archives:
            raise CarMediaInventoryError(f"Duplicate FH6 thumbnail archive index RC{index}")
        archives[index] = candidate.name

    if 0 not in archives:
        raise CarMediaInventoryError("Missing required FH6 thumbnail archive: RC0HiRes.zip")

    expected = set(range(max(archives) + 1))
    missing = sorted(expected - archives.keys())
    if missing:
        names = ", ".join(f"RC{index}HiRes.zip" for index in missing)
        raise CarMediaInventoryError(f"Missing required FH6 thumbnail archive: {names}")

    return tuple(archives[index] for index in sorted(archives))


def inventory_fh6_thumbnails(content_root: Path) -> tuple[CarMediaSource, ...]:
    stripped_root = content_root / "media" / "stripped"
    sources: dict[int, CarMediaSource] = {}

    for archive_name in discover_fh6_thumbnail_archives(content_root):
        archive_path = stripped_root / archive_name
        if not archive_path.is_file():
            raise CarMediaInventoryError(f"Missing required FH6 thumbnail archive: {archive_name}")
        try:
            with ZipFile(archive_path) as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    basename = PurePosixPath(info.filename).name
                    match = _THUMBNAIL_PATTERN.fullmatch(basename)
                    if match is None:
                        if basename.lower().startswith("thumbnail_") and basename.lower().endswith("_big.swatchbin"):
                            raise CarMediaInventoryError(
                                f"Malformed FH6 thumbnail entry in {archive_name}: {info.filename}"
                            )
                        continue
                    car_id = int(match.group(1))
                    if car_id in sources:
                        previous = sources[car_id]
                        raise CarMediaInventoryError(
                            f"Duplicate FH6 thumbnail car ID {car_id}: "
                            f"{previous.archive}/{previous.entry} and {archive_name}/{info.filename}"
                        )
                    digest = hashlib.sha256()
                    with archive.open(info) as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(chunk)
                    sources[car_id] = CarMediaSource(
                        car_id=car_id,
                        archive=archive_name,
                        entry=info.filename,
                        size=info.file_size,
                        sha256=digest.hexdigest(),
                    )
        except BadZipFile as exc:
            raise CarMediaInventoryError(f"Invalid FH6 thumbnail archive: {archive_name}") from exc

    return tuple(sources[car_id] for car_id in sorted(sources))


def write_web_derivative(
    car_id: int,
    master: Path,
    web_root: Path,
    *,
    max_bytes: int = 120 * 1024,
) -> CarMediaArtifact:
    master_bytes = master.read_bytes()
    master_sha256 = hashlib.sha256(master_bytes).hexdigest()
    with Image.open(io.BytesIO(master_bytes)) as opened:
        image = opened.convert("RGBA")
    image.thumbnail((640, 360), Image.Resampling.LANCZOS)
    alpha_minimum, alpha_maximum = image.getchannel("A").getextrema()
    has_alpha = alpha_minimum < 255 or alpha_maximum < 255

    encoded = b""
    for quality in range(82, 71, -2):
        output = io.BytesIO()
        image.save(output, format="WEBP", quality=quality, method=4, exact=True)
        encoded = output.getvalue()
        if len(encoded) <= max_bytes:
            break

    web_sha256 = hashlib.sha256(encoded).hexdigest()
    filename = f"{car_id}-{web_sha256[:8]}.webp"
    web_root.mkdir(parents=True, exist_ok=True)
    destination = web_root / filename
    temporary = web_root / f".{filename}.tmp"
    temporary.write_bytes(encoded)
    temporary.replace(destination)
    return CarMediaArtifact(
        car_id=car_id,
        width=image.width,
        height=image.height,
        has_alpha=has_alpha,
        master_sha256=master_sha256,
        web_path=f"/images/cars/fh6/{filename}",
        web_size=len(encoded),
        web_sha256=web_sha256,
        oversize=len(encoded) > max_bytes,
    )


def convert_thumbnail(
    source: CarMediaSource,
    *,
    texconv: Path,
    content_root: Path,
    master_root: Path,
    web_root: Path,
) -> CarMediaArtifact:
    archive_path = content_root / "media" / "stripped" / source.archive
    master_root.mkdir(parents=True, exist_ok=True)
    try:
        with ZipFile(archive_path) as archive:
            payload = archive.read(source.entry)
    except (BadZipFile, KeyError, OSError) as exc:
        raise CarMediaConversionError(
            f"Unable to extract car {source.car_id} from {source.archive}/{source.entry}"
        ) from exc
    if hashlib.sha256(payload).hexdigest() != source.sha256:
        raise CarMediaConversionError(f"Source checksum changed for FH6 car {source.car_id}")

    with tempfile.TemporaryDirectory(prefix=".staging-", dir=master_root) as staging_value:
        staging = Path(staging_value)
        swatch = staging / f"Thumbnail_{source.car_id}_Big.swatchbin"
        swatch.write_bytes(payload)
        environment = os.environ.copy()
        environment["DOTNET_ROLL_FORWARD"] = "Major"
        try:
            subprocess.run(
                [str(texconv), str(swatch), "-s"],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise CarMediaConversionError(f"TexConv failed for FH6 car {source.car_id}") from exc
        dds = swatch.with_suffix(".dds")
        if not dds.is_file():
            raise CarMediaConversionError(f"TexConv produced no DDS for FH6 car {source.car_id}")
        try:
            with Image.open(dds) as opened:
                master_image = opened.convert("RGBA")
        except (OSError, ValueError) as exc:
            raise CarMediaConversionError(f"Invalid DDS for FH6 car {source.car_id}") from exc
        master = master_root / f"{source.car_id}.png"
        staged_master = staging / f"{source.car_id}.png"
        master_image.save(staged_master, format="PNG", optimize=False)
        staged_master.replace(master)

    return write_web_derivative(source.car_id, master, web_root)


def load_car_names(path: Path) -> dict[int, CanonicalCarName]:
    names: dict[int, CanonicalCarName] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream, delimiter=";"):
            car_id = int(row["car_id"])
            if car_id in names:
                raise CarMediaInventoryError(f"Duplicate canonical FH6 car ID: {car_id}")
            names[car_id] = CanonicalCarName(
                car_id=car_id,
                display_name=row["display_name"].strip(),
                confidence=row["confidence"].strip(),
            )
    return names


def normalize_car_alias(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = value.casefold().replace("’", "'").replace("‘", "'")
    return " ".join(re.sub(r"[^a-z0-9#']+", " ", value).split())


def validate_catalog_coverage(
    sources: tuple[CarMediaSource, ...],
    names: dict[int, CanonicalCarName],
    aliases: dict[str, int],
    rivals_names: set[str],
) -> CoverageReport:
    source_ids = {source.car_id for source in sources}
    normalized_alias_targets: dict[str, set[int]] = {}
    for alias, car_id in aliases.items():
        normalized_alias_targets.setdefault(normalize_car_alias(alias), set()).add(car_id)
    normalized_aliases = {
        alias: next(iter(car_ids))
        for alias, car_ids in normalized_alias_targets.items()
        if len(car_ids) == 1
    }
    unresolved = tuple(
        sorted(name for name in rivals_names if normalize_car_alias(name) not in normalized_aliases)
    )
    return CoverageReport(
        missing_catalog_ids=tuple(sorted(source_ids - names.keys())),
        orphan_catalog_ids=tuple(sorted(names.keys() - source_ids)),
        unresolved_rivals_names=unresolved,
        invalid_alias_ids=tuple(sorted(set(aliases.values()) - source_ids)),
        ambiguous_aliases=tuple(
            sorted(alias for alias, car_ids in normalized_alias_targets.items() if len(car_ids) > 1)
        ),
    )


def convert_thumbnails_batch(
    sources: tuple[CarMediaSource, ...],
    *,
    texconv: Path,
    content_root: Path,
    master_root: Path,
    web_root: Path,
) -> tuple[CarMediaArtifact, ...]:
    master_root.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, list[CarMediaSource]] = {}
    for source in sources:
        grouped.setdefault(source.archive, []).append(source)

    with tempfile.TemporaryDirectory(prefix=".batch-staging-", dir=master_root) as staging_value:
        staging = Path(staging_value)
        for archive_name, archive_sources in grouped.items():
            archive_path = content_root / "media" / "stripped" / archive_name
            try:
                with ZipFile(archive_path) as archive:
                    for source in archive_sources:
                        payload = archive.read(source.entry)
                        if hashlib.sha256(payload).hexdigest() != source.sha256:
                            raise CarMediaConversionError(
                                f"Source checksum changed for FH6 car {source.car_id}"
                            )
                        (staging / f"Thumbnail_{source.car_id}_Big.swatchbin").write_bytes(payload)
            except (BadZipFile, KeyError, OSError) as exc:
                raise CarMediaConversionError(f"Unable to stage FH6 archive {archive_name}") from exc

        environment = os.environ.copy()
        environment["DOTNET_ROLL_FORWARD"] = "Major"
        try:
            subprocess.run(
                [str(texconv), str(staging), "-s"],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise CarMediaConversionError("TexConv failed during the FH6 thumbnail batch") from exc

        artifacts: list[CarMediaArtifact] = []
        for source in sources:
            dds = staging / f"Thumbnail_{source.car_id}_Big.dds"
            if not dds.is_file():
                raise CarMediaConversionError(f"TexConv produced no DDS for FH6 car {source.car_id}")
            try:
                with Image.open(dds) as opened:
                    master_image = opened.convert("RGBA")
            except (OSError, ValueError) as exc:
                raise CarMediaConversionError(f"Invalid DDS for FH6 car {source.car_id}") from exc
            master = master_root / f"{source.car_id}.png"
            staged_master = staging / f"{source.car_id}.png"
            master_image.save(staged_master, format="PNG", optimize=False)
            staged_master.replace(master)
            artifacts.append(write_web_derivative(source.car_id, master, web_root))
        return tuple(artifacts)
