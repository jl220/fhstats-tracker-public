from __future__ import annotations

from threading import Event
from types import SimpleNamespace
from typing import Any

from forza_tracker.telemetry.runtime import start_telemetry_listener_if_enabled


def test_disabled_telemetry_does_not_start_listener() -> None:
    statuses: list[tuple[str, dict[str, Any]]] = []

    thread = start_telemetry_listener_if_enabled(
        SimpleNamespace(enabled=False, host="127.0.0.1", port=9999),
        listener=lambda **_: None,
        status_writer=lambda state, **kwargs: statuses.append((state, kwargs)),
    )

    assert thread is None
    assert statuses == [("disabled", {"message": "FH6 telemetry disabled"})]


def test_enabled_telemetry_starts_listener_thread_with_settings() -> None:
    finished = Event()
    received: dict[str, Any] = {}

    def listener(**kwargs: Any) -> None:
        received.update(kwargs)
        finished.set()

    thread = start_telemetry_listener_if_enabled(
        SimpleNamespace(enabled=True, host="127.0.0.1", port=10001),
        listener=listener,
        status_writer=lambda *_args, **_kwargs: None,
    )

    assert thread is not None
    thread.join(timeout=1)
    assert finished.is_set()
    assert received["host"] == "127.0.0.1"
    assert received["port"] == 10001
    assert received["duration_seconds"] == 0
    assert received["emit_summary"] is False


def test_listener_errors_are_written_to_runtime_status() -> None:
    statuses: list[tuple[str, dict[str, Any]]] = []

    def listener(**_kwargs: Any) -> None:
        raise OSError("port already in use")

    thread = start_telemetry_listener_if_enabled(
        SimpleNamespace(enabled=True, host="127.0.0.1", port=10001),
        listener=listener,
        status_writer=lambda state, **kwargs: statuses.append((state, kwargs)),
    )

    assert thread is not None
    thread.join(timeout=1)
    assert statuses[0][0] == "error"
    assert "port already in use" in statuses[0][1]["message"]
