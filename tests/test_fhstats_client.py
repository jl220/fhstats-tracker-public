from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from forza_tracker.fhstats_client import FhstatsCatalogClient, SyncAsset
from forza_tracker.local_catalog import count_rivals_for_circuit, load_rivals, load_tierlist


@dataclass
class FakeResponse:
    text: str
    status_code: int = 200

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_sync_catalog_downloads_assets_and_writes_manifest(tmp_path: Path) -> None:
    calls: list[str] = []

    def get(url: str, **_kwargs: Any) -> FakeResponse:
        calls.append(url)
        return FakeResponse('{"events":[]}')

    client = FhstatsCatalogClient(
        base_url="https://example.test/catalog",
        cache_dir=tmp_path,
        get=get,
    )

    result = client.sync_assets([SyncAsset("rivals", "road-rivals.json")])

    assert result.source == "remote"
    assert result.files_updated == ["road-rivals.json"]
    assert calls == ["https://example.test/catalog/road-rivals.json"]
    assert (tmp_path / "road-rivals.json").read_text(encoding="utf-8") == '{"events":[]}'
    assert '"source": "remote"' in (tmp_path / "manifest.json").read_text(encoding="utf-8")


def test_sync_catalog_uses_existing_cache_when_remote_fails(tmp_path: Path) -> None:
    (tmp_path / "road-rivals.json").write_text('{"events":[{"name_es":"Cached"}]}', encoding="utf-8")

    def get(_url: str, **_kwargs: Any) -> FakeResponse:
        raise OSError("network down")

    client = FhstatsCatalogClient(
        base_url="https://example.test/catalog",
        cache_dir=tmp_path,
        get=get,
    )

    result = client.sync_assets([SyncAsset("rivals", "road-rivals.json")])

    assert result.source == "cache"
    assert result.files_updated == []
    assert result.error == "network down"
    assert (tmp_path / "road-rivals.json").read_text(encoding="utf-8") == '{"events":[{"name_es":"Cached"}]}'


def test_sync_catalog_raises_when_remote_fails_without_cache(tmp_path: Path) -> None:
    def get(_url: str, **_kwargs: Any) -> FakeResponse:
        raise OSError("network down")

    client = FhstatsCatalogClient(
        base_url="https://example.test/catalog",
        cache_dir=tmp_path,
        get=get,
    )

    with pytest.raises(RuntimeError, match="network down"):
        client.sync_assets([SyncAsset("rivals", "road-rivals.json")])


def test_local_catalog_prefers_synced_cache_root(tmp_path: Path) -> None:
    (tmp_path / "road-rivals.json").write_text(
        '{"events":[{"name_es":"Cached Circuit","classes":[{"label":"B 600","entries":[{"rank":1,"player":"Jolu","car":"Honda","time":"01:00.000"}]}]}]}',
        encoding="utf-8",
    )
    (tmp_path / "tierlist-cars.ts").write_text(
        '{ id: "cached", discipline: "road", className: "B 600", tier: "S", make: "Honda", model: "Beat", year: 1991, tuneCode: "123", tuner: "Jolu", buildType: "Grip", difficulty: "Easy", image: card("honda-beat"), labsUrl: "https://fhstats.com/metacars/honda-beat"\n  }',
        encoding="utf-8",
    )

    rivals = load_rivals("road", cache_root=tmp_path)
    tierlist = load_tierlist(cache_root=tmp_path)

    assert rivals[0]["event"] == "Cached Circuit"
    assert tierlist[0]["model"] == "Beat"


def test_count_rivals_for_circuit_normalizes_names_and_disciplines(tmp_path: Path) -> None:
    _write_rivals_catalog(tmp_path, "road-rivals.json", "Circuito de autopista", 2)
    _write_rivals_catalog(tmp_path, "dirt-rivals.json", "Circuito de Autopista", 1)

    assert count_rivals_for_circuit("circuito autopista", cache_root=tmp_path) == 3


def _write_rivals_catalog(root: Path, filename: str, event_name: str, entries: int) -> None:
    payload = {
        "events": [
            {
                "name_es": event_name,
                "classes": [
                    {
                        "label": "B 600",
                        "entries": [
                            {"rank": index + 1, "player": f"Driver {index}", "car": "Honda", "time": "01:00.000"}
                            for index in range(entries)
                        ],
                    }
                ],
            }
        ]
    }
    (root / filename).write_text(__import__("json").dumps(payload), encoding="utf-8")
