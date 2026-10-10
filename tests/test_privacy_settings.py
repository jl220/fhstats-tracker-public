from __future__ import annotations

from pathlib import Path

from forza_tracker.config.settings import load_settings


def test_upload_and_diagnostics_preferences_are_independent(tmp_path: Path) -> None:
    config = tmp_path / "tracker.yaml"
    config.write_text(
        """
api:
  enabled: false
  diagnostics_enabled: true
  base_url: https://fhstats.com
""".strip(),
        encoding="utf-8",
    )

    settings = load_settings(config)

    assert settings.api.enabled is False
    assert settings.api.diagnostics_enabled is True


def test_older_configs_keep_their_existing_upload_preference_for_diagnostics(tmp_path: Path) -> None:
    config = tmp_path / "tracker.yaml"
    config.write_text("api:\n  enabled: true\n", encoding="utf-8")

    settings = load_settings(config)

    assert settings.api.enabled is True
    assert settings.api.diagnostics_enabled is True
