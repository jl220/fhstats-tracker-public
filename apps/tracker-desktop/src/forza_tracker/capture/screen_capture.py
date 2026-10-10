from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CapturedFrame:
    image: np.ndarray
    monitor_index: int = 1


class ScreenCapture(Protocol):
    def grab(self) -> CapturedFrame:
        ...

    def grab_all(self) -> list[CapturedFrame]:
        ...


class MssScreenCapture:
    def __init__(self, monitor_index: int = 0) -> None:
        import mss

        self._mss = mss.mss()
        if monitor_index < 0:
            raise ValueError("Monitor index must be 0 (automatic) or a positive monitor number.")
        if monitor_index >= len(self._mss.monitors):
            raise ValueError(
                f"Monitor index {monitor_index} is not available. "
                f"Available monitors: {len(self._mss.monitors) - 1}"
            )
        self._monitor_indexes = (
            list(range(1, len(self._mss.monitors)))
            if monitor_index == 0
            else [monitor_index]
        )
        logger.info(
            "Screen capture monitors=%s mode=%s",
            self._monitor_indexes,
            "automatic" if monitor_index == 0 else "manual",
        )

    def grab(self) -> CapturedFrame:
        return self.grab_all()[0]

    def grab_all(self) -> list[CapturedFrame]:
        frames: list[CapturedFrame] = []
        for monitor_index in self._monitor_indexes:
            raw = np.array(self._mss.grab(self._mss.monitors[monitor_index]))
            frames.append(
                CapturedFrame(
                    image=cv2.cvtColor(raw, cv2.COLOR_BGRA2BGR),
                    monitor_index=monitor_index,
                )
            )
        return frames


def create_capture_backend(backend: str, monitor_index: int) -> ScreenCapture:
    if backend.lower() == "mss":
        return MssScreenCapture(monitor_index=monitor_index)
    raise ValueError(f"Unsupported capture backend: {backend}")
