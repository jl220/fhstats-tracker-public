from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from forza_tracker.fhstats_client import CATALOG_CACHE_DIR
from forza_tracker.paths import application_root
from forza_tracker.metacars_images import CarMedia, local_image_path


def data_root(cache_root: Path | None = None) -> Path:
    if cache_root is not None and cache_root.exists():
        return cache_root
    if CATALOG_CACHE_DIR.exists():
        return CATALOG_CACHE_DIR
    packaged = application_root() / "data" / "recommender"
    return packaged if packaged.exists() else application_root() / "web" / "data"


def tierlist_image_root() -> Path:
    packaged = application_root() / "data" / "tierlist-images"
    return packaged if packaged.exists() else application_root() / "web" / "public" / "images" / "tierlist"


def load_rivals(discipline: str = "road", *, cache_root: Path | None = None) -> list[dict[str, Any]]:
    filename = {
        "road": "road-rivals.json",
        "street": "street-rivals.json",
        "dirt": "dirt-rivals.json",
        "cross-country": "cross-country-rivals.json",
    }.get(discipline, "road-rivals.json")
    try:
        payload = json.loads((data_root(cache_root) / filename).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    rows: list[dict[str, Any]] = []
    for event in payload.get("events", []):
        if not isinstance(event, dict):
            continue
        event_name = str(event.get("name_es") or event.get("name_en") or "-")
        for class_group in event.get("classes", []):
            if not isinstance(class_group, dict):
                continue
            class_name = str(class_group.get("label") or "-")
            for entry in class_group.get("entries", []):
                if not isinstance(entry, dict):
                    continue
                rows.append(
                    {
                        "discipline": discipline,
                        "event": event_name,
                        "class": class_name,
                        "rank": entry.get("rank"),
                        "player": entry.get("player") or "-",
                        "car": entry.get("car") or "-",
                        "time": entry.get("time") or "-",
                    }
                )
    return rows


def count_rivals_for_circuit(circuit_name: str | None, *, cache_root: Path | None = None) -> int:
    circuit_key = _catalog_name_key(circuit_name)
    if not circuit_key:
        return 0
    return sum(
        1
        for discipline in ("road", "street", "dirt", "cross-country")
        for row in load_rivals(discipline, cache_root=cache_root)
        if _catalog_name_key(row.get("event")) == circuit_key
    )


def load_tierlist(*, cache_root: Path | None = None) -> list[dict[str, str]]:
    root = data_root(cache_root)
    try:
        source = (root / "tierlist-cars.ts").read_text(encoding="utf-8-sig")
    except OSError:
        return []
    rows: list[dict[str, str]] = []
    media = CarMedia(root)
    for block in re.findall(r"\{\s*id:\s*\"[^\"]+\".*?\n\s*\}", source, re.DOTALL):
        row = {
            "discipline": _field(block, "discipline"),
            "class": _field(block, "className"),
            "tier": _field(block, "tier"),
            "make": _field(block, "make"),
            "model": _field(block, "model"),
            "year": _number_field(block, "year"),
            "car_id": _number_field(block, "carId"),
            "tune": _field(block, "tuneCode") or _field(block, "tuneName"),
            "tuner": _field(block, "tuner"),
            "build": _field(block, "buildType"),
            "difficulty": _field(block, "difficulty"),
            "image_source": _image_source(block),
            "url": _field(block, "labsUrl"),
        }
        if row["model"] and row["class"]:
            row['fallback_source'] = media.source(row)
            row['fallback_image'] = local_image_path(root, row['fallback_source'])
            row['image'] = local_image_path(root, row['image_source']) or row['fallback_image']
            rows.append(row)
    return rows


def _field(block: str, name: str) -> str:
    match = re.search(rf"\b{re.escape(name)}:\s*\"([^\"]*)\"", block)
    return match.group(1) if match else ""


def _number_field(block: str, name: str) -> str:
    match = re.search(rf"\b{re.escape(name)}:\s*(\d+)", block)
    return match.group(1) if match else ""


def _image_source(block: str) -> str:
    direct = _field(block, 'thumbnailImage') or _field(block, 'image')
    if direct:
        return direct
    match = re.search(r'\bimage:\s*(card|image)\("([^\"]+)"\)', block)
    if match:
        suffix = 'png' if match.group(1) == 'card' else 'jpg'
        return f'/images/tierlist/{match.group(2)}.{suffix}'
    return ''


def _catalog_name_key(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    words = re.findall(r"[a-z0-9]+", normalized.casefold())
    return "".join(word for word in words if word not in {"de", "del", "la", "las", "el", "los", "the"})
