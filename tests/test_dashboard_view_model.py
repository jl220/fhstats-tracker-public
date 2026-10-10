from __future__ import annotations

from datetime import datetime, timezone

import pytest

from forza_tracker.localization import configure_language
from forza_tracker.ui.dashboard_view_model import build_dashboard_view_model
from forza_tracker.ui.history import HistoryEntry, build_race_history


def _race(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "screen_type": "race_result",
        "timestamp": "2026-07-12T18:10:00+00:00",
        "event_number": 2,
        "event_total": 3,
        "circuit_name": "Circuito de autopista",
        "starting_position": 9,
        "position": 1,
        "car": "1984 Honda CRXMugen",
        "car_class": "B 600",
        "total_time": "03:12.345",
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def restore_english() -> None:
    configure_language("en")
    yield
    configure_language("en")


def test_dashboard_view_model_has_empty_first_run_state() -> None:
    view_model = build_dashboard_view_model([], {}, {})

    assert view_model.hero_title == "No saved races"
    assert view_model.hero_subtitle == "Start capture and finish a race to see your session here."
    assert view_model.cards["last_race"].value == "-"
    assert view_model.cards["tracker"].value == "No capture"
    assert view_model.cards["telemetry"].value == "Waiting"
    assert view_model.cards["telemetry"].detail == "No UDP signal"
    assert view_model.cards["telemetry"].tone == "warning"


def test_dashboard_view_model_has_loading_state_while_capture_runs_without_races() -> None:
    view_model = build_dashboard_view_model(
        [],
        {"state": "running"},
        {"state": "listening", "connected": False},
    )

    assert view_model.hero_title == "Capture active"
    assert view_model.hero_subtitle == "Waiting for the first race detected by OCR or telemetry."
    assert view_model.cards["tracker"].value == "Capturing"
    assert view_model.cards["tracker"].tone == "live"
    assert view_model.cards["telemetry"].value == "Waiting"
    assert view_model.cards["telemetry"].detail == "No UDP signal"
    assert view_model.cards["telemetry"].tone == "warning"


def test_dashboard_view_model_summarizes_latest_race() -> None:
    races = build_race_history([HistoryEntry(_race())])

    view_model = build_dashboard_view_model(races, {}, {})

    assert view_model.hero_title == "Victory at Highway Circuit"
    assert view_model.hero_subtitle == "Event 2/3 · B 600 · 03:12.345"
    assert view_model.cards["last_race"].value == "P1"
    assert view_model.cards["last_car"].value == "1984 Honda CRXMugen"
    assert view_model.cards["circuit"].value == "Highway Circuit"
    assert view_model.cards["car_class"].value == "B 600"


def test_dashboard_view_model_reports_runtime_and_telemetry_state() -> None:
    now = datetime(2026, 7, 12, 18, 10, 2, tzinfo=timezone.utc)
    races = build_race_history([HistoryEntry(_race(position=4))])
    capture_status = {"state": "running"}
    telemetry_status = {
        "connected": True,
        "last_packet_at": "2026-07-12T18:10:01+00:00",
        "car_display_name": "1984 Honda CRXMugen",
        "race_position": 4,
    }

    view_model = build_dashboard_view_model(
        races,
        capture_status,
        telemetry_status,
        now=now,
    )

    assert view_model.cards["tracker"].value == "Capturing"
    assert view_model.cards["tracker"].detail == "OCR and detector active"
    assert view_model.cards["telemetry"].value == "Connected"
    assert view_model.cards["telemetry"].detail == "P4 · 1984 Honda CRXMugen"


def test_dashboard_view_model_hides_telemetry_error_state() -> None:
    view_model = build_dashboard_view_model(
        [],
        {},
        {"state": "error", "connected": False, "error_message": "socket error"},
    )

    telemetry = view_model.cards["telemetry"]
    assert telemetry.value == "Waiting"
    assert telemetry.detail == "No UDP signal"
    assert telemetry.tone == "warning"
    assert "error" not in f"{telemetry.value} {telemetry.detail}".lower()


def test_dashboard_view_model_presents_listening_telemetry_as_waiting() -> None:
    view_model = build_dashboard_view_model(
        [],
        {},
        {"state": "listening", "connected": False},
    )

    assert view_model.cards["telemetry"].value == "Waiting"
    assert view_model.cards["telemetry"].detail == "No UDP signal"
    assert view_model.cards["telemetry"].tone == "warning"


def test_dashboard_view_model_keeps_disabled_telemetry_neutral() -> None:
    view_model = build_dashboard_view_model(
        [],
        {},
        {"state": "disabled", "connected": False},
    )

    telemetry = view_model.cards["telemetry"]
    assert telemetry.value == "Disabled"
    assert telemetry.tone == "neutral"


def test_dashboard_view_model_presents_stale_connected_telemetry_as_waiting() -> None:
    now = datetime(2026, 7, 12, 18, 10, 8, tzinfo=timezone.utc)
    view_model = build_dashboard_view_model(
        [],
        {},
        {
            "connected": True,
            "last_packet_at": "2026-07-12T18:10:00+00:00",
            "car_display_name": "1984 Honda CRXMugen",
            "race_position": 4,
        },
        now=now,
    )

    telemetry = view_model.cards["telemetry"]
    assert telemetry.value == "Waiting"
    assert telemetry.detail == "No UDP signal"
    assert telemetry.tone == "warning"


def test_dashboard_view_model_remains_available_in_spanish() -> None:
    configure_language("es")

    view_model = build_dashboard_view_model([], {}, {"state": "disabled"})

    assert view_model.hero_title == "Sin carreras guardadas"
    assert view_model.cards["telemetry"].value == "Desactivada"
