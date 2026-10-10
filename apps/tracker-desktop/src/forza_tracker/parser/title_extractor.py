from __future__ import annotations

import cv2
import numpy as np

from forza_tracker.parser.ocr import TesseractOcr


class TitleExtractor:
    def __init__(self, ocr: TesseractOcr) -> None:
        self._ocr = ocr

    def extract(self, screenshot: np.ndarray) -> str | None:
        height, width = screenshot.shape[:2]
        gray = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
        top = gray[: int(height * 0.35), :]
        mask = cv2.inRange(top, 0, 70)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        candidates: list[tuple[int, int, int, int]] = []
        for contour in contours:
            x, y, box_width, box_height = cv2.boundingRect(contour)
            if box_width < width * 0.12:
                continue
            if not (height * 0.025 < box_height < height * 0.10):
                continue
            if not (height * 0.05 < y < height * 0.24):
                continue
            if x > width * 0.65:
                continue
            candidates.append((x, y, box_width, box_height))

        if not candidates:
            return None

        x, y, box_width, box_height = max(candidates, key=lambda box: box[2] * box[3])
        padding = 8
        crop = screenshot[
            max(0, y - padding) : min(height, y + box_height + padding),
            max(0, x - padding) : min(width, x + box_width + padding),
        ]
        return _clean_title(self._ocr.read_line(crop))


def _clean_title(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = text.replace("|", " ").replace("[", " ").replace("]", " ")
    cleaned = " ".join(cleaned.split())
    cleaned = cleaned.lstrip(" ,.;:!-")
    return cleaned or None
