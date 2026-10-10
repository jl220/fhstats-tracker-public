from __future__ import annotations

from pathlib import Path

import numpy as np

from forza_tracker.garage.scanner import (
    GarageScreenProfile,
    looks_like_garage_marker,
    navigation_distance,
    run_linear_capture,
)


PROFILE = GarageScreenProfile(
    regions={
        "selected_card": (0.0, 0.0, 0.5, 1.0),
        "navigation_strip": (0.5, 0.0, 0.5, 1.0),
        "car_image": (0.0, 0.0, 0.5, 1.0),
        "name": (0.0, 0.0, 0.5, 0.5),
        "class_pi": (0.0, 0.5, 0.5, 0.5),
    }
)


def _frame(selected: int, navigation: int) -> np.ndarray:
    image = np.zeros((40, 80, 3), dtype=np.uint8)
    image[:, :40] = selected
    image[:, 40:] = navigation
    return image


class FakeBackend:
    def __init__(self, frames: list[np.ndarray]) -> None:
        self.frames = frames
        self.position = 0
        self.keys: list[str] = []
        self.safe_checks = 0

    def assert_safe_screen(self) -> None:
        self.safe_checks += 1

    def capture_window(self) -> np.ndarray:
        return self.frames[self.position].copy()

    def press_right(self) -> None:
        self.keys.append("right")
        self.position = min(self.position + 1, len(self.frames) - 1)

    def sleep(self, seconds: float) -> None:
        del seconds


class WrappingBackend(FakeBackend):
    def press_right(self) -> None:
        self.keys.append("right")
        self.position = (self.position + 1) % len(self.frames)


def test_linear_scan_stops_after_three_unchanged_right_attempts(tmp_path: Path) -> None:
    backend = FakeBackend(
        [
            _frame(10, 10),
            _frame(20, 20),
            _frame(30, 30),
            _frame(30, 30),
            _frame(30, 30),
            _frame(30, 30),
        ]
    )

    result = run_linear_capture(backend, tmp_path, PROFILE, unchanged_limit=3)

    assert result.capture_count == 3
    assert backend.keys == ["right"] * 5
    assert backend.safe_checks == 6
    assert len(list((tmp_path / "raw").glob("*-car.png"))) == 3


def test_navigation_strip_distinguishes_identical_consecutive_cars(tmp_path: Path) -> None:
    backend = FakeBackend(
        [
            _frame(50, 10),
            _frame(50, 20),
            _frame(60, 30),
            _frame(60, 30),
            _frame(60, 30),
            _frame(60, 30),
        ]
    )

    result = run_linear_capture(backend, tmp_path, PROFILE, unchanged_limit=3)

    assert result.capture_count == 3
    assert backend.keys == ["right"] * 5


def test_linear_scan_stops_before_saving_first_car_twice_after_wrap(tmp_path: Path) -> None:
    backend = WrappingBackend(
        [
            _frame(10, 10),
            _frame(20, 20),
            _frame(30, 30),
        ]
    )

    result = run_linear_capture(
        backend,
        tmp_path,
        PROFILE,
        loop_detection_min_cars=3,
        max_cars=5,
    )

    assert result.capture_count == 3
    assert backend.keys == ["right"] * 3
    assert len(list((tmp_path / "raw").glob("*-car.png"))) == 3


def test_garage_marker_accepts_calibrated_dark_title_with_white_text() -> None:
    marker = np.full((10, 10, 3), 100, dtype=np.uint8)
    marker.reshape(-1, 3)[:44] = 20
    marker.reshape(-1, 3)[44:46] = 240

    assert looks_like_garage_marker(marker)
    assert not looks_like_garage_marker(np.full((10, 10, 3), 100, dtype=np.uint8))


def test_navigation_distance_separates_animation_noise_from_car_change() -> None:
    baseline = np.full(128, 100, dtype=np.uint8)
    animation_noise = baseline.copy()
    animation_noise[:4] += 1
    car_change = np.full(128, 135, dtype=np.uint8)

    assert navigation_distance(baseline, animation_noise) < 0.5
    assert navigation_distance(baseline, car_change) > 2.0
