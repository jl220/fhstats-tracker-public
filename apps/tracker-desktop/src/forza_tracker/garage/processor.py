from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import cv2
import numpy as np

from forza_tracker.garage.models import GarageCar, GarageSnapshot
from forza_tracker.garage.recognition import (
    apply_duplicate_policy,
    card_text_to_catalog_query,
    match_catalog,
    normalize_car_text,
    parse_class_pi,
)


OcrCallback = Callable[[np.ndarray, int], str]


def process_draft(
    snapshot: GarageSnapshot,
    draft_path: Path,
    catalog: Mapping[int, Any] | Iterable[Any],
    aliases: Mapping[str, str],
    ocr: OcrCallback,
) -> GarageSnapshot:
    raw_dir = (draft_path / "raw").resolve()
    records_path = raw_dir / "captures.json"
    records = json.loads(records_path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError("El indice de capturas del garaje no es valido.")

    cars: list[GarageCar] = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Hay una captura de garaje no valida.")
        capture_index = int(record["capture_index"])
        car_path = _safe_capture_path(raw_dir, str(record["car_image"]))
        name_path = _safe_capture_path(raw_dir, str(record["name_image"]))
        pi_path = _safe_capture_path(raw_dir, str(record["class_pi_image"]))
        car_image = _read_image(car_path)
        name_image = _read_image(name_path)
        pi_image = _read_image(pi_path)

        name_text = _ocr_consensus(name_image, ocr, psm=6)
        car_class, performance_index, pi_text = _read_class_pi(pi_image, ocr)
        parsed = card_text_to_catalog_query(name_text)
        matched = match_catalog(parsed.query, catalog, aliases)
        del car_image  # The lossless source remains on disk and is referenced below.
        cars.append(
            GarageCar(
                capture_index=capture_index,
                display_name=matched.display_name or parsed.query,
                year=parsed.year,
                make=parsed.make,
                model=parsed.model,
                car_class=car_class,
                performance_index=performance_index,
                canonical_id=matched.canonical_id,
                confidence=matched.confidence,
                raw_ocr=f"{name_text}\n{pi_text}".strip(),
                image_path=f"raw/{car_path.name}",
                duplicate_group=None,
                quantity=1,
            )
        )

    processed = apply_duplicate_policy(cars, preserve_duplicates=snapshot.options.preserve_duplicates)
    return replace(
        snapshot,
        completed=True,
        completed_at=datetime.now(UTC).isoformat(),
        cars=processed,
        expected_count=len(records),
        observed_count=len(records),
        warning=None,
    )


def reprocess_snapshot_matches(
    snapshot: GarageSnapshot,
    catalog: Mapping[int, Any] | Iterable[Any],
    aliases: Mapping[str, str],
) -> GarageSnapshot:
    cars: list[GarageCar] = []
    for car in snapshot.cars:
        parsed = card_text_to_catalog_query(_saved_name_text(car.raw_ocr))
        matched = match_catalog(parsed.query, catalog, aliases)
        cars.append(
            replace(
                car,
                display_name=matched.display_name or parsed.query,
                year=parsed.year,
                make=parsed.make,
                model=parsed.model,
                canonical_id=matched.canonical_id,
                confidence=matched.confidence,
                duplicate_group=None,
            )
        )
    processed = apply_duplicate_policy(cars, preserve_duplicates=snapshot.options.preserve_duplicates)
    return replace(snapshot, cars=processed, warning=None)


def _saved_name_text(raw_ocr: str) -> str:
    lines = [line.strip() for line in raw_ocr.splitlines() if line.strip()]
    if lines and re.match(r"^(?:R|S2|S1|A|B|C|D)\b", lines[-1], re.IGNORECASE):
        lines.pop()
    return "\n".join(lines)


def load_aliases(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {normalize_car_text(str(key)): str(value) for key, value in payload.items()}


def save_alias(path: Path, raw_text: str, canonical_id: str) -> dict[str, str]:
    aliases = load_aliases(path)
    aliases[normalize_car_text(raw_text)] = str(canonical_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(aliases, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)
    return aliases


def _safe_capture_path(raw_dir: Path, relative: str) -> Path:
    path = (raw_dir / relative).resolve()
    if path.parent != raw_dir:
        raise ValueError("Una captura intenta salir de la carpeta provisional.")
    return path


def _read_image(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"No se pudo leer la captura {path.name}.")
    return image


def _ocr_consensus(image: np.ndarray, ocr: OcrCallback, *, psm: int) -> str:
    variants = _ocr_variants(image)
    values = [ocr(variant, psm).strip() for variant in variants]
    non_empty = [value for value in values if normalize_car_text(value)]
    if not non_empty:
        return ""
    counts = Counter(normalize_car_text(value) for value in non_empty)
    winning_key = max(counts, key=lambda key: (counts[key], len(key)))
    return max(
        (value for value in non_empty if normalize_car_text(value) == winning_key),
        key=len,
    )


def _ocr_variants(image: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    enlarged = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    _, binary = cv2.threshold(enlarged, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return image, binary, cv2.bitwise_not(binary)


def _read_class_pi(image: np.ndarray, ocr: OcrCallback) -> tuple[str | None, int | None, str]:
    car_class = _detect_class_color(image)
    digits_crop = image[:, max(1, int(image.shape[1] * 0.35)) :]
    digits_text = _ocr_consensus(digits_crop, ocr, psm=8)
    digits_match = re.search(r"\b(\d{3})\b", digits_text)
    combined = f"{car_class or ''} {digits_match.group(1) if digits_match else digits_text}".strip()
    parsed_class, performance_index = parse_class_pi(combined)
    return parsed_class or car_class, performance_index, combined


def _detect_class_color(image: np.ndarray) -> str | None:
    left = image[:, : max(1, int(image.shape[1] * 0.42))]
    hsv = cv2.cvtColor(left, cv2.COLOR_BGR2HSV)
    saturated = hsv[(hsv[:, :, 1] >= 80) & (hsv[:, :, 2] >= 100)]
    if saturated.size == 0:
        return None
    hue = float(np.median(saturated[:, 0]))
    target_hues = {
        "R": 165.0,
        "S2": 112.0,
        "S1": 145.0,
        "A": 178.0,
        "B": 10.0,
        "C": 21.0,
        "D": 95.0,
    }

    def hue_distance(target: float) -> float:
        direct = abs(hue - target)
        return min(direct, 180.0 - direct)

    car_class, distance = min(target_hues.items(), key=lambda item: hue_distance(item[1]))
    return car_class if hue_distance(distance) <= 16.0 else None
