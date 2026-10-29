from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from forza_tracker.localization import configure_language
from forza_tracker.ui import app as tracker_app


class _Value:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: object) -> None:
        self.value = str(value)


def _dashboard(language: str = "en") -> SimpleNamespace:
    return SimpleNamespace(
        _language_setting=_Value(language),
        _language_feedback=_Value(),
        _settings=SimpleNamespace(app=SimpleNamespace(language="en")),
        _restart_for_language=lambda: None,
    )


def test_language_selector_saves_and_restarts_when_confirmed(
    monkeypatch,
    tmp_path: Path,
) -> None:
    configure_language("en")
    dashboard = _dashboard("es")
    saved: list[tuple[str, Path, Path]] = []
    restarted: list[bool] = []

    def fake_save(language: str, *, local_path: Path, default_path: Path) -> Path:
        saved.append((language, local_path, default_path))
        return tmp_path / "tracker.local.yaml"

    monkeypatch.setattr(tracker_app, "save_language", fake_save)
    monkeypatch.setattr(tracker_app.messagebox, "askyesno", lambda *_args, **_kwargs: True)
    dashboard._restart_for_language = lambda: restarted.append(True)

    tracker_app.TrackerDashboard._save_language_setting(dashboard)

    assert saved[0][0] == "es"
    assert dashboard._config_path == tmp_path / "tracker.local.yaml"
    assert restarted == [True]
