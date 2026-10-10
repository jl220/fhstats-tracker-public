from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forza_tracker.paths import user_data_root


CONTROL_PATH = user_data_root() / "data" / "runtime" / "recording-command.json"
STATUS_PATH = user_data_root() / "data" / "runtime" / "recording-status.json"


def send_recording_command(command: str, path: Path = CONTROL_PATH) -> None:
    _write_json(
        path,
        {
            "id": str(uuid.uuid4()),
            "command": command,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def consume_recording_command(path: Path = CONTROL_PATH) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        path.unlink(missing_ok=True)
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def write_recording_status(
    *,
    recording: bool,
    mode: str = "idle",
    output_path: str | None = None,
    message: str = "",
    path: Path = STATUS_PATH,
) -> None:
    _write_json(
        path,
        {
            "recording": recording,
            "mode": mode,
            "output_path": output_path,
            "message": message,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def read_recording_status(path: Path = STATUS_PATH) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=path.stem, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
