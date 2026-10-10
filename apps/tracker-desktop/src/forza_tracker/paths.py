from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists() and (parent / "config" / "tracker.yaml").exists():
            return parent
    return Path(__file__).resolve().parents[4]


def user_data_root() -> Path:
    override = os.environ.get("FHSTATS_TRACKER_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "FHStatsTracker"
    return Path.home() / "AppData" / "Local" / "FHStatsTracker"


def ensure_user_data_layout() -> Path:
    root = user_data_root()
    for relative in ("config", "data/history", "data/runtime", "data/recordings", "captures", "garage", "logs"):
        (root / relative).mkdir(parents=True, exist_ok=True)

    if root != application_root():
        _copy_if_missing(
            application_root() / "config" / "tracker.local.yaml",
            root / "config" / "tracker.local.yaml",
        )
        _merge_jsonl(
            application_root() / "data" / "history" / "results.jsonl",
            root / "data" / "history" / "results.jsonl",
        )
        captures_destination = root / "captures"
        if not any(captures_destination.iterdir()):
            _copy_tree_contents(application_root() / "captures", captures_destination)
    return root


def resolve_resource_path(path: Path) -> Path:
    return path if path.is_absolute() else application_root() / path


def resolve_data_path(path: Path) -> Path:
    return path if path.is_absolute() else user_data_root() / path


def _copy_if_missing(source: Path, destination: Path) -> None:
    if source.exists() and not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _copy_tree_contents(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        target = destination / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        elif not target.exists():
            shutil.copy2(item, target)


def _merge_jsonl(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    existing_lines = (
        [line.strip() for line in destination.read_text(encoding="utf-8").splitlines() if line.strip()]
        if destination.exists()
        else []
    )
    seen = set(existing_lines)
    merged = list(existing_lines)
    for line in source.read_text(encoding="utf-8").splitlines():
        normalized = line.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            merged.append(normalized)
    if merged:
        destination.write_text("\n".join(merged) + "\n", encoding="utf-8")
