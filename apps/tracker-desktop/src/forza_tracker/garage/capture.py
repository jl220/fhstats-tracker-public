from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class WindowFrame:
    image: np.ndarray
    left: int
    top: int
    monitor_index: int


def crop_window_frame(frame: WindowFrame, window_rect: tuple[int, int, int, int]) -> np.ndarray:
    left, top, right, bottom = window_rect
    x1 = max(0, left - frame.left)
    y1 = max(0, top - frame.top)
    x2 = min(frame.image.shape[1], right - frame.left)
    y2 = min(frame.image.shape[0], bottom - frame.top)
    if x2 <= x1 or y2 <= y1:
        raise ValueError("La ventana de Forza no intersecta el monitor capturado.")
    return frame.image[y1:y2, x1:x2].copy()


def frame_difference(before: np.ndarray, after: np.ndarray) -> float:
    if before.shape != after.shape:
        raise ValueError("Las capturas deben tener el mismo tamano.")
    gray_before = cv2.cvtColor(before, cv2.COLOR_BGR2GRAY)
    gray_after = cv2.cvtColor(after, cv2.COLOR_BGR2GRAY)
    return float(cv2.absdiff(gray_before, gray_after).mean())
