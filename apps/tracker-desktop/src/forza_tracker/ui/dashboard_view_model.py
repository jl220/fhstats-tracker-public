from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from forza_tracker.localization import tr
from forza_tracker.track_maps import find_track_map
from forza_tracker.ui.history import RaceEntry


@dataclass(frozen=True)
class DashboardCard:
    label: str
    value: str
    detail: str
    tone: str = "neutral"


@dataclass(frozen=True)
class DashboardViewModel:
    hero_title: str
    hero_subtitle: str
    cards: dict[str, DashboardCard]


def build_dashboard_view_model(
    races: list[RaceEntry],
    capture_status: dict[str, Any],
    telemetry_status: dict[str, Any],
    *,
    now: datetime | None = None,
) -> DashboardViewModel:
    latest = races[-1] if races else None
    cards = {
        "last_race": _last_race_card(latest),
        "last_car": _latest_value_card(tr("dashboard.last_car"), latest, "car"),
        "circuit": _latest_value_card(tr("history.circuit"), latest, "circuit_name", circuit=True),
        "car_class": _latest_value_card(tr("history.class"), latest, "car_class"),
        "tracker": _tracker_card(capture_status),
        "telemetry": _telemetry_card(telemetry_status, now=now),
    }

    if latest is None and _is_capture_running(capture_status):
        return DashboardViewModel(
            hero_title=tr("dashboard.active"),
            hero_subtitle=tr("dashboard.waiting_first_race"),
            cards=cards,
        )

    if latest is None:
        return DashboardViewModel(
            hero_title=tr("dashboard.no_races"),
            hero_subtitle=tr("dashboard.no_races_detail"),
            cards=cards,
        )

    circuit = _circuit_display(latest.raw.get("circuit_name"))
    car_class = _clean_value(latest.raw.get("car_class"))
    time = _clean_value(latest.raw.get("total_time"))
    position = _positive_int(latest.raw.get("position"))
    result = (
        tr("dashboard.victory")
        if position == 1
        else tr("dashboard.position_result", position=position)
        if position
        else tr("dashboard.last_race")
    )
    return DashboardViewModel(
        hero_title=tr("dashboard.result_at", result=result, circuit=circuit) if circuit != "-" else result,
        hero_subtitle=tr(
            "dashboard.event_summary",
            event=_event_label(latest.raw.get("event_number"), latest.raw.get("event_total")),
            car_class=car_class,
            time=time,
        ),
        cards=cards,
    )


def _last_race_card(latest: RaceEntry | None) -> DashboardCard:
    if latest is None:
        return DashboardCard(tr("dashboard.last_race"), "-", tr("dashboard.waiting_results"))
    position = _positive_int(latest.raw.get("position"))
    value = f"P{position}" if position is not None else "-"
    return DashboardCard(
        tr("dashboard.last_race"),
        value,
        _event_label(latest.raw.get("event_number"), latest.raw.get("event_total")),
        "win" if position == 1 else "podium" if position is not None and position <= 3 else "neutral",
    )


def _latest_value_card(
    label: str, latest: RaceEntry | None, key: str, *, circuit: bool = False
) -> DashboardCard:
    if latest is None:
        return DashboardCard(label, "-", tr("dashboard.no_data"))
    value = _circuit_display(latest.raw.get(key)) if circuit else _clean_value(latest.raw.get(key))
    return DashboardCard(label, value, latest.display_time)


def _tracker_card(capture_status: dict[str, Any]) -> DashboardCard:
    state = str(capture_status.get("state") or "").lower()
    if _is_capture_running(capture_status):
        return DashboardCard("Tracker", tr("dashboard.capturing"), tr("dashboard.detectors_active"), "live")
    if state == "error":
        return DashboardCard("Tracker", tr("dashboard.error"), _clean_value(capture_status.get("error_message")), "danger")
    if state in {"stopped", "idle"}:
        return DashboardCard("Tracker", tr("dashboard.stopped"), tr("dashboard.ready_to_start"), "neutral")
    return DashboardCard("Tracker", tr("dashboard.no_capture"), tr("capture.waiting_game"), "neutral")


def _is_capture_running(capture_status: dict[str, Any]) -> bool:
    state = str(capture_status.get("state") or "").lower()
    return state in {"running", "capturing", "ok", "starting", "listening"}


def _telemetry_card(telemetry_status: dict[str, Any], *, now: datetime | None) -> DashboardCard:
    if telemetry_status.get("state") == "disabled":
        return DashboardCard(tr("dashboard.telemetry"), tr("dashboard.disabled"), tr("dashboard.classic_ocr"), "neutral")
    waiting_card = DashboardCard(
        tr("dashboard.telemetry"), tr("dashboard.waiting"), tr("dashboard.no_udp_signal"), "warning"
    )
    if telemetry_status.get("state") == "error" or not telemetry_status.get("connected"):
        return waiting_card

    age = _status_age_seconds(telemetry_status, now=now)
    if age is not None and age > 5:
        return waiting_card

    position = _positive_int(telemetry_status.get("race_position"))
    car = _clean_value(telemetry_status.get("car_display_name"))
    detail = f"P{position} · {car}" if position is not None else car
    return DashboardCard(tr("dashboard.telemetry"), tr("dashboard.connected"), detail, "live")


def _circuit_display(value: Any) -> str:
    clean = _clean_value(value)
    if clean == "-":
        return clean
    track_map = find_track_map(clean)
    return track_map.display_name() if track_map is not None else clean


def _status_age_seconds(status: dict[str, Any], *, now: datetime | None) -> float | None:
    value = status.get("last_packet_at") or status.get("updated_at")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return (reference.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()


def _event_label(event_number: Any, event_total: Any) -> str:
    if event_number in {None, ""}:
        return "-"
    if event_total in {None, ""}:
        return str(event_number)
    return f"{event_number}/{event_total}"


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _clean_value(value: Any) -> str:
    if value in {None, ""}:
        return "-"
    return str(value)
