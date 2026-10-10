from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from forza_tracker.localization import tr
from forza_tracker.overlay.event_state import OverlayEventSnapshot
from forza_tracker.ui.track_map_view_model import build_track_map_panel


@dataclass(frozen=True)
class OverlayTrackView:
    slot: int
    name: str
    discipline: str
    image_path: Path | None


@dataclass(frozen=True)
class OverlayRecommendationView:
    label: str
    car_name: str


@dataclass(frozen=True)
class OverlayViewModel:
    event_id: str
    car_class: str
    recognized_count: int
    tracks: tuple[OverlayTrackView, OverlayTrackView, OverlayTrackView]
    recommendations: tuple[OverlayRecommendationView, ...]
    content_signature: str


def build_overlay_view_model(
    snapshot: OverlayEventSnapshot,
    recommendation_payload: dict[str, object] | None,
    *,
    map_root: Path | None = None,
) -> OverlayViewModel:
    payload = recommendation_payload or {}
    matches = _recommendation_matches(snapshot, payload)
    events = payload.get("events") if matches and isinstance(payload.get("events"), list) else []
    disciplines = _event_disciplines(events)
    tracks = []
    for index, circuit in enumerate(snapshot.circuits):
        if not circuit:
            tracks.append(OverlayTrackView(index + 1, "", "", None))
            continue
        discipline = disciplines.get(_normalize(circuit), "")
        panel = build_track_map_panel(circuit, discipline=discipline or None, root=map_root)
        if not discipline and panel.image_path and "·" in panel.subtitle:
            discipline = panel.subtitle.split("·", 1)[0].strip()
        tracks.append(
            OverlayTrackView(
                index + 1,
                panel.title if panel.image_path else circuit,
                _discipline_label(discipline),
                panel.image_path,
            )
        )

    recommendations = _recommendations(payload) if matches else ()
    signature_payload = {
        "event_id": snapshot.event_id,
        "class": snapshot.car_class or "",
        "tracks": [
            {
                "slot": track.slot,
                "name": track.name,
                "discipline": track.discipline,
                "image": _image_signature(track.image_path),
            }
            for track in tracks
        ],
        "recommendations": [item.car_name for item in recommendations],
    }
    signature = hashlib.sha256(
        json.dumps(signature_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return OverlayViewModel(
        event_id=snapshot.event_id,
        car_class=snapshot.car_class or "",
        recognized_count=sum(bool(circuit) for circuit in snapshot.circuits),
        tracks=tuple(tracks),
        recommendations=recommendations,
        content_signature=signature,
    )


def _recommendation_matches(snapshot: OverlayEventSnapshot, payload: dict[str, object]) -> bool:
    if str(payload.get("status") or "") != "ok" or not all(snapshot.circuits):
        return False
    requested = payload.get("requested_event_plan")
    if not isinstance(requested, list) or len(requested) < 3:
        return False
    if _normalize(str(payload.get("car_class") or "")) != _normalize(snapshot.car_class or ""):
        return False
    return tuple(_normalize(str(item)) for item in requested[:3]) == tuple(
        _normalize(item or "") for item in snapshot.circuits
    )


def _event_disciplines(events: list[object]) -> dict[str, str]:
    result = {}
    for item in events:
        if not isinstance(item, dict):
            continue
        name = str(item.get("requested_name") or item.get("name") or "")
        discipline = str(item.get("discipline") or "")
        if name and discipline:
            result[_normalize(name)] = discipline
    return result


def _recommendations(payload: dict[str, object]) -> tuple[OverlayRecommendationView, ...]:
    values = payload.get("recommendations")
    if not isinstance(values, list):
        return ()
    labels = (
        tr("overlay.recommendation.primary"),
        tr("overlay.recommendation.alternative", index=1),
        tr("overlay.recommendation.alternative", index=2),
    )
    result = []
    for label, item in zip(labels, values[:3]):
        car = item.get("car") if isinstance(item, dict) else None
        name = str(car.get("name") or "") if isinstance(car, dict) else ""
        if name:
            result.append(OverlayRecommendationView(label, name))
    return tuple(result)


def _normalize(value: str) -> str:
    plain = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return " ".join("".join(char if char.isalnum() else " " for char in plain.casefold()).split())


def _discipline_label(value: str) -> str:
    return value.replace("_", " ").strip().title()


def _image_signature(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return f"{path}:{path.stat().st_mtime_ns}"
    except OSError:
        return str(path)
