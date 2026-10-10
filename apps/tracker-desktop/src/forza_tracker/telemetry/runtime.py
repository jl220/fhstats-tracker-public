from __future__ import annotations

import threading
from typing import Any, Callable

from forza_tracker.telemetry.listener import listen_forza_horizon_6_udp
from forza_tracker.telemetry.status import write_telemetry_status


TelemetryListener = Callable[..., None]
TelemetryStatusWriter = Callable[..., None]


def start_telemetry_listener_if_enabled(
    telemetry_settings: Any,
    *,
    listener: TelemetryListener = listen_forza_horizon_6_udp,
    status_writer: TelemetryStatusWriter = write_telemetry_status,
) -> threading.Thread | None:
    if not bool(getattr(telemetry_settings, "enabled", False)):
        status_writer("disabled", message="FH6 telemetry disabled")
        return None

    host = str(getattr(telemetry_settings, "host", "0.0.0.0"))
    port = int(getattr(telemetry_settings, "port", 9999))

    thread = threading.Thread(
        target=_run_listener,
        kwargs={
            "listener": listener,
            "status_writer": status_writer,
            "host": host,
            "port": port,
        },
        name="fhstats-fh6-telemetry",
        daemon=True,
    )
    thread.start()
    return thread


def _run_listener(
    *,
    listener: TelemetryListener,
    status_writer: TelemetryStatusWriter,
    host: str,
    port: int,
) -> None:
    try:
        listener(
            host=host,
            port=port,
            duration_seconds=0,
            emit_summary=False,
        )
    except OSError as error:
        status_writer("error", message=f"FH6 telemetry listener failed: {error}")
