from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from forza_tracker.localization import normalize_language
from forza_tracker.paths import application_root, user_data_root


def default_config_path() -> Path:
    return application_root() / "config" / "tracker.yaml"


def local_config_path() -> Path:
    return user_data_root() / "config" / "tracker.local.yaml"


def load_editable_config(
    *,
    local_path: Path | None = None,
    default_path: Path | None = None,
) -> dict[str, Any]:
    local = local_path or local_config_path()
    default = default_path or default_config_path()
    source = local if local.exists() else default
    with source.open("r", encoding="utf-8-sig") as file:
        data = yaml.safe_load(file) or {}
    return data if isinstance(data, dict) else {}


def write_local_config(data: dict[str, Any], *, local_path: Path | None = None) -> Path:
    destination = local_path or local_config_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="\n") as file:
        yaml.safe_dump(data, file, sort_keys=False, allow_unicode=True)
    return destination


def save_language(
    language: str,
    *,
    local_path: Path | None = None,
    default_path: Path | None = None,
) -> Path:
    normalized = normalize_language(language)
    if normalized is None:
        raise ValueError(f"Unsupported language: {language}")
    data = load_editable_config(local_path=local_path, default_path=default_path)
    app = data.setdefault("app", {})
    if not isinstance(app, dict):
        app = {}
        data["app"] = app
    app["language"] = normalized
    return write_local_config(data, local_path=local_path)
