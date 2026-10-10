from __future__ import annotations

import inspect
from pathlib import Path

import yaml

from forza_tracker.config.settings import load_settings
from forza_tracker.config.user_config import load_editable_config, save_language
from forza_tracker.ui.app import language_choice_required
from forza_tracker.ui.app import TrackerDashboard


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_missing_language_remains_unresolved(tmp_path: Path) -> None:
    config = _write(tmp_path / "tracker.yaml", "app:\n  player_name: Tester\n")

    assert load_settings(config).app.language is None


def test_valid_language_is_normalized(tmp_path: Path) -> None:
    config = _write(tmp_path / "tracker.yaml", "app:\n  language: ' ES '\n")

    assert load_settings(config).app.language == "es"


def test_invalid_language_becomes_unresolved(tmp_path: Path) -> None:
    config = _write(tmp_path / "tracker.yaml", "app:\n  language: de\n")

    assert load_settings(config).app.language is None


def test_language_choice_is_required_only_for_unresolved_settings(tmp_path: Path) -> None:
    missing = _write(tmp_path / "missing.yaml", "app: {}\n")
    selected = _write(tmp_path / "selected.yaml", "app:\n  language: en\n")

    assert language_choice_required(load_settings(missing)) is True
    assert language_choice_required(load_settings(selected)) is False


def test_initial_language_dialog_keeps_continue_button_visible() -> None:
    source = inspect.getsource(TrackerDashboard._choose_initial_language)

    assert 'dialog.geometry("430x380")' in source


def test_cancelled_initial_language_choice_bypasses_full_dashboard_shutdown() -> None:
    source = inspect.getsource(TrackerDashboard.__init__)

    cancellation_branch = source.split("if selected_language is None:", maxsplit=1)[1].split(
        "self._config_path = _resolve_config_path()",
        maxsplit=1,
    )[0]
    assert "tk.Tk.destroy(self)" in cancellation_branch
    assert "self.destroy()" not in cancellation_branch


def test_editable_config_prefers_existing_local_file(tmp_path: Path) -> None:
    default = _write(tmp_path / "default.yaml", "app:\n  player_name: Default\n")
    local = _write(tmp_path / "local.yaml", "app:\n  player_name: Local\n")

    assert load_editable_config(local_path=local, default_path=default)["app"]["player_name"] == "Local"


def test_save_language_preserves_other_settings_and_writes_utf8(tmp_path: Path) -> None:
    default = _write(
        tmp_path / "default.yaml",
        "app:\n  player_name: Tester\nrecording:\n  mode: races\n",
    )
    local = tmp_path / "config" / "tracker.local.yaml"

    written = save_language("es", local_path=local, default_path=default)
    payload = yaml.safe_load(written.read_text(encoding="utf-8"))

    assert written == local
    assert payload["app"] == {"player_name": "Tester", "language": "es"}
    assert payload["recording"]["mode"] == "races"


def test_save_language_rejects_unsupported_codes(tmp_path: Path) -> None:
    default = _write(tmp_path / "default.yaml", "app: {}\n")

    try:
        save_language("de", local_path=tmp_path / "local.yaml", default_path=default)
    except ValueError as error:
        assert str(error) == "Unsupported language: de"
    else:
        raise AssertionError("unsupported languages must not be persisted")
