from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from forza_tracker.config.settings import load_settings
from forza_tracker.detector.result_detector import ResultDetector
from forza_tracker.models.screen_type import ScreenType


ROOT = Path(__file__).resolve().parents[1]
TEST_IMAGES = Path(__file__).resolve().parent


def test_detector_classifies_known_screen_captures() -> None:
    detector = ResultDetector(
        templates_dir=ROOT / "templates",
        threshold=0.86,
        cooldown_seconds=0,
        resize_scales=[1.0, 0.9, 1.1],
        thresholds_by_type={
            "starting_grid": 0.92,
            "race_result": 0.90,
            "championship_result": 0.95,
        },
    )

    expected = {
        "starting_grid_20260614_230438.png": ScreenType.STARTING_GRID,
        "race_result_20260614_230408.png": ScreenType.RACE_RESULT,
        "championship_result_20260615_002222.png": ScreenType.CHAMPIONSHIP_RESULT,
    }

    for filename, screen_type in expected.items():
        image = cv2.imread(str(TEST_IMAGES / filename))

        result = detector.detect(image)

        assert result.detected
        assert result.screen_type == screen_type


def test_detector_selects_the_monitor_with_a_known_screen() -> None:
    detector = ResultDetector(
        templates_dir=ROOT / "templates",
        threshold=0.86,
        cooldown_seconds=0,
        resize_scales=[1.0, 0.9, 1.1],
        thresholds_by_type={
            "starting_grid": 0.92,
            "race_result": 0.78,
            "championship_result": 0.95,
        },
    )
    race_result = cv2.imread(str(TEST_IMAGES / "race_result_20260614_230408.png"))
    unrelated_monitor = np.full_like(race_result, 32)

    monitor_index, result = detector.detect_many([unrelated_monitor, race_result])

    assert monitor_index == 1
    assert result.detected
    assert result.screen_type == ScreenType.RACE_RESULT


def test_settings_cap_transition_thresholds_for_short_lived_screens(tmp_path: Path) -> None:
    config_path = tmp_path / "tracker.yaml"
    config_path.write_text(
        """
detector:
  thresholds_by_type:
    event_registration: 0.86
    race_result: 0.90
    championship_result: 0.95
""",
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    assert settings.detector.thresholds_by_type["event_registration"] == 0.77
    assert settings.detector.thresholds_by_type["race_result"] == 0.78
    assert settings.detector.thresholds_by_type["championship_result"] == 0.92
