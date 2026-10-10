from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from forza_tracker.paths import user_data_root


DEFAULT_CATALOG_BASE_URL = "https://fhstats.com/api/catalog"
CATALOG_CACHE_DIR = user_data_root() / "data" / "catalog-cache"


@dataclass(frozen=True)
class SyncAsset:
    group: str
    filename: str


@dataclass(frozen=True)
class CatalogSyncResult:
    source: str
    files_updated: list[str]
    cache_dir: Path
    updated_at: str
    error: str | None = None


HttpGet = Callable[..., Any]


class FhstatsCatalogClient:
    def __init__(
        self,
        *,
        base_url: str = DEFAULT_CATALOG_BASE_URL,
        cache_dir: Path = CATALOG_CACHE_DIR,
        timeout_seconds: float = 8.0,
        get: HttpGet = requests.get,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._cache_dir = cache_dir
        self._timeout_seconds = timeout_seconds
        self._get = get

    def sync_assets(self, assets: list[SyncAsset]) -> CatalogSyncResult:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        updated_at = datetime.now(timezone.utc).isoformat()
        files_updated: list[str] = []
        try:
            for asset in assets:
                payload = self._download_text(asset.filename)
                _write_text_atomic(self._cache_dir / asset.filename, payload)
                files_updated.append(asset.filename)
        except Exception as error:
            if self._has_cached_assets(assets):
                return CatalogSyncResult(
                    source="cache",
                    files_updated=[],
                    cache_dir=self._cache_dir,
                    updated_at=updated_at,
                    error=str(error),
                )
            raise RuntimeError(str(error)) from error

        self._write_manifest("remote", files_updated, updated_at)
        return CatalogSyncResult(
            source="remote",
            files_updated=files_updated,
            cache_dir=self._cache_dir,
            updated_at=updated_at,
        )

    def _download_text(self, filename: str) -> str:
        response = self._get(
            f"{self._base_url}/{filename}",
            timeout=self._timeout_seconds,
            headers={"Accept": "application/json,text/plain,*/*"},
        )
        response.raise_for_status()
        return str(response.text)

    def _has_cached_assets(self, assets: list[SyncAsset]) -> bool:
        return all((self._cache_dir / asset.filename).exists() for asset in assets)

    def _write_manifest(self, source: str, files: list[str], updated_at: str) -> None:
        payload = {
            "source": source,
            "updated_at": updated_at,
            "files": files,
        }
        _write_text_atomic(
            self._cache_dir / "manifest.json",
            json.dumps(payload, ensure_ascii=False, indent=2),
        )


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=path.stem, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(text)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
