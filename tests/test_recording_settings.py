from pathlib import Path

from forza_tracker.config.settings import load_settings


BASE = """
app:
  player_name: Test
capture: {{}}
detector: {{}}
history: {{}}
api: {{}}
ocr: {{}}
recording:
  enabled: {enabled}
{mode}
"""


def test_new_installation_defaults_to_recording_off(tmp_path: Path) -> None:
    path = tmp_path / "tracker.yaml"
    path.write_text(BASE.format(enabled="false", mode=""), encoding="utf-8")

    settings = load_settings(path)

    assert settings.recording.enabled is False
    assert settings.recording.mode == "off"


def test_existing_enabled_recording_stays_in_separate_races_mode(tmp_path: Path) -> None:
    path = tmp_path / "tracker.yaml"
    path.write_text(BASE.format(enabled="true", mode=""), encoding="utf-8")

    settings = load_settings(path)

    assert settings.recording.enabled is True
    assert settings.recording.mode == "races"


def test_session_mode_is_persisted_without_enabling_automatic_recording(tmp_path: Path) -> None:
    path = tmp_path / "tracker.yaml"
    path.write_text(BASE.format(enabled="false", mode='  mode: "session"'), encoding="utf-8")

    settings = load_settings(path)

    assert settings.recording.enabled is False
    assert settings.recording.mode == "session"


def test_capture_storage_limit_defaults_to_one_gb(tmp_path: Path) -> None:
    path = tmp_path / "tracker.yaml"
    path.write_text(BASE.format(enabled="false", mode=""), encoding="utf-8")

    settings = load_settings(path)

    assert settings.capture.max_storage_gb == 1.0
