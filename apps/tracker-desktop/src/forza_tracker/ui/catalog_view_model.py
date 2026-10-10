from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


ALL_CIRCUITS = "all"


@dataclass(frozen=True)
class MetaTierSection:
    label: str
    tier: str
    rows: tuple[Mapping[str, Any], ...]


def rivals_circuit_options(rows: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    circuits = sorted({str(row.get("event") or "").strip() for row in rows} - {""}, key=str.casefold)
    return (ALL_CIRCUITS, *circuits)


def filter_rivals_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    circuit: str = ALL_CIRCUITS,
    car_class: str = "all",
    query: str = "",
) -> tuple[Mapping[str, Any], ...]:
    normalized_query = query.strip().casefold()
    filtered: list[Mapping[str, Any]] = []
    for row in rows:
        if circuit != ALL_CIRCUITS and row.get("event") != circuit:
            continue
        if car_class != "all" and row.get("class") != car_class:
            continue
        searchable = f"{row.get('event', '')} {row.get('player', '')} {row.get('car', '')}".casefold()
        if normalized_query and normalized_query not in searchable:
            continue
        filtered.append(row)
    return tuple(filtered)


def build_meta_tier_sections(rows: Sequence[Mapping[str, Any]]) -> tuple[MetaTierSection, ...]:
    sections: list[MetaTierSection] = []
    for tier, label in (("S+", "Meta+"), ("S", "Meta"), ("A", "Offmeta")):
        tier_rows = tuple(row for row in rows if row.get("tier") == tier)
        if tier_rows:
            sections.append(MetaTierSection(label=label, tier=tier, rows=tier_rows))
    return tuple(sections)
