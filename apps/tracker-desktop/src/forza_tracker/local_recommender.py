from __future__ import annotations

import json
import math
import os
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any

from forza_tracker.local_catalog import load_rivals
from forza_tracker.paths import application_root
from forza_tracker.recommender.meta_engine import build_meta_report

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGED_DATA_ROOT = application_root() / "data" / "recommender"
DATA_ROOT = Path(os.environ.get("FHSTATS_RECOMMENDER_DATA", PACKAGED_DATA_ROOT))
if not DATA_ROOT.exists():
    DATA_ROOT = PROJECT_ROOT / "web" / "data"
RIVALS_FILES = {
    "road": DATA_ROOT / "road-rivals.json",
    "street": DATA_ROOT / "street-rivals.json",
    "dirt": DATA_ROOT / "dirt-rivals.json",
    "cross-country": DATA_ROOT / "cross-country-rivals.json",
}
TIERLIST_PATH = DATA_ROOT / "tierlist-cars.ts"
CIRCUIT_IMAGE_ROOT = Path(
    os.environ.get(
        "FHSTATS_CIRCUIT_IMAGES",
        PROJECT_ROOT / "web" / "public" / "images" / "circuits",
    )
)

SPECIAL_CAR_ALIASES = {
    "porsche 911 gt2 rs": {"911 gt2 rs 18", "porsche 911 gt2 rs 18", "1gt2 rs 18"},
    "lamborghini essenza scv12": {"essenza scv12"},
    "dodge viper gts acr forza edition": {"dodge viper fe", "viper fe 99", "per fe 99"},
    "mazda mx 5 miata forza edition": {"mazda mx 5 fe 94", "mx 5 fe 94", "x 5 fe 94"},
    "acura integra type r": {"acura integra", "ura integra"},
    "ford mustang gt fastback": {"ford mustang 68", "ford mustang gt 2 2 fastback"},
    "honda civic type r": {"honda civic 97", "nda civic 97"},
    "alfa romeo 33 stradale": {"alfa romeo 33s"},
    "mercedes benz clk gtr": {"m b clk gtr", "clk gtr"},
    "audi sport quattro s1": {"audi sport quattro", "audi quattro", "2 audi sport quattro"},
    "audi r8 coupe v10 gt rwd": {"audi r8 v10 gt", "r8 coupe v10 gt rwd"},
    "shelby cobra daytona coupe": {"shelby daytona", "shelby cobra daytona"},
    "ford f 450 drw platinum": {"ford sd f 450", "ford f 450", "ford super duty f 450"},
}


@dataclass(frozen=True)
class TierCar:
    id: str
    discipline: str
    class_name: str
    tier: str
    make: str
    model: str
    image: str | None
    tune_code: str | None
    tune_name: str | None
    tuner: str | None
    build_type: str | None
    difficulty: str | None

    @property
    def name(self) -> str:
        return f"{self.make} {self.model}".strip()


def recommend_championship(event_plan: list[str], car_class: str) -> dict[str, Any]:
    clean_plan = [str(name).strip() for name in event_plan[:3] if str(name).strip()]
    if not clean_plan:
        raise ValueError("At least one circuit is required")

    matched_events = [_match_event(name) for name in clean_plan]
    disciplines = [event["discipline"] for event in matched_events if event is not None]
    if not disciplines:
        return _empty_response(clean_plan, car_class, "No se han encontrado los circuitos en Rivals.")

    dominant_discipline = max(set(disciplines), key=disciplines.count)
    tier_discipline = "road" if dominant_discipline == "street" else dominant_discipline
    candidates = [
        car
        for car in _load_tier_cars()
        if car.class_name == car_class and car.discipline == tier_discipline
    ]
    if not candidates:
        return _empty_response(
            clean_plan,
            car_class,
            f"No hay coches de tier list para {car_class} en {tier_discipline}.",
        )

    event_evidence = [
        _score_event_candidates(event, candidates, car_class) if event is not None else {}
        for event in matched_events
    ]
    scored = [
        _score_candidate(car, matched_events, event_evidence)
        for car in candidates
    ]
    scored.sort(key=lambda item: (-item["score"], -item["rivals"]["appearances"], item["car"]["name"]))

    evidence_count = sum(item["rivals"]["appearances"] for item in scored)
    confidence = "alta" if evidence_count >= 12 else "media" if evidence_count >= 4 else "baja"
    return {
        "status": "ok",
        "car_class": car_class,
        "discipline": dominant_discipline,
        "confidence": confidence,
        "weights": {"rivals": 0.65, "tier_list": 0.25, "usability": 0.10},
        "events": [
            _event_payload(requested_name, event)
            for requested_name, event in zip(clean_plan, matched_events, strict=False)
        ],
        "meta": _meta_report_for_class(car_class),
        "recommendations": scored[:3],
    }


def _score_candidate(
    car: TierCar,
    events: list[dict[str, Any] | None],
    event_evidence: list[dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    per_event: list[dict[str, Any]] = []
    normalized_scores: list[float] = []
    total_appearances = 0
    best_rank: int | None = None

    for event, evidence_by_car in zip(events, event_evidence, strict=False):
        evidence = evidence_by_car.get(car.id, {})
        event_score = float(evidence.get("normalized_score", 0.0))
        normalized_scores.append(event_score)
        appearances = int(evidence.get("appearances", 0))
        rank = evidence.get("best_rank")
        total_appearances += appearances
        if isinstance(rank, int):
            best_rank = rank if best_rank is None else min(best_rank, rank)
        per_event.append(
            {
                "event": event["name_es"] if event else "Circuito no reconocido",
                "score": round(event_score, 1),
                "appearances": appearances,
                "best_rank": rank,
            }
        )

    rivals_score = sum(normalized_scores) / len(normalized_scores) if normalized_scores else 0.0
    tier_score = 100.0 if car.tier == "S+" else 84.0
    usability_score = {
        "low": 100.0,
        "medium": 76.0,
        "hard": 52.0,
    }.get((car.difficulty or "").lower(), 72.0)
    consistency_penalty = _standard_deviation(normalized_scores) * 0.08 if len(normalized_scores) > 1 else 0.0
    score = 0.65 * rivals_score + 0.25 * tier_score + 0.10 * usability_score - consistency_penalty

    reason = _recommendation_reason(car, per_event, total_appearances)
    return {
        "score": round(max(0.0, score), 1),
        "reason": reason,
        "car": {
            "id": car.id,
            "name": car.name,
            "class": car.class_name,
            "tier": car.tier,
            "image": car.image,
        },
        "tune": {
            "code": car.tune_code,
            "name": car.tune_name,
            "tuner": car.tuner,
            "build_type": car.build_type,
            "difficulty": car.difficulty,
        },
        "rivals": {
            "score": round(rivals_score, 1),
            "appearances": total_appearances,
            "best_rank": best_rank,
            "events": per_event,
        },
    }


def _score_event_candidates(
    event: dict[str, Any],
    candidates: list[TierCar],
    car_class: str,
) -> dict[str, dict[str, Any]]:
    class_block = next(
        (block for block in event.get("classes", []) if block.get("label") == car_class),
        None,
    )
    entries = class_block.get("entries", []) if class_block else []
    scores: dict[str, dict[str, Any]] = {}
    maximum_points = 0.0

    for car in candidates:
        matches = [entry for entry in entries if _car_matches(car, str(entry.get("car") or ""))]
        ranks = [int(entry["rank"]) for entry in matches if isinstance(entry.get("rank"), int)]
        points = sum(max(1, 12 - rank) for rank in ranks)
        maximum_points = max(maximum_points, points)
        scores[car.id] = {
            "points": points,
            "appearances": len(ranks),
            "best_rank": min(ranks) if ranks else None,
        }

    for evidence in scores.values():
        evidence["normalized_score"] = (
            100.0 * float(evidence["points"]) / maximum_points
            if maximum_points > 0
            else 0.0
        )
    return scores


def _car_matches(car: TierCar, rival_name: str) -> bool:
    rival = _normalize(rival_name)
    canonical = _normalize(car.name)
    model = _normalize(car.model)
    aliases = {
        canonical,
        canonical[1:] if len(canonical) > 7 else canonical,
        model,
        *SPECIAL_CAR_ALIASES.get(canonical, set()),
    }
    return any(
        rival == alias
        or len(alias) >= 4 and any(character.isdigit() for character in alias) and alias in rival
        or len(alias) >= 7 and alias in rival
        or len(rival) >= 7 and rival in alias
        or SequenceMatcher(None, rival, alias).ratio() >= 0.82
        for alias in aliases
    )


def _recommendation_reason(car: TierCar, events: list[dict[str, Any]], appearances: int) -> str:
    covered = sum(1 for event in events if event["appearances"] > 0)
    if appearances:
        return (
            f"Tier {car.tier}, con evidencia Rivals en {covered}/{len(events)} circuitos "
            f"y {appearances} apariciones en la clase."
        )
    return (
        f"Tier {car.tier} sin evidencia directa en estos circuitos; "
        "se recomienda por su posición general en la tier list."
    )


def _event_payload(requested_name: str, event: dict[str, Any] | None) -> dict[str, Any]:
    if event is None:
        return {
            "requested_name": requested_name,
            "matched": False,
            "name": requested_name,
            "discipline": None,
            "slug": None,
            "map_url": None,
            "map_available": False,
        }
    image_path = CIRCUIT_IMAGE_ROOT / f"{event['slug']}.png"
    return {
        "requested_name": requested_name,
        "matched": True,
        "name": event["name_es"],
        "discipline": event["discipline"],
        "slug": event["slug"],
        "event_type": event.get("event_type"),
        "map_url": f"/images/circuits/{event['slug']}.png",
        "map_available": image_path.exists(),
    }


def _empty_response(event_plan: list[str], car_class: str, message: str) -> dict[str, Any]:
    return {
        "status": "no_data",
        "car_class": car_class,
        "discipline": None,
        "confidence": "baja",
        "message": message,
        "meta": _meta_report_for_class(car_class),
        "events": [
            {
                "requested_name": name,
                "matched": False,
                "name": name,
                "map_url": None,
                "map_available": False,
            }
            for name in event_plan
        ],
        "recommendations": [],
    }


def _meta_report_for_class(car_class: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    sequence = 0
    for discipline in ("road", "street", "dirt", "cross-country"):
        for row in load_rivals(discipline):
            sequence += 1
            rows.append({**row, "sequence": row.get("sequence") or sequence})
    return build_meta_report(rows, car_class=car_class)


def _match_event(name: str) -> dict[str, Any] | None:
    requested = _normalize(name)
    best: dict[str, Any] | None = None
    best_score = 0.0
    for event in _load_rivals_events():
        candidates = [event.get("name_es", ""), event.get("name_en", ""), event.get("slug", "")]
        score = max(
            SequenceMatcher(None, requested, _normalize(candidate)).ratio()
            for candidate in candidates
            if candidate
        )
        if score > best_score:
            best = event
            best_score = score
    return best if best_score >= 0.72 else None


def _load_rivals_events() -> tuple[dict[str, Any], ...]:
    signature = tuple(path.stat().st_mtime_ns for path in RIVALS_FILES.values())
    return _load_rivals_events_cached(signature)


@lru_cache(maxsize=2)
def _load_rivals_events_cached(_signature: tuple[int, ...]) -> tuple[dict[str, Any], ...]:
    events: list[dict[str, Any]] = []
    for discipline, path in RIVALS_FILES.items():
        with path.open("r", encoding="utf-8-sig") as file:
            payload = json.load(file)
        for event in payload.get("events", []):
            events.append({**event, "discipline": discipline})
    return tuple(events)


def _load_tier_cars() -> tuple[TierCar, ...]:
    return _load_tier_cars_cached(TIERLIST_PATH.stat().st_mtime_ns)


@lru_cache(maxsize=2)
def _load_tier_cars_cached(_signature: int) -> tuple[TierCar, ...]:
    source = TIERLIST_PATH.read_text(encoding="utf-8")
    array_source = _extract_array(source, "export const tierSPlusCars")
    objects = _extract_top_level_objects(array_source)
    cars: list[TierCar] = []
    for value in objects:
        fields = {
            key: _extract_string_field(value, key)
            for key in (
                "id",
                "discipline",
                "className",
                "tier",
                "make",
                "model",
                "tuneCode",
                "tuneName",
                "tuner",
                "buildType",
                "difficulty",
            )
        }
        image = _extract_image(value)
        required = ("id", "discipline", "className", "tier", "make", "model")
        if not all(fields[key] for key in required):
            continue
        cars.append(
            TierCar(
                id=fields["id"] or "",
                discipline=fields["discipline"] or "",
                class_name=fields["className"] or "",
                tier=fields["tier"] or "",
                make=fields["make"] or "",
                model=fields["model"] or "",
                image=image,
                tune_code=fields["tuneCode"],
                tune_name=fields["tuneName"],
                tuner=fields["tuner"],
                build_type=fields["buildType"],
                difficulty=fields["difficulty"],
            )
        )
    return tuple(cars)


def _extract_array(source: str, marker: str) -> str:
    marker_index = source.index(marker)
    assignment = source.index("=", marker_index)
    start = source.index("[", assignment)
    end = _matching_delimiter(source, start, "[", "]")
    return source[start + 1 : end]


def _extract_top_level_objects(source: str) -> list[str]:
    objects: list[str] = []
    index = 0
    while index < len(source):
        if source[index] != "{":
            index += 1
            continue
        end = _matching_delimiter(source, index, "{", "}")
        objects.append(source[index : end + 1])
        index = end + 1
    return objects


def _matching_delimiter(source: str, start: int, opening: str, closing: str) -> int:
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(start, len(source)):
        character = source[index]
        if quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {'"', "'", "`"}:
            quote = character
        elif character == opening:
            depth += 1
        elif character == closing:
            depth -= 1
            if depth == 0:
                return index
    raise ValueError(f"Unclosed delimiter {opening}")


def _extract_string_field(source: str, field: str) -> str | None:
    match = re.search(rf"\b{re.escape(field)}\s*:\s*([\"'])(.*?)\1", source, re.DOTALL)
    return match.group(2).strip() if match else None


def _extract_image(source: str) -> str | None:
    helper = re.search(r"\bimage\s*:\s*(?:card|image)\s*\(\s*([\"'])(.*?)\1\s*\)", source)
    if helper:
        extension = "png" if "card" in helper.group(0).split("(", 1)[0] else "jpg"
        return f"/images/tierlist/{helper.group(2)}.{extension}"
    return _extract_string_field(source, "image")


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(character for character in decomposed if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9]+", " ", ascii_value.lower()).strip()


def _standard_deviation(values: list[float]) -> float:
    if not values:
        return 0.0
    average = sum(values) / len(values)
    return math.sqrt(sum((value - average) ** 2 for value in values) / len(values))
