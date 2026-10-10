from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forza_tracker.car_catalog.fh6 import lookup_fh6_car
from forza_tracker.paths import user_data_root
from forza_tracker.telemetry.forza_udp import ForzaHorizonTelemetry


STATUS_PATH = user_data_root() / "data" / "runtime" / "telemetry-status.json"


def write_telemetry_status(
    state: str,
    *,
    packet: ForzaHorizonTelemetry | None = None,
    message: str = "",
    remote: str | None = None,
    path: Path = STATUS_PATH,
) -> None:
    payload: dict[str, Any] = {
        "state": state,
        "connected": state == "connected",
        "game": "fh6",
        "message": message,
        "remote": remote,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if packet is not None:
        payload.update(packet.to_json_dict())
        car = lookup_fh6_car(packet.car_ordinal)
        if car is not None:
            payload.update(
                {
                    "car_display_name": car.display_name,
                    "car_name_confidence": car.confidence,
                }
            )
        payload["last_packet_at"] = payload["updated_at"]
    _write_json(path, payload)


def read_telemetry_status(path: Path = STATUS_PATH) -> dict[str, Any]:
    # Windows can briefly deny opening the destination during os.replace.
    # Retry the actual read; never substitute an older cached packet.
    for attempt in range(4):
        try:
            with path.open("r", encoding="utf-8") as file:
                payload = json.load(file)
            return payload if isinstance(payload, dict) else {}
        except (PermissionError, FileNotFoundError):
            if attempt < 3:
                time.sleep(0.002)
        except (OSError, ValueError):
            return {}
    return {}


def telemetry_status_signature(path: Path = STATUS_PATH) -> tuple[int, int] | None:
    try:
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns
    except OSError:
        return None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=path.stem, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
        _replace_with_retry(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _replace_with_retry(
    source: str,
    destination: Path,
    *,
    attempts: int = 5,
    delay_seconds: float = 0.02,
) -> None:
    for attempt in range(attempts):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(delay_seconds)
