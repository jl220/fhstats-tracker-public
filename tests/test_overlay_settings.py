from pathlib import Path

from forza_tracker.config.settings import OverlaySettings, load_settings


def _config(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "tracker.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_legacy_config_enables_overlay_by_default(tmp_path: Path) -> None:
    settings = load_settings(_config(tmp_path, "{}\n"))
    assert settings.overlay == OverlaySettings()


def test_overlay_reads_explicit_values(tmp_path: Path) -> None:
    settings = load_settings(
        _config(
            tmp_path,
            "overlay:\n  enabled: false\n  opacity: 0.7\n  scale: 1.2\n  duration_seconds: 22\n",
        )
    )
    assert settings.overlay == OverlaySettings(False, 0.7, 1.2, 22)


def test_overlay_clamps_unsafe_values(tmp_path: Path) -> None:
    settings = load_settings(
        _config(
            tmp_path,
            "overlay:\n  enabled: true\n  opacity: 2\n  scale: 0.1\n  duration_seconds: 90\n",
        )
    )
    assert settings.overlay == OverlaySettings(True, 0.95, 0.75, 35)
