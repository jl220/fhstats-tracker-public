from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import unicodedata

from forza_tracker.localization import tr
from forza_tracker.ui.track_map_view_model import TrackMapPanel, build_track_map_panel


@dataclass(frozen=True)
class EventProgressRow:
    index: int
    map_panel: TrackMapPanel


def format_event_times(total_time: object, best_lap: object) -> str:
    total = _optional_text(total_time)
    lap = _optional_text(best_lap)
    if total and lap:
        return f"{total} · {tr('event.lap_short')} {lap}"
    if lap:
        return f"{tr('event.lap_short')} {lap}"
    return total or "-"


def build_event_progress_rows(
    slots: Sequence[Mapping[str, Any]],
    *,
    recommendation_events: Sequence[Mapping[str, Any]] = (),
    map_root: Path | None = None,
) -> tuple[EventProgressRow, ...]:
    rows: list[EventProgressRow] = []
    for slot in slots:
        circuit = str(slot.get("circuit") or "").strip()
        discipline = _optional_text(slot.get("discipline"))
        if discipline is None:
            discipline = _recommendation_discipline(circuit, recommendation_events)
        map_panel = build_track_map_panel(circuit, discipline=discipline, root=map_root)
        rows.append(
            EventProgressRow(
                index=int(slot.get("index") or 0),
                map_panel=map_panel,
            )
        )
    return tuple(rows)


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _recommendation_discipline(
    circuit: str,
    recommendation_events: Sequence[Mapping[str, Any]],
) -> str | None:
    normalized_circuit = _normalize_name(circuit)
    if not normalized_circuit:
        return None
    for event in recommendation_events:
        if event.get("matched") is not True:
            continue
        candidates = (event.get("requested_name"), event.get("name"))
        if normalized_circuit not in {_normalize_name(candidate) for candidate in candidates}:
            continue
        return _optional_text(event.get("discipline"))
    return None


def _normalize_name(value: object) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    return " ".join(
        "".join(character for character in decomposed if not unicodedata.combining(character)).casefold().split()
    )
