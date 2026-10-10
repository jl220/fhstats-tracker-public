from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import requests

from forza_tracker.paths import application_root
from forza_tracker.localization import tr
from forza_tracker.version import APP_VERSION


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    download_url: str
    sha256: str
    size: int | None
    release_notes: str
    mandatory: bool


def check_for_update(
    base_url: str,
    timeout_seconds: int = 10,
    current_version: str = APP_VERSION,
) -> UpdateInfo | None:
    response = requests.get(
        f"{base_url.rstrip('/')}/api/tracker/update",
        params={"current_version": current_version},
        headers={"User-Agent": f"FHStatsTracker/{current_version}"},
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not payload.get("update_available"):
        return None
    update = _parse_update_info(payload)
    return update if is_newer_version(update.version, current_version) else None


def download_update(
    update: UpdateInfo,
    progress: Callable[[int, int | None], None] | None = None,
) -> Path:
    destination = Path(tempfile.gettempdir()) / f"fhstats-tracker-{update.version}-windows-portable.zip"
    temporary = destination.with_suffix(".download")
    digest = hashlib.sha256()
    downloaded = 0

    with requests.get(
        update.download_url,
        stream=True,
        timeout=(10, 120),
        headers={"User-Agent": f"FHStatsTracker/{APP_VERSION}"},
    ) as response:
        response.raise_for_status()
        total = update.size or _as_int(response.headers.get("Content-Length"))
        with temporary.open("wb") as file:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                file.write(chunk)
                digest.update(chunk)
                downloaded += len(chunk)
                if progress:
                    progress(downloaded, total)

    actual_hash = digest.hexdigest().lower()
    if actual_hash != update.sha256.lower():
        temporary.unlink(missing_ok=True)
        raise ValueError(tr("update.hash_mismatch"))
    os.replace(temporary, destination)
    return destination


def launch_installer(zip_path: Path) -> None:
    if not getattr(sys, "frozen", False):
        raise RuntimeError(tr("update.packaged_only"))

    install_dir = application_root()
    executable = Path(sys.executable).resolve()
    script_path = Path(tempfile.gettempdir()) / f"fhstats-updater-{os.getpid()}.ps1"
    script_path.write_text(_installer_script(), encoding="utf-8-sig")
    creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            "-ZipPath",
            str(zip_path.resolve()),
            "-InstallDir",
            str(install_dir.resolve()),
            "-Executable",
            str(executable),
            "-ProcessId",
            str(os.getpid()),
        ],
        close_fds=True,
        creationflags=creationflags,
    )


def is_newer_version(candidate: str, current: str) -> bool:
    return _version_key(candidate) > _version_key(current)


def _parse_update_info(payload: dict[str, Any]) -> UpdateInfo:
    version = str(payload.get("version") or "").strip()
    download_url = str(payload.get("download_url") or "").strip()
    sha256 = str(payload.get("sha256") or "").strip().lower()
    if not version or not download_url or not re.fullmatch(r"[a-f0-9]{64}", sha256):
        raise ValueError(tr("update.invalid_manifest"))
    if not download_url.startswith("https://"):
        raise ValueError(tr("update.https_required"))
    return UpdateInfo(
        version=version,
        download_url=download_url,
        sha256=sha256,
        size=_as_int(payload.get("size")),
        release_notes=str(payload.get("release_notes") or "").strip(),
        mandatory=bool(payload.get("mandatory", False)),
    )


def _version_key(value: str) -> tuple[int, int, int, int]:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", value)
    if not match:
        return 0, 0, 0, -1
    stability = 0 if any(label in value.lower() for label in ("alpha", "beta", "rc")) else 1
    return int(match.group(1)), int(match.group(2)), int(match.group(3)), stability


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _installer_script() -> str:
    script = r"""param(
    [Parameter(Mandatory=$true)][string]$ZipPath,
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [Parameter(Mandatory=$true)][string]$Executable,
    [Parameter(Mandatory=$true)][int]$ProcessId
)
$ErrorActionPreference = "Stop"
try {
    Wait-Process -Id $ProcessId -ErrorAction SilentlyContinue
    $extractDir = Join-Path $env:TEMP ("fhstats-update-" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $extractDir | Out-Null
    Expand-Archive -LiteralPath $ZipPath -DestinationPath $extractDir -Force
    $newExecutable = Get-ChildItem -LiteralPath $extractDir -Recurse -Filter "FHStatsTracker.exe" -File |
        Select-Object -First 1
    if (-not $newExecutable) {
        throw "FHStatsTracker.exe was not found in the update."
    }
    $sourceDir = $newExecutable.Directory.FullName
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    Copy-Item -Path (Join-Path $sourceDir "*") -Destination $InstallDir -Recurse -Force
    Remove-Item -LiteralPath $extractDir -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $ZipPath -Force -ErrorAction SilentlyContinue
    Start-Process -FilePath $Executable -WorkingDirectory $InstallDir
} catch {
    $message = $_.Exception.Message
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        "__FHSTATS_UPDATE_ERROR__",
        "FHStats Tracker",
        "OK",
        "Error"
    ) | Out-Null
}
Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
"""
    localized_error = tr("update.install_failed", message="$message").replace('"', '`"')
    return script.replace("__FHSTATS_UPDATE_ERROR__", localized_error)
