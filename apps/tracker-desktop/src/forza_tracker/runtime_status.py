from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forza_tracker.paths import user_data_root


STATUS_PATH = user_data_root() / "data" / "runtime" / "capture_status.json"


def write_status(state: str, **values: Any) -> None:
    path = STATUS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = read_status()
    payload.update(values)
    payload.update(
        {
            "state": state,
            "pid": os.getpid(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    handle, temporary_name = tempfile.mkstemp(
        prefix="capture_status_",
        suffix=".json",
        dir=path.parent,
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=True, indent=2)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def read_status(path: Path = STATUS_PATH) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            value = json.load(file)
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def sanitize_error_message(message: str, limit: int = 1000) -> str:
    cleaned = re.sub(
        r"(?i)(?:[a-z]:\\|/)(?:[^ \r\n\t\"']+[\\/])+[^ \r\n\t\"']*",
        "[local-path]",
        message,
    )
    cleaned = " ".join(cleaned.split())
    return cleaned[:limit] or "Unknown error"
