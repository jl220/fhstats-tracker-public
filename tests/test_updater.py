from __future__ import annotations

import hashlib

import pytest
from types import SimpleNamespace

from forza_tracker.updater import UpdateInfo, download_update, is_newer_version


@pytest.mark.parametrize("candidate", ["0.4.3", "0.4.4"])
def test_client_rejects_stale_server_update_flag(monkeypatch, candidate):
    from forza_tracker.updater import check_for_update
    response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
        "update_available": True, "version": candidate,
        "download_url": "https://fhstats.com/update.zip", "sha256": "a" * 64,
    })
    monkeypatch.setattr("forza_tracker.updater.requests.get", lambda *a, **k: response)
    assert check_for_update("https://fhstats.com", current_version="0.4.4") is None


class _FakeResponse:
    def __init__(self, content: bytes) -> None:
        self._content = content
        self.headers = {"Content-Length": str(len(content))}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int):
        yield self._content[:chunk_size]
        yield self._content[chunk_size:]


def test_version_comparison_handles_prereleases() -> None:
    assert is_newer_version("0.2.2-alpha", "0.2.1")
    assert is_newer_version("0.2.1", "0.2.1-alpha")
    assert not is_newer_version("0.1.5-alpha", "0.2.1")


def test_download_update_verifies_sha256(monkeypatch, tmp_path) -> None:
    content = b"fhstats update package"
    update = UpdateInfo(
        version="0.2.2",
        download_url="https://fhstats.com/downloads/update.zip",
        sha256=hashlib.sha256(content).hexdigest(),
        size=len(content),
        release_notes="",
        mandatory=False,
    )
    monkeypatch.setattr("forza_tracker.updater.tempfile.gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr("forza_tracker.updater.requests.get", lambda *_args, **_kwargs: _FakeResponse(content))

    path = download_update(update)

    assert path.read_bytes() == content


def test_download_update_rejects_invalid_hash(monkeypatch, tmp_path) -> None:
    update = UpdateInfo(
        version="0.2.2",
        download_url="https://fhstats.com/downloads/update.zip",
        sha256="0" * 64,
        size=3,
        release_notes="",
        mandatory=False,
    )
    monkeypatch.setattr("forza_tracker.updater.tempfile.gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr("forza_tracker.updater.requests.get", lambda *_args, **_kwargs: _FakeResponse(b"bad"))

    with pytest.raises(ValueError, match="SHA-256"):
        download_update(update)
