from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forza_tracker.paths import user_data_root


RECOMMENDATION_PATH = user_data_root() / "data" / "runtime" / "recommendation.json"


def write_recommendation(payload: dict[str, Any], path: Path = RECOMMENDATION_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    value = dict(payload)
    value["updated_at"] = datetime.now(timezone.utc).isoformat()
    handle, temporary_name = tempfile.mkstemp(
        prefix="recommendation_",
        suffix=".json",
        dir=path.parent,
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False, indent=2)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def read_recommendation(path: Path = RECOMMENDATION_PATH) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            value = json.load(file)
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def recommendation_signature(path: Path = RECOMMENDATION_PATH) -> tuple[int, int] | None:
    try:
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns
    except OSError:
        return None
