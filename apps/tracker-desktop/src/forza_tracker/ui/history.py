from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any
import re


_CLASS_RE = re.compile(r"\b(R|X|S2|S1|A|B|C|D)\s*([0-9O]{3})", re.IGNORECASE)
_KNOWN_CLASSES = [
    ("D", "400"),
    ("C", "500"),
    ("B", "600"),
    ("A", "700"),
    ("S1", "800"),
    ("S2", "900"),
    ("R", "998"),
]
_CLASS_CROP_CACHE: dict[str, str | None] = {}


@dataclass(frozen=True)
class HistoryEntry:
    raw: dict[str, Any]

    @property
    def screen_type(self) -> str:
        return str(self.raw.get("screen_type") or "")

    @property
    def timestamp(self) -> datetime | None:
        value = self.raw.get("timestamp")
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

    @property
    def display_time(self) -> str:
        parsed = self.timestamp
        if parsed is None:
            return "-"
        return parsed.astimezone().strftime("%d/%m %H:%M:%S")

    def get(self, key: str, default: str = "-") -> Any:
        value = self.raw.get(key)
        return default if value is None or value == "" else value


@dataclass(frozen=True)
class RaceEntry:
    raw: dict[str, Any]
    starting_grid: HistoryEntry | None
    race_result: HistoryEntry
    championship_result: HistoryEntry | None

    @property
    def timestamp(self) -> datetime | None:
        return self.race_result.timestamp

    @property
    def display_time(self) -> str:
        return self.race_result.display_time

    def get(self, key: str, default: str = "-") -> Any:
        value = self.raw.get(key)
        return default if value is None or value == "" else value


@dataclass(frozen=True)
class GroupSummary:
    name: str
    car_class: str
    total_races: int
    wins: int
    podiums: int
    avg_position: float | None
    best_finish: int | None
    favorite_car: str | None
    win_rate: float
    podium_rate: float
    avg_positions_gained: float | None


@dataclass(frozen=True)
class HistorySummary:
    total_races: int
    wins: int
    podiums: int
    avg_position: float | None
    win_rate: float
    podium_rate: float
    avg_positions_gained: float | None
    unique_cars: int
    favorite_car: str | None
    favorite_class: str | None
    best_comeback: int | None
    by_car: list[GroupSummary]
    by_circuit: list[GroupSummary]


class HistoryRepository:
    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def signature(self) -> tuple[int, int] | None:
        if not self._path.exists():
            return None
        stat = self._path.stat()
        return stat.st_size, stat.st_mtime_ns

    def load(self) -> list[HistoryEntry]:
        if not self._path.exists():
            return []

        entries: list[HistoryEntry] = []
        with self._path.open("r", encoding="utf-8-sig") as file:
            for line in file:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    entries.append(HistoryEntry(data))

        return _enrich_entries(entries)


def summarize(entries: list[HistoryEntry] | list[RaceEntry]) -> HistorySummary:
    races = build_race_history(entries) if entries and isinstance(entries[0], HistoryEntry) else entries
    race_entries = [race for race in races if _is_int(race.raw.get("position"))]
    race_positions = [int(race.raw["position"]) for race in race_entries]
    wins = sum(1 for position in race_positions if position == 1)
    podiums = sum(1 for position in race_positions if position <= 3)
    gains = [
        int(race.raw["starting_position"]) - int(race.raw["position"])
        for race in race_entries
        if _is_int(race.raw.get("starting_position"))
    ]
    cars = [str(race.raw["car"]) for race in race_entries if _clean_group_value(race.raw.get("car"))]
    classes = [str(race.raw["car_class"]) for race in race_entries if _clean_group_value(race.raw.get("car_class"))]
    total_races = len(race_positions)

    return HistorySummary(
        total_races=total_races,
        wins=wins,
        podiums=podiums,
        avg_position=mean(race_positions) if race_positions else None,
        win_rate=(wins / total_races * 100) if total_races else 0.0,
        podium_rate=(podiums / total_races * 100) if total_races else 0.0,
        avg_positions_gained=mean(gains) if gains else None,
        unique_cars=len(set(cars)),
        favorite_car=_most_common(cars),
        favorite_class=_most_common(classes),
        best_comeback=max(gains) if gains else None,
        by_car=_summarize_groups(race_entries, "car"),
        by_circuit=_summarize_groups(race_entries, "circuit_name"),
    )


def build_race_history(entries: list[HistoryEntry] | list[RaceEntry]) -> list[RaceEntry]:
    if not entries:
        return []
    if isinstance(entries[0], RaceEntry):
        return list(entries)

    races: list[RaceEntry] = []
    pending_grid: HistoryEntry | None = None
    last_race_index: int | None = None

    for entry in entries:
        if entry.screen_type == "starting_grid":
            pending_grid = entry
            continue

        if entry.screen_type == "race_result":
            race = _build_race_entry(pending_grid, entry, None)
            if races and _is_duplicate_race(races[-1], race):
                races[-1] = _merge_duplicate_race(races[-1], race)
                last_race_index = len(races) - 1
                pending_grid = None
                continue
            races.append(race)
            last_race_index = len(races) - 1
            pending_grid = None
            continue

        if entry.screen_type == "championship_result" and last_race_index is not None:
            current = races[last_race_index]
            races[last_race_index] = _build_race_entry(current.starting_grid, current.race_result, entry)
            last_race_index = None

    return races


def _is_duplicate_race(previous: RaceEntry, current: RaceEntry) -> bool:
    previous_time = previous.timestamp
    current_time = current.timestamp
    if previous_time is not None and current_time is not None:
        seconds_apart = abs((current_time - previous_time).total_seconds())
        if seconds_apart > 180:
            return False

    keys = [
        "event_number",
        "event_total",
        "circuit_name",
        "starting_position",
        "position",
        "car",
        "car_class",
        "total_time",
        "best_lap",
    ]
    comparable = [
        (_clean_compare_value(previous.raw.get(key)), _clean_compare_value(current.raw.get(key)))
        for key in keys
        if previous.raw.get(key) not in {None, ""} and current.raw.get(key) not in {None, ""}
    ]
    if not comparable:
        return False

    matches = sum(1 for previous_value, current_value in comparable if previous_value == current_value)
    strong_fields = {"position", "total_time", "best_lap"}
    has_strong_match = any(
        previous.raw.get(key) not in {None, ""}
        and current.raw.get(key) not in {None, ""}
        and _clean_compare_value(previous.raw.get(key)) == _clean_compare_value(current.raw.get(key))
        for key in strong_fields
    )
    return has_strong_match and matches >= max(2, len(comparable) - 1)


def _merge_duplicate_race(previous: RaceEntry, current: RaceEntry) -> RaceEntry:
    championship_result = current.championship_result or previous.championship_result
    starting_grid = current.starting_grid or previous.starting_grid
    return _build_race_entry(starting_grid, current.race_result, championship_result)


def _build_race_entry(
    starting_grid: HistoryEntry | None,
    race_result: HistoryEntry,
    championship_result: HistoryEntry | None,
) -> RaceEntry:
    raw: dict[str, Any] = {}
    for source in [starting_grid, race_result, championship_result]:
        if source is None:
            continue
        for key, value in source.raw.items():
            if value is not None and value != "":
                raw[key] = value

    if starting_grid is not None:
        raw["starting_position"] = starting_grid.raw.get("starting_position")
        raw["event_number"] = starting_grid.raw.get("event_number")
        raw["event_total"] = starting_grid.raw.get("event_total")
        raw["event_plan"] = starting_grid.raw.get("event_plan")
        raw["discipline"] = starting_grid.raw.get("discipline")
        raw["class_crop_path"] = starting_grid.raw.get("class_crop_path")
    raw["position"] = race_result.raw.get("position")
    raw["best_lap"] = race_result.raw.get("best_lap")
    raw["total_time"] = race_result.raw.get("total_time")
    raw["timestamp"] = race_result.raw.get("timestamp")
    raw["screenshot_path"] = race_result.raw.get("screenshot_path")
    raw["circuit_name"] = race_result.raw.get("circuit_name") or (
        starting_grid.raw.get("circuit_name") if starting_grid is not None else None
    )
    if championship_result is not None:
        raw["championship_position"] = championship_result.raw.get("championship_position")

    return RaceEntry(
        raw=raw,
        starting_grid=starting_grid,
        race_result=race_result,
        championship_result=championship_result,
    )


def _enrich_entries(entries: list[HistoryEntry]) -> list[HistoryEntry]:
    enriched: list[HistoryEntry] = []
    last_circuit_name: str | None = None
    last_grid_car: str | None = None
    last_grid_car_class: str | None = None
    last_event_type: str | None = None
    last_event_number: int | None = None
    last_event_total: int | None = None
    last_event_plan: list[str] | None = None
    last_race_car: str | None = None
    last_race_car_class: str | None = None

    for entry in entries:
        raw = dict(entry.raw)
        screen_type = str(raw.get("screen_type") or "")

        if screen_type in {"event_registration", "matchmaking_search", "matchmaking_found"}:
            raw["circuit_name"] = None
        elif _is_real_circuit_name(raw.get("circuit_name")):
            raw["circuit_name"] = _clean_circuit_name(raw["circuit_name"])
            last_circuit_name = str(raw["circuit_name"])
        elif last_circuit_name:
            raw["circuit_name"] = last_circuit_name

        if not raw.get("car_class"):
            crop_class = _extract_class_from_crop_path(raw.get("class_crop_path"))
            if crop_class:
                raw["car_class"] = crop_class

        circuit_discipline = _discipline_from_circuit_name(raw.get("circuit_name"))
        if circuit_discipline:
            raw["discipline"] = circuit_discipline

        if screen_type in {"event_registration", "matchmaking_search", "matchmaking_found"}:
            if raw.get("event_type"):
                last_event_type = str(raw["event_type"])
            if isinstance(raw.get("event_plan"), list):
                last_event_plan = [str(item) for item in raw["event_plan"]]
            if _is_int(raw.get("event_number")):
                last_event_number = int(raw["event_number"])
            if _is_int(raw.get("event_total")):
                last_event_total = int(raw["event_total"])
        elif screen_type == "starting_grid":
            if not raw.get("event_type") and last_event_type:
                raw["event_type"] = last_event_type
            if not raw.get("event_number") and last_event_number:
                raw["event_number"] = last_event_number
            if not raw.get("event_total") and last_event_total:
                raw["event_total"] = last_event_total
            if not raw.get("event_plan") and last_event_plan:
                raw["event_plan"] = last_event_plan
            if not raw.get("car_class"):
                raw["car_class"] = _extract_class(raw.get("raw_row_text"))
            if raw.get("car"):
                last_grid_car = str(raw["car"])
                last_grid_car_class = _clean_group_value(raw.get("car_class"))
            elif raw.get("car_class"):
                last_grid_car_class = str(raw["car_class"])
        elif screen_type == "race_result":
            if not raw.get("car_class"):
                raw["car_class"] = _extract_class(raw.get("raw_row_text"))
            if not raw.get("car") and last_grid_car:
                raw["car"] = last_grid_car
            if not raw.get("car_class") and last_grid_car_class and (not raw.get("car") or raw.get("car") == last_grid_car):
                raw["car_class"] = last_grid_car_class
            if not raw.get("discipline") and enriched:
                raw["discipline"] = enriched[-1].raw.get("discipline")
            if not raw.get("event_number") and enriched:
                raw["event_number"] = enriched[-1].raw.get("event_number")
                raw["event_total"] = enriched[-1].raw.get("event_total")
            if not raw.get("event_type") and last_event_type:
                raw["event_type"] = last_event_type
            if not raw.get("event_plan") and last_event_plan:
                raw["event_plan"] = last_event_plan
            if raw.get("car"):
                last_race_car = str(raw["car"])
                last_race_car_class = _clean_group_value(raw.get("car_class"))
            elif raw.get("car_class"):
                last_race_car_class = str(raw["car_class"])
        elif screen_type == "championship_result":
            if not raw.get("car") and last_race_car:
                raw["car"] = last_race_car
            if not raw.get("car_class") and last_race_car_class:
                raw["car_class"] = last_race_car_class
            if not raw.get("event_type") and last_event_type:
                raw["event_type"] = last_event_type
            if not raw.get("event_plan") and last_event_plan:
                raw["event_plan"] = last_event_plan

        enriched.append(HistoryEntry(raw))

    return enriched


def _summarize_groups(entries: list[RaceEntry], key: str) -> list[GroupSummary]:
    grouped: dict[tuple[str, str], list[RaceEntry]] = {}
    for entry in entries:
        name = _clean_group_value(entry.raw.get(key))
        if name is None:
            continue
        car_class = _clean_group_value(entry.raw.get("car_class")) or "Sin clase"
        grouped.setdefault((name, car_class), []).append(entry)

    summaries = [_build_group_summary(name, car_class, group) for (name, car_class), group in grouped.items()]
    return sorted(
        summaries,
        key=lambda item: (
            item.avg_position if item.avg_position is not None else 999,
            -item.total_races,
            item.name,
            item.car_class,
        ),
    )


def _build_group_summary(name: str, car_class: str, entries: list[RaceEntry]) -> GroupSummary:
    positions = [int(entry.raw["position"]) for entry in entries if _is_int(entry.raw.get("position"))]
    cars = [str(entry.raw["car"]) for entry in entries if entry.raw.get("car")]
    gains = [
        int(entry.raw["starting_position"]) - int(entry.raw["position"])
        for entry in entries
        if _is_int(entry.raw.get("starting_position")) and _is_int(entry.raw.get("position"))
    ]
    favorite_car = _most_common(cars)
    total_races = len(positions)
    wins = sum(1 for position in positions if position == 1)
    podiums = sum(1 for position in positions if position <= 3)
    return GroupSummary(
        name=name,
        car_class=car_class,
        total_races=total_races,
        wins=wins,
        podiums=podiums,
        avg_position=mean(positions) if positions else None,
        best_finish=min(positions) if positions else None,
        favorite_car=favorite_car,
        win_rate=(wins / total_races * 100) if total_races else 0.0,
        podium_rate=(podiums / total_races * 100) if total_races else 0.0,
        avg_positions_gained=mean(gains) if gains else None,
    )


def _most_common(values: list[str]) -> str | None:
    if not values:
        return None
    counts = Counter(values)
    return min(counts, key=lambda value: (-counts[value], value))


def _clean_group_value(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _clean_compare_value(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lower())


def _is_real_circuit_name(value: Any) -> bool:
    if value is None or value == "" or value == "-":
        return False
    normalized = _clean_circuit_name(value).lower()
    blocked = ["inscripcion al evento", "inscripción al evento", "accediendo", "horizon play"]
    return not any(text in normalized for text in blocked)


def _clean_circuit_name(value: Any) -> str:
    return str(value).strip().lstrip(" ,.;:!-")


def _discipline_from_circuit_name(value: Any) -> str | None:
    if value is None or value == "" or value == "-":
        return None
    normalized = _clean_circuit_name(value).lower()
    normalized = normalized.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
    if "campo a traves" in normalized or "campo atraves" in normalized:
        return "campo a traves"
    if "tierra" in normalized:
        return "tierra"
    if "carretera" in normalized:
        return "carretera"
    if "callej" in normalized:
        return "callejera"
    return None


def _extract_class(text: Any) -> str | None:
    if not text:
        return None
    normalized = str(text).upper()
    normalized = normalized.replace("$2", "S2").replace("§2", "S2")
    normalized = normalized.replace("$1", "S1").replace("§1", "S1")
    normalized = re.sub(r"\bS[IIL]\s*800\b", "S1 800", normalized)
    normalized = normalized.replace("€", "C").replace("¢", "C").replace("©", "C")
    normalized = normalized.replace("AY ", "A ").replace("A Y ", "A ")
    compact = re.sub(r"[^A-Z0-9]", "", normalized)
    compact = re.sub(r"S[IIL]800", "S1800", compact)
    for class_name, score in _KNOWN_CLASSES:
        if f"{class_name}{score}" in compact:
            return f"{class_name} {score}"

    match = _CLASS_RE.search(normalized)
    if not match:
        return None
    class_name = match.group(1).upper()
    score = match.group(2).upper().replace("O", "0")
    if (class_name, score) not in _KNOWN_CLASSES:
        return None
    return f"{class_name} {score}"


def _extract_class_from_crop_path(value: Any) -> str | None:
    if not value:
        return None
    path = Path(str(value))
    cache_key = str(path)
    if cache_key in _CLASS_CROP_CACHE:
        return _CLASS_CROP_CACHE[cache_key]
    if not path.exists():
        _CLASS_CROP_CACHE[cache_key] = None
        return None

    try:
        import cv2

        image = cv2.imread(str(path))
    except Exception:
        _CLASS_CROP_CACHE[cache_key] = None
        return None

    if image is None:
        _CLASS_CROP_CACHE[cache_key] = None
        return None

    class_name = _infer_class_from_crop(image)
    _CLASS_CROP_CACHE[cache_key] = class_name
    return class_name


def _infer_class_from_crop(image: Any) -> str | None:
    try:
        import cv2

        height, width = image.shape[:2]
        badge = image[int(height * 0.15) : int(height * 0.85), 0 : int(width * 0.13)]
        if badge.size == 0:
            return None
        hsv = cv2.cvtColor(badge, cv2.COLOR_BGR2HSV)
        hue = float(hsv[:, :, 0].mean())
        saturation = float(hsv[:, :, 1].mean())
        value = float(hsv[:, :, 2].mean())
    except Exception:
        return None

    if saturation < 80 or value < 120:
        return None
    if 30 <= hue <= 52:
        return "C 500"
    if 92 <= hue <= 115:
        return "S1 800"
    return None


def _is_int(value: Any) -> bool:
    try:
        int(value)
        return True
    except (TypeError, ValueError):
        return False
