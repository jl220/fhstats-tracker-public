from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable

from forza_tracker.models.result import ParsedResult
from forza_tracker.models.screen_type import ScreenType
from forza_tracker.telemetry.status import read_telemetry_status


def apply_latest_telemetry_context(
    result: ParsedResult,
    telemetry_settings: Any,
    *,
    status_reader: Callable[[], dict[str, Any]] = read_telemetry_status,
    now: datetime | None = None,
) -> ParsedResult:
    enabled = bool(getattr(telemetry_settings, "enabled", False))
    if not enabled:
        return result

    stale_after_seconds = float(getattr(telemetry_settings, "stale_after_seconds", 3.0))
    return apply_telemetry_context(
        result,
        status_reader(),
        enabled=enabled,
        stale_after_seconds=stale_after_seconds,
        now=now,
    )


def apply_telemetry_context(
    result: ParsedResult,
    status: dict[str, Any],
    *,
    enabled: bool,
    stale_after_seconds: float,
    now: datetime | None = None,
) -> ParsedResult:
    if not enabled or result.screen_type != ScreenType.RACE_RESULT:
        return result
    if not _is_fresh_connected_status(status, stale_after_seconds=stale_after_seconds, now=now):
        return result

    telemetry_position = _positive_int(status.get("race_position"))
    telemetry_car = _non_empty_text(status.get("car_display_name"))
    telemetry_class = _format_car_class(status.get("car_class"), status.get("performance_index"))

    updates: dict[str, Any] = {}
    if telemetry_position is not None:
        updates["position"] = telemetry_position
    if telemetry_car is not None:
        updates["car"] = telemetry_car
    if telemetry_class is not None:
        updates["car_class"] = telemetry_class

    return replace(result, **updates) if updates else result


def _is_fresh_connected_status(
    status: dict[str, Any],
    *,
    stale_after_seconds: float,
    now: datetime | None,
) -> bool:
    if not status.get("connected") and status.get("state") != "connected":
        return False

    packet_time = _parse_datetime(status.get("last_packet_at") or status.get("updated_at"))
    if packet_time is None:
        return False

    reference_time = now or datetime.now(timezone.utc)
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=timezone.utc)

    return (reference_time - packet_time).total_seconds() <= stale_after_seconds


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _non_empty_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _format_car_class(car_class: Any, performance_index: Any) -> str | None:
    label = _non_empty_text(car_class)
    pi = _positive_int(performance_index)
    if label is None or pi is None:
        return None
    return f"{label.upper()} {pi}"
