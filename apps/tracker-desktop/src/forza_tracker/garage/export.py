from __future__ import annotations

import json
import re
import shutil
import unicodedata
from pathlib import Path

from forza_tracker.garage.models import GarageCar, GarageSnapshot


def export_fhstats_package(
    snapshot: GarageSnapshot,
    destination: Path,
    *,
    source_root: Path | None = None,
) -> Path:
    if not snapshot.completed:
        raise ValueError("Solo se puede exportar un garaje completado.")
    package = destination.expanduser().resolve()
    if package.exists() and any(package.iterdir()):
        raise FileExistsError(f"La carpeta de exportacion ya contiene archivos: {package}")
    originals = package / "originals"
    originals.mkdir(parents=True, exist_ok=True)
    root = source_root.expanduser().resolve() if source_root is not None else None

    selected: dict[str, GarageCar] = {}
    unresolved: list[dict[str, object]] = []
    for car in sorted(snapshot.cars, key=lambda item: item.capture_index):
        if car.confidence != "confirmed" or not car.canonical_id:
            unresolved.append(
                {
                    "capture_index": car.capture_index,
                    "display_name": car.display_name,
                    "confidence": car.confidence,
                    "raw_ocr": car.raw_ocr,
                    "image_path": car.image_path,
                }
            )
            continue
        selected.setdefault(car.canonical_id, car)

    exported: list[dict[str, object]] = []
    used_slugs: set[str] = set()
    for canonical_id, car in sorted(selected.items(), key=lambda item: item[1].capture_index):
        source = _resolve_source(car.image_path, root)
        base_slug = _slugify("-".join(part for part in (str(car.year or ""), car.make or "", car.model or "") if part))
        slug = base_slug or f"car-{canonical_id}"
        if slug in used_slugs:
            slug = f"{slug}-{canonical_id}"
        used_slugs.add(slug)
        relative_output = Path("originals") / f"{slug}.png"
        shutil.copy2(source, package / relative_output)
        exported.append(
            {
                "canonical_id": canonical_id,
                "display_name": car.display_name,
                "year": car.year,
                "make": car.make,
                "model": car.model,
                "slug": slug,
                "capture_index": car.capture_index,
                "source_scan_id": snapshot.scan_id,
                "image_path": relative_output.as_posix(),
            }
        )

    _write_json(
        package / "manifest.json",
        {
            "schema_version": 1,
            "scan_id": snapshot.scan_id,
            "observed_count": snapshot.observed_count,
            "cars": exported,
        },
    )
    _write_json(package / "unresolved.json", unresolved)
    return package


def _resolve_source(value: str, root: Path | None) -> Path:
    path = Path(value)
    if root is not None and not path.is_absolute():
        path = (root / path).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Una imagen intenta salir de la copia del garaje.")
    else:
        path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"No existe la imagen del garaje: {path}")
    return path


def _slugify(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", ascii_text)).strip("-")


def _write_json(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
