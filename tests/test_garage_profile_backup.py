from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from forza_tracker.garage.profile_backup import (
    ProfileGameRunningError,
    ProfileSourceNotFoundError,
    create_profile_backup,
    find_profile_source,
    load_profile_backup_manifest,
)


def _package_root(local_app_data: Path) -> Path:
    return local_app_data / "Packages" / "Microsoft.ForteBaseGame_8wekyb3d8bbwe"


def _write_pair(local_app_data: Path, user: str, prefix: str, payload: bytes) -> tuple[Path, Path]:
    root = _package_root(local_app_data) / "LocalCache" / "Local" / "LocalStorage_Shared" / user
    root.mkdir(parents=True)
    profile = root / f"{prefix}ProfileBackup"
    meta = root / f"{prefix}Meta"
    profile.write_bytes(payload)
    meta.write_bytes(b"meta-" + payload)
    return profile, meta


def test_find_profile_source_uses_newest_complete_pair(tmp_path: Path) -> None:
    older, older_meta = _write_pair(tmp_path, "User_old", "100", b"old")
    newer, newer_meta = _write_pair(tmp_path, "User_new", "200", b"new")
    older_timestamp = datetime(2026, 7, 20, 11, 0, tzinfo=UTC).timestamp()
    newer_timestamp = datetime(2026, 7, 20, 12, 0, tzinfo=UTC).timestamp()
    os.utime(older, (older_timestamp, older_timestamp))
    os.utime(older_meta, (older_timestamp, older_timestamp))
    os.utime(newer, (newer_timestamp, newer_timestamp))
    os.utime(newer_meta, (newer_timestamp, newer_timestamp))

    source = find_profile_source(tmp_path)

    assert source.profile == newer
    assert source.meta == newer_meta
    assert source.prefix == "200"


def test_find_profile_source_rejects_incomplete_pair(tmp_path: Path) -> None:
    root = _package_root(tmp_path) / "LocalCache" / "Local" / "LocalStorage_Shared" / "User_one"
    root.mkdir(parents=True)
    (root / "100ProfileBackup").write_bytes(b"profile")

    with pytest.raises(ProfileSourceNotFoundError):
        find_profile_source(tmp_path)


def test_load_profile_backup_manifest_returns_none_when_missing(tmp_path: Path) -> None:
    assert load_profile_backup_manifest(tmp_path / "profile_backup") is None


def test_create_profile_backup_rejects_running_game(tmp_path: Path) -> None:
    _write_pair(tmp_path / "source", "User_one", "100", b"profile")

    with pytest.raises(ProfileGameRunningError):
        create_profile_backup(
            tmp_path / "source",
            tmp_path / "tracker" / "profile_backup",
            is_game_running=lambda: True,
        )

    assert not (tmp_path / "tracker" / "profile_backup").exists()


def test_create_profile_backup_copies_and_verifies_pair(tmp_path: Path) -> None:
    profile, meta = _write_pair(tmp_path / "source", "User_one", "100", b"profile")
    destination = tmp_path / "tracker" / "profile_backup"

    manifest = create_profile_backup(
        tmp_path / "source",
        destination,
        is_game_running=lambda: False,
    )

    assert manifest.verified is True
    assert manifest.total_size == profile.stat().st_size + meta.stat().st_size
    assert (destination / "ProfileBackup.bin").read_bytes() == profile.read_bytes()
    assert (destination / "Meta.bin").read_bytes() == meta.read_bytes()
    assert load_profile_backup_manifest(destination) == manifest


def test_create_profile_backup_preserves_old_copy_if_game_starts_during_validation(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    _write_pair(source_root, "User_one", "100", b"first")
    destination = tmp_path / "tracker" / "profile_backup"
    create_profile_backup(source_root, destination, is_game_running=lambda: False)
    previous = (destination / "ProfileBackup.bin").read_bytes()
    _write_pair(source_root, "User_two", "200", b"second")
    states = iter((False, True))

    with pytest.raises(ProfileGameRunningError):
        create_profile_backup(source_root, destination, is_game_running=lambda: next(states))

    assert (destination / "ProfileBackup.bin").read_bytes() == previous
