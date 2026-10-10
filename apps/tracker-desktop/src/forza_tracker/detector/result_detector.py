from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from forza_tracker.models.screen_type import ScreenType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DetectionResult:
    detected: bool
    confidence: float = 0.0
    template_name: str | None = None
    screen_type: ScreenType = ScreenType.UNKNOWN


class ResultDetector:
    def __init__(
        self,
        templates_dir: Path,
        threshold: float,
        cooldown_seconds: int,
        resize_scales: list[float],
        thresholds_by_type: dict[str, float] | None = None,
        cooldown_seconds_by_type: dict[str, float] | None = None,
        log_best_match_interval_seconds: int = 10,
    ) -> None:
        self._templates_dir = templates_dir
        self._threshold = threshold
        self._thresholds_by_type = thresholds_by_type or {}
        self._cooldown_seconds = cooldown_seconds
        self._cooldown_seconds_by_type = cooldown_seconds_by_type or {}
        self._resize_scales = resize_scales
        self._log_best_match_interval_seconds = log_best_match_interval_seconds
        self._last_best_match_log_at = 0.0
        self._last_detection_at_by_type: dict[ScreenType, float] = {}
        self._templates = self._load_templates(templates_dir)

    def detect(self, screenshot: np.ndarray) -> DetectionResult:
        _, result = self.detect_many([screenshot])
        return result

    def detect_many(self, screenshots: list[np.ndarray]) -> tuple[int, DetectionResult]:
        if not self._templates:
            logger.warning("No templates found in %s", self._templates_dir)
            return 0, DetectionResult(detected=False)

        fast_result = self._detect_event_registration_fast(screenshots)
        if fast_result is not None:
            screenshot_index, result = fast_result
            self._record_detection(result)
            return screenshot_index, result

        best_index = 0
        best = DetectionResult(detected=False)

        for screenshot_index, screenshot in enumerate(screenshots):
            gray_screen = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
            resolution_scale = gray_screen.shape[0] / 1440.0
            for template_name, screen_type, template in self._templates:
                if self._is_in_cooldown(screen_type):
                    continue

                threshold = self._threshold_for(screen_type)
                for scale_multiplier in self._resize_scales:
                    scale = scale_multiplier * resolution_scale
                    candidate = _resize_template(template, scale)
                    if candidate.shape[0] > gray_screen.shape[0] or candidate.shape[1] > gray_screen.shape[1]:
                        continue

                    match = cv2.matchTemplate(gray_screen, candidate, cv2.TM_CCOEFF_NORMED)
                    _, confidence, _, _ = cv2.minMaxLoc(match)
                    detected = confidence >= threshold
                    if (
                        detected and not best.detected
                        or detected == best.detected and confidence > best.confidence
                    ):
                        best_index = screenshot_index
                        best = DetectionResult(
                            detected=detected,
                            confidence=float(confidence),
                            template_name=template_name,
                            screen_type=screen_type,
                        )

        if best.detected:
            self._record_detection(best)
        elif best.template_name and time.monotonic() - self._last_best_match_log_at >= self._log_best_match_interval_seconds:
            self._last_best_match_log_at = time.monotonic()
            logger.info(
                "No screen detected. Best match type=%s template=%s confidence=%.3f threshold=%.3f",
                best.screen_type,
                best.template_name,
                best.confidence,
                self._threshold_for(best.screen_type),
            )

        return best_index, best

    def _detect_event_registration_fast(
        self,
        screenshots: list[np.ndarray],
    ) -> tuple[int, DetectionResult] | None:
        screen_type = ScreenType.EVENT_REGISTRATION
        if self._is_in_cooldown(screen_type):
            return None
        templates = [
            (name, template)
            for name, candidate_type, template in self._templates
            if candidate_type == screen_type
        ]
        threshold = self._threshold_for(screen_type)
        for screenshot_index, screenshot in enumerate(screenshots):
            gray_screen = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
            resolution_scale = gray_screen.shape[0] / 1440.0
            for template_name, template in templates:
                for scale_adjustment in (1.0, 0.98, 1.02):
                    candidate = _resize_template(template, resolution_scale * scale_adjustment)
                    if candidate.shape[0] > gray_screen.shape[0] or candidate.shape[1] > gray_screen.shape[1]:
                        continue
                    match = cv2.matchTemplate(gray_screen, candidate, cv2.TM_CCOEFF_NORMED)
                    _, confidence, _, _ = cv2.minMaxLoc(match)
                    if confidence >= threshold:
                        return screenshot_index, DetectionResult(
                            detected=True,
                            confidence=float(confidence),
                            template_name=template_name,
                            screen_type=screen_type,
                        )
        return None

    def _record_detection(self, result: DetectionResult) -> None:
        self._last_detection_at_by_type[result.screen_type] = time.monotonic()
        logger.info(
            "Screen detected type=%s template=%s confidence=%.3f threshold=%.3f",
            result.screen_type,
            result.template_name,
            result.confidence,
            self._threshold_for(result.screen_type),
        )

    def reset_cooldown(self, screen_type: ScreenType) -> None:
        self._last_detection_at_by_type.pop(screen_type, None)

    def _threshold_for(self, screen_type: ScreenType) -> float:
        return self._thresholds_by_type.get(screen_type.value, self._threshold)

    def _is_in_cooldown(self, screen_type: ScreenType) -> bool:
        last_detection_at = self._last_detection_at_by_type.get(screen_type, 0.0)
        cooldown_seconds = self._cooldown_seconds_by_type.get(screen_type.value, self._cooldown_seconds)
        return time.monotonic() - last_detection_at < cooldown_seconds

    @staticmethod
    def _load_templates(templates_dir: Path) -> list[tuple[str, ScreenType, np.ndarray]]:
        templates_dir.mkdir(parents=True, exist_ok=True)
        templates: list[tuple[str, ScreenType, np.ndarray]] = []

        for path in sorted(templates_dir.glob("*")):
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".bmp"}:
                continue

            image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if image is None:
                logger.warning("Could not load template: %s", path)
                continue

            screen_type = ScreenType.from_template_name(path.name)
            if screen_type == ScreenType.UNKNOWN:
                logger.warning(
                    "Template %s has no known prefix. Use event_registration_, matchmaking_search_, matchmaking_found_, starting_grid_, race_result_ or championship_result_",
                    path.name,
                )
                continue
            templates.append((path.name, screen_type, image))

        logger.info("Loaded %d screen templates from %s", len(templates), templates_dir)
        return templates


def _resize_template(template: np.ndarray, scale: float) -> np.ndarray:
    if scale == 1.0:
        return template
    width = max(1, int(template.shape[1] * scale))
    height = max(1, int(template.shape[0] * scale))
    return cv2.resize(template, (width, height), interpolation=cv2.INTER_AREA)
