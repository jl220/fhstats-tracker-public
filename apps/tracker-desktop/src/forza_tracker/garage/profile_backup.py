from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


FH6_PACKAGE_FAMILY = "Microsoft.ForteBaseGame_8wekyb3d8bbwe"


class ProfileBackupError(RuntimeError):
    pass


class ProfileSourceNotFoundError(ProfileBackupError):
    pass


class ProfileGameRunningError(ProfileBackupError):
    pass


class ProfileValidationError(ProfileBackupError):
    pass


@dataclass(frozen=True)
class ProfileSourcePair:
    profile: Path
    meta: Path
    prefix: str


@dataclass(frozen=True)
class ProfileFileManifest:
    stored_name: str
    source_name: str
    size: int
    sha256: str
    modified_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "stored_name": self.stored_name,
            "source_name": self.source_name,
            "size": self.size,
            "sha256": self.sha256,
            "modified_at": self.modified_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> ProfileFileManifest:
        return cls(
            stored_name=str(payload["stored_name"]),
            source_name=str(payload["source_name"]),
            size=int(payload["size"]),
            sha256=str(payload["sha256"]),
            modified_at=str(payload["modified_at"]),
        )


@dataclass(frozen=True)
class ProfileBackupManifest:
    schema_version: int
    created_at: str
    package_family: str
    verified: bool
    files: tuple[ProfileFileManifest, ...]

    @property
    def total_size(self) -> int:
        return sum(item.size for item in self.files)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "package_family": self.package_family,
            "verified": self.verified,
            "files": [item.to_dict() for item in self.files],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> ProfileBackupManifest:
        raw_files = payload["files"]
        if not isinstance(raw_files, list):
            raise TypeError("files must be a list")
        return cls(
            schema_version=int(payload["schema_version"]),
            created_at=str(payload["created_at"]),
            package_family=str(payload["package_family"]),
            verified=bool(payload["verified"]),
            files=tuple(ProfileFileManifest.from_dict(item) for item in raw_files if isinstance(item, dict)),
        )


def find_profile_source(local_app_data: Path) -> ProfileSourcePair:
    shared = (
        local_app_data
        / "Packages"
        / FH6_PACKAGE_FAMILY
        / "LocalCache"
        / "Local"
        / "LocalStorage_Shared"
    )
    candidates: list[ProfileSourcePair] = []
    for profile in shared.glob("User_*/*ProfileBackup"):
        prefix = profile.name.removesuffix("ProfileBackup")
        meta = profile.with_name(f"{prefix}Meta")
        if profile.is_file() and meta.is_file():
            candidates.append(ProfileSourcePair(profile=profile, meta=meta, prefix=prefix))
    if not candidates:
        raise ProfileSourceNotFoundError("No se encontro una pareja completa del perfil de FH6.")
    return max(candidates, key=lambda pair: max(pair.profile.stat().st_mtime_ns, pair.meta.stat().st_mtime_ns))


def load_profile_backup_manifest(destination: Path) -> ProfileBackupManifest | None:
    path = destination / "manifest.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        return ProfileBackupManifest.from_dict(payload)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_manifest(source: Path, stored_name: str, sha256: str) -> ProfileFileManifest:
    stat = source.stat()
    return ProfileFileManifest(
        stored_name=stored_name,
        source_name=source.name,
        size=stat.st_size,
        sha256=sha256,
        modified_at=datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
    )


def create_profile_backup(
    local_app_data: Path,
    destination: Path,
    *,
    is_game_running: Callable[[], bool],
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ProfileBackupManifest:
    if is_game_running():
        raise ProfileGameRunningError("Cierra Forza Horizon 6 antes de guardar la copia.")

    source = find_profile_source(local_app_data)
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.parent / f".{destination.name}.staging-{uuid.uuid4().hex}"
    retiring = destination.parent / f".{destination.name}.retiring"
    if retiring.exists() and not destination.exists():
        retiring.replace(destination)
    elif retiring.exists():
        shutil.rmtree(retiring)
    staging.mkdir()

    try:
        records: list[ProfileFileManifest] = []
        for source_file, stored_name in (
            (source.profile, "ProfileBackup.bin"),
            (source.meta, "Meta.bin"),
        ):
            before = _sha256(source_file)
            copied = staging / stored_name
            shutil.copy2(source_file, copied)
            if _sha256(copied) != before or _sha256(source_file) != before:
                raise ProfileValidationError("El perfil cambio durante la copia.")
            records.append(_file_manifest(source_file, stored_name, before))

        if is_game_running():
            raise ProfileGameRunningError("FH6 se abrio durante la copia; no se sustituyo el backup anterior.")

        manifest = ProfileBackupManifest(
            schema_version=1,
            created_at=now().astimezone(UTC).isoformat(),
            package_family=FH6_PACKAGE_FAMILY,
            verified=True,
            files=tuple(records),
        )
        (staging / "manifest.json").write_text(
            json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        if destination.exists():
            destination.replace(retiring)
        try:
            staging.replace(destination)
        except Exception:
            if not destination.exists() and retiring.exists():
                retiring.replace(destination)
            raise
        if retiring.exists():
            shutil.rmtree(retiring)
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)
