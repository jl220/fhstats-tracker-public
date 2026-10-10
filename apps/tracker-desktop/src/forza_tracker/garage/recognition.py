from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from typing import Any, Iterable, Mapping

from forza_tracker.garage.models import GarageCar


_OCR_CONFUSIONS = str.maketrans(
    {
        "|": " ",
        "ı": "i",
        "€": "e",
        "§": "s",
        "’": "'",
        "`": "'",
    }
)
_CLASS_PI_PATTERN = re.compile(r"\b(R|S2|S1|A|B|C|D)\s*(\d{3})\b", re.IGNORECASE)
_YEAR_PATTERN = re.compile(r"\b((?:19|20)\d{2})\b")


@dataclass(frozen=True)
class ParsedCardText:
    query: str
    year: int | None
    make: str | None
    model: str | None


@dataclass(frozen=True)
class CatalogMatch:
    canonical_id: str | None
    display_name: str
    confidence: str
    score: float


def normalize_car_text(value: str) -> str:
    translated = value.translate(_OCR_CONFUSIONS)
    decomposed = unicodedata.normalize("NFKD", translated.casefold())
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    alphanumeric = re.sub(r"[^a-z0-9]+", " ", ascii_text)
    return " ".join(alphanumeric.split())


def parse_class_pi(value: str) -> tuple[str | None, int | None]:
    normalized = re.sub(r"\s+", " ", value.upper())
    match = _CLASS_PI_PATTERN.search(normalized)
    if match is None:
        return None, None
    return match.group(1).upper(), int(match.group(2))


def card_text_to_catalog_query(value: str) -> ParsedCardText:
    compact = " ".join(value.replace("\r", "\n").split())
    year_match = _YEAR_PATTERN.search(compact)
    if year_match is None:
        model = _clean_card_component(compact)
        return ParsedCardText(query=model or compact, year=None, make=None, model=model or None)
    model = _clean_card_component(compact[: year_match.start()]) or None
    make = _clean_card_component(compact[year_match.end() :]) or None
    year = int(year_match.group(1))
    if model and make:
        query = f"{year} {make} {model}"
    else:
        query = compact
    return ParsedCardText(query=query, year=year, make=make, model=model)


def _clean_card_component(value: str) -> str:
    return value.strip(" \t\r\n|~_-â€“â€”Â·â€¢.,:;!?[](){}'\"`")


def match_catalog(
    value: str,
    catalog: Mapping[int, Any] | Iterable[Any],
    aliases: Mapping[str, str],
) -> CatalogMatch:
    cars = list(catalog.values()) if isinstance(catalog, Mapping) else list(catalog)
    normalized = normalize_car_text(value)
    normalized_aliases = {normalize_car_text(key): str(car_id) for key, car_id in aliases.items()}
    alias_id = normalized_aliases.get(normalized)
    if alias_id is not None:
        aliased = next((car for car in cars if str(car.car_ordinal) == alias_id), None)
        if aliased is not None:
            return CatalogMatch(alias_id, str(aliased.display_name), "confirmed", 1.0)

    exact = [car for car in cars if normalize_car_text(str(car.display_name)) == normalized]
    if len(exact) == 1:
        return CatalogMatch(str(exact[0].car_ordinal), str(exact[0].display_name), "confirmed", 1.0)
    if not normalized or not cars:
        return CatalogMatch(None, value.strip(), "review", 0.0)

    normalized_tokens = normalized.split()
    compact = "".join(normalized_tokens)
    token_sorted = tuple(sorted(normalized_tokens))
    shape_matches = []
    for car in cars:
        candidate = normalize_car_text(str(car.display_name))
        candidate_tokens = candidate.split()
        if "".join(candidate_tokens) == compact or tuple(sorted(candidate_tokens)) == token_sorted:
            shape_matches.append(car)
    if len(shape_matches) == 1:
        matched = shape_matches[0]
        return CatalogMatch(str(matched.car_ordinal), str(matched.display_name), "confirmed", 1.0)

    prefix_matches = []
    for car in cars:
        candidate = normalize_car_text(str(car.display_name))
        if len(candidate.split()) >= 3 and (
            normalized.startswith(candidate + " ") or candidate.startswith(normalized + " ")
        ):
            prefix_matches.append(car)
    if len(prefix_matches) == 1:
        matched = prefix_matches[0]
        score = SequenceMatcher(None, normalized, normalize_car_text(str(matched.display_name))).ratio()
        return CatalogMatch(str(matched.car_ordinal), str(matched.display_name), "confirmed", score)

    scored = sorted(
        (
            (
                SequenceMatcher(None, normalized, normalize_car_text(str(car.display_name))).ratio(),
                car,
            )
            for car in cars
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    best_score, best_car = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    if best_score >= 0.92 and best_score - second_score >= 0.05:
        return CatalogMatch(
            str(best_car.car_ordinal),
            str(best_car.display_name),
            "confirmed",
            best_score,
        )
    return CatalogMatch(None, value.strip(), "review", best_score)


def apply_duplicate_policy(cars: Iterable[GarageCar], *, preserve_duplicates: bool) -> tuple[GarageCar, ...]:
    ordered = tuple(cars)
    if preserve_duplicates:
        return ordered

    grouped: dict[str, GarageCar] = {}
    result: list[GarageCar] = []
    for car in ordered:
        if car.canonical_id is None:
            result.append(car)
            continue
        existing = grouped.get(car.canonical_id)
        if existing is None:
            grouped_car = replace(car, duplicate_group=car.canonical_id)
            grouped[car.canonical_id] = grouped_car
            result.append(grouped_car)
            continue
        updated = replace(existing, quantity=existing.quantity + car.quantity)
        grouped[car.canonical_id] = updated
        result[result.index(existing)] = updated
    return tuple(result)
