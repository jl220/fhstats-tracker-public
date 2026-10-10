from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from forza_tracker.overlay.event_state import OverlayEventSnapshot
from forza_tracker.overlay.model import build_overlay_view_model
from forza_tracker.localization import configure_language


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def _snapshot(circuits=("A", "B", "C")) -> OverlayEventSnapshot:
    return OverlayEventSnapshot(1, "event-1", NOW, NOW, "registration", "registration", "S1 900", circuits)


def test_overlay_model_keeps_three_slots_and_omits_missing_map_messages(monkeypatch) -> None:
    def fake_panel(name, **_kwargs):
        if name == "A":
            return SimpleNamespace(title="Circuit A", subtitle="Road · local", image_path=Path("a.png"))
        return SimpleNamespace(title=name or "", subtitle="Mapa local no disponible", image_path=None)

    monkeypatch.setattr("forza_tracker.overlay.model.build_track_map_panel", fake_panel)
    view = build_overlay_view_model(_snapshot(("A", None, "C")), None)

    assert view.recognized_count == 2
    assert len(view.tracks) == 3
    assert view.tracks[0].image_path == Path("a.png")
    assert view.tracks[1].name == ""
    assert view.tracks[1].discipline == ""
    assert view.tracks[2].image_path is None


def test_overlay_model_accepts_only_matching_event_wide_recommendation(monkeypatch) -> None:
    monkeypatch.setattr(
        "forza_tracker.overlay.model.build_track_map_panel",
        lambda name, **_kwargs: SimpleNamespace(title=name, subtitle="Road · local", image_path=None),
    )
    payload = {
        "status": "ok",
        "car_class": "S1 900",
        "requested_event_plan": ["A", "B", "C"],
        "events": [
            {"requested_name": "A", "discipline": "road"},
            {"requested_name": "B", "discipline": "dirt"},
            {"requested_name": "C", "discipline": "cross_country"},
        ],
        "recommendations": [
            {"car": {"name": "Car One"}, "score": 99},
            {"car": {"name": "Car Two"}, "score": 88},
            {"car": {"name": "Car Three"}, "score": 77},
        ],
    }

    view = build_overlay_view_model(_snapshot(), payload)
    stale = build_overlay_view_model(_snapshot(), {**payload, "car_class": "A 800"})

    assert [item.label for item in view.recommendations] == [
        "Primary",
        "Alternative 1",
        "Alternative 2",
    ]
    assert [item.car_name for item in view.recommendations] == ["Car One", "Car Two", "Car Three"]
    assert not hasattr(view.recommendations[0], "score")
    assert [track.discipline for track in view.tracks] == ["Road", "Dirt", "Cross Country"]
    assert stale.recommendations == ()


def test_overlay_recommendation_labels_follow_selected_language(monkeypatch) -> None:
    configure_language("es")
    monkeypatch.setattr(
        "forza_tracker.overlay.model.build_track_map_panel",
        lambda name, **_kwargs: SimpleNamespace(title=name, subtitle="", image_path=None),
    )
    payload = {
        "status": "ok",
        "car_class": "S1 900",
        "requested_event_plan": ["A", "B", "C"],
        "recommendations": [{"car": {"name": "Car One"}}],
    }

    view = build_overlay_view_model(_snapshot(), payload)

    assert view.recommendations[0].label == "Principal"


def test_content_signature_ignores_snapshot_poll_timestamp(monkeypatch) -> None:
    monkeypatch.setattr(
        "forza_tracker.overlay.model.build_track_map_panel",
        lambda name, **_kwargs: SimpleNamespace(title=name, subtitle="", image_path=None),
    )
    first = _snapshot()
    second = replace(first, updated_at=NOW + timedelta(seconds=2))

    assert build_overlay_view_model(first, None).content_signature == build_overlay_view_model(
        second, None
    ).content_signature


def test_overlay_resolves_spanish_and_english_circuit_names_to_same_maps() -> None:
    configure_language("en")
    spanish = _snapshot(("Circuito de autopista", "Tramo de la cascada", "El Titán"))
    english = _snapshot(("Highway Circuit", "Waterfall Trail", "The Titan"))

    spanish_view = build_overlay_view_model(spanish, None)
    english_view = build_overlay_view_model(english, None)

    assert all(track.image_path is not None for track in spanish_view.tracks)
    assert [track.image_path for track in spanish_view.tracks] == [
        track.image_path for track in english_view.tracks
    ]
    assert [track.name for track in spanish_view.tracks] == [
        "Highway Circuit",
        "Waterfall Trail",
        "The Titan",
    ]
