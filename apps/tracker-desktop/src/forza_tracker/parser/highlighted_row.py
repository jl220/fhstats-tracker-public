from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class HighlightedRow:
    x: int
    y: int
    width: int
    height: int
    row_index: int
    confidence: float


class HighlightedRowLocator:
    # These ratios are calibrated from 2560x1440 Forza table captures. Keeping
    # them relative makes the parser survive resolution changes better than raw pixels.
    _FIRST_ROW_Y_RATIO = 0.251
    _ROW_HEIGHT_RATIO = 0.050

    def locate(self, screenshot: np.ndarray) -> HighlightedRow | None:
        height, width = screenshot.shape[:2]
        hsv = cv2.cvtColor(screenshot, cv2.COLOR_BGR2HSV)

        # Forza uses a bright yellow-green outline for the local player's row.
        mask = cv2.inRange(hsv, np.array([35, 80, 120]), np.array([90, 255, 255]))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        candidates: list[tuple[float, int, int, int, int]] = []
        for contour in contours:
            x, y, box_width, box_height = cv2.boundingRect(contour)
            if box_width < width * 0.25:
                continue
            if box_width > width * 0.85:
                continue
            if box_height < height * 0.015 or box_height > height * 0.10:
                continue

            inner = screenshot[y + 5 : y + box_height - 5, x + 5 : x + box_width - 5]
            if inner.size == 0:
                continue

            # Header bars are also green, but their inside is bright. The selected
            # player row has a dark interior, so lower mean brightness wins.
            mean_brightness = float(inner.mean())
            candidates.append((mean_brightness, x, y, box_width, box_height))

        if not candidates:
            return None

        mean_brightness, x, y, box_width, box_height = min(candidates, key=lambda item: item[0])
        first_row_y = height * self._FIRST_ROW_Y_RATIO
        row_height = height * self._ROW_HEIGHT_RATIO
        row_index = max(1, round((y - first_row_y) / row_height) + 1)

        # Brightness is not a probability, but this gives us a simple quality signal:
        # 1.0 means very dark selected row, 0.0 means too bright to trust.
        confidence = max(0.0, min(1.0, 1.0 - mean_brightness / 255.0))
        return HighlightedRow(
            x=x,
            y=y,
            width=box_width,
            height=box_height,
            row_index=row_index,
            confidence=confidence,
        )