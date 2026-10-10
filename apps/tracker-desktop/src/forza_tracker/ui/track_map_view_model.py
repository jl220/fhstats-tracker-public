from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from forza_tracker.localization import tr
from forza_tracker.track_maps import find_track_map


@dataclass(frozen=True)
class TrackMapPanel:
    status: str
    title: str
    subtitle: str
    image_path: Path | None


def build_track_map_panel(
    circuit_name: str | None,
    *,
    discipline: str | None = None,
    root: Path | None = None,
    language: str | None = None,
) -> TrackMapPanel:
    clean_name = str(circuit_name or "").strip()
    if not clean_name:
        return TrackMapPanel(
            status="empty",
            title=tr("map.title"),
            subtitle=tr("map.waiting"),
            image_path=None,
        )

    track_map = find_track_map(clean_name, discipline=discipline, root=root)
    if track_map is None:
        return TrackMapPanel(
            status="missing",
            title=clean_name,
            subtitle=tr("map.missing"),
            image_path=None,
        )

    discipline_key = f"filter.{track_map.discipline.replace('-', '_')}"
    return TrackMapPanel(
        status="found",
        title=track_map.display_name(language),
        subtitle=f"{tr(discipline_key)} · {track_map.source}",
        image_path=track_map.image_path,
    )
