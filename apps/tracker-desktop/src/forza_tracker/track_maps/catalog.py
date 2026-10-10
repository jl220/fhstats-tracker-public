from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from forza_tracker.localization import current_language, normalize_language
from forza_tracker.paths import application_root


DEFAULT_TRACK_MAP_ROOT = application_root() / "data" / "canonical" / "track_maps"


@dataclass(frozen=True)
class TrackMap:
    id: str
    discipline: str
    index: int
    slug: str
    name_en: str
    name_es: str
    image_path: Path
    source: str
    aliases: tuple[str, ...] = ()

    def display_name(self, language: str | None = None) -> str:
        selected = normalize_language(language) or current_language()
        if selected == "es":
            return self.name_es or self.name_en or self.slug
        return self.name_en or self.name_es or self.slug

    @property
    def name(self) -> str:
        return self.display_name()


def load_track_maps(root: Path | None = None) -> tuple[TrackMap, ...]:
    catalog_root = root or DEFAULT_TRACK_MAP_ROOT
    if root is None:
        return _load_track_maps_cached(_catalog_signature(catalog_root), str(catalog_root))
    return _load_track_maps(catalog_root)


def find_track_map(
    query: str | None,
    *,
    discipline: str | None = None,
    root: Path | None = None,
) -> TrackMap | None:
    normalized_query = _normalize(query)
    if not normalized_query:
        return None
    query_tokens = set(normalized_query.split())
    normalized_discipline = _normalize_discipline(discipline)
    exact_matches: list[TrackMap] = []
    matches: list[TrackMap] = []
    for track_map in load_track_maps(root):
        if normalized_discipline and track_map.discipline != normalized_discipline:
            continue
        candidates = (
            track_map.slug,
            track_map.slug.replace("-", " "),
            track_map.name_en,
            track_map.name_es,
            *track_map.aliases,
        )
        if normalized_query in {_normalize(candidate) for candidate in candidates}:
            exact_matches.append(track_map)
        elif any(_matches(normalized_query, query_tokens, candidate) for candidate in candidates):
            matches.append(track_map)
    matches = exact_matches or matches
    if normalized_discipline:
        return matches[0] if matches else None
    return matches[0] if len(matches) == 1 else None


@lru_cache(maxsize=2)
def _load_track_maps_cached(_signature: tuple[int, int], root_value: str) -> tuple[TrackMap, ...]:
    return _load_track_maps(Path(root_value))


def _load_track_maps(root: Path) -> tuple[TrackMap, ...]:
    metadata_path = root / "metadata.json"
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return tuple()
    if not isinstance(payload, list):
        return tuple()

    maps: list[TrackMap] = []
    for raw_item in payload:
        if not isinstance(raw_item, dict):
            continue
        item = _track_map_from_metadata(root, raw_item)
        if item is not None:
            maps.append(item)
    maps.sort(key=lambda item: (item.discipline, item.index, item.slug))
    return tuple(maps)


def _track_map_from_metadata(root: Path, item: dict[str, Any]) -> TrackMap | None:
    image = item.get("image")
    if not isinstance(image, str) or not image.strip():
        return None
    image_path = root / Path(image)
    if not image_path.exists():
        return None
    slug = _clean(item.get("slug"))
    discipline = _clean(item.get("discipline"))
    if not slug or not discipline:
        return None
    return TrackMap(
        id=_clean(item.get("id")) or f"{discipline}:{slug}",
        discipline=discipline,
        index=_positive_int(item.get("index")) or 0,
        slug=slug,
        name_en=_clean(item.get("name_en")),
        name_es=_clean(item.get("name_es")),
        image_path=image_path,
        source=_clean(item.get("source")),
        aliases=_aliases(item.get("aliases")),
    )


def _catalog_signature(root: Path) -> tuple[int, int]:
    metadata = root / "metadata.json"
    try:
        stat = metadata.stat()
    except OSError:
        return (0, 0)
    return (stat.st_mtime_ns, stat.st_size)


def _matches(normalized_query: str, query_tokens: set[str], candidate: str) -> bool:
    normalized_candidate = _normalize(candidate)
    if not normalized_candidate:
        return False
    if normalized_query == normalized_candidate:
        return True
    if normalized_query in normalized_candidate or normalized_candidate in normalized_query:
        return True
    candidate_tokens = set(normalized_candidate.split())
    if query_tokens and query_tokens.issubset(candidate_tokens):
        return True
    return bool(_distinctive_tokens(normalized_query) & _distinctive_tokens(normalized_candidate))


def _normalize(value: str | None) -> str:
    if value is None:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(value))
    ascii_value = "".join(character for character in decomposed if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9]+", " ", ascii_value.lower()).strip()


def _normalize_discipline(value: str | None) -> str | None:
    aliases = {
        "calle": "street",
        "callejera": "street",
        "campo a traves": "cross-country",
        "carretera": "road",
        "cross country": "cross-country",
        "dirt": "dirt",
        "road": "road",
        "street": "street",
        "tierra": "dirt",
    }
    return aliases.get(_normalize(value))


def _distinctive_tokens(normalized_value: str) -> set[str]:
    route_type_tokens = {
        "a",
        "al",
        "atraves",
        "campo",
        "carrera",
        "circuito",
        "country",
        "cross",
        "de",
        "del",
        "dirt",
        "el",
        "la",
        "los",
        "road",
        "street",
        "the",
        "tierra",
        "tramo",
    }
    return {token for token in normalized_value.split() if token not in route_type_tokens}


def _clean(value: Any) -> str:
    return str(value).strip() if value not in {None, ""} else ""


def _aliases(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None
