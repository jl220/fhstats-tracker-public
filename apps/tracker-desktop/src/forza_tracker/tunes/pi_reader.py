from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re

import cv2
import numpy as np


PI_BOX = (0.488, 0.600, 0.548, 0.655)
SELECTED_PI_DIGITS_BOX = (0.5078, 0.6111, 0.5332, 0.6375)
CAROUSEL_PI_BAND = (0.15, 0.72, 0.87, 0.80)


@dataclass(frozen=True)
class VisiblePiBlock:
    performance_indexes: list[int]
    selected_pi: int


def detect_pi_boxes(
    frame: np.ndarray,
    expected_count: int | None,
) -> list[tuple[int, int, int, int]]:
    if frame.ndim != 3 or frame.shape[2] < 3:
        raise ValueError("La captura de Forza no tiene tres canales de color.")
    if expected_count is not None and expected_count <= 0:
        raise ValueError("El número esperado de distintivos debe ser positivo.")
    height, width = frame.shape[:2]
    left_ratio, top_ratio, right_ratio, bottom_ratio = CAROUSEL_PI_BAND
    left = int(left_ratio * width)
    top = int(top_ratio * height)
    right = int(right_ratio * width)
    bottom = int(bottom_ratio * height)
    band = frame[top:bottom, left:right, :3]
    dark = cv2.inRange(band, np.array((0, 0, 0)), np.array((65, 65, 65)))
    contours, _hierarchy = cv2.findContours(
        dark,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    minimum_width = max(8, int(width * 0.012))
    maximum_width = max(minimum_width, int(width * 0.035))
    minimum_height = max(8, int(height * 0.012))
    maximum_height = max(minimum_height, int(height * 0.040))
    boxes: list[tuple[int, int, int, int]] = []
    for contour in contours:
        x, y, box_width, box_height = cv2.boundingRect(contour)
        fill_ratio = cv2.contourArea(contour) / (box_width * box_height)
        if not (
            minimum_width <= box_width <= maximum_width
            and minimum_height <= box_height <= maximum_height
            and fill_ratio >= 0.80
        ):
            continue
        boxes.append((left + x, top + y, box_width, box_height))
    boxes.sort(key=lambda box: box[0])
    if expected_count is not None and len(boxes) != expected_count:
        raise RuntimeError(
            f"Se esperaban {expected_count} distintivos PI visibles y se detectaron {len(boxes)}."
        )
    return boxes


def crop_new_pi(frame: np.ndarray) -> np.ndarray:
    if frame.ndim != 3 or frame.shape[2] < 3:
        raise ValueError("La captura de Forza no tiene tres canales de color.")
    height, width = frame.shape[:2]
    left, top, right, bottom = PI_BOX
    crop = frame[
        int(top * height) : int(bottom * height),
        int(left * width) : int(right * width),
        :3,
    ].copy()
    if crop.size == 0:
        raise ValueError("El recorte normalizado del PI está vacío.")
    return crop


def crop_selected_pi_digits(frame: np.ndarray) -> np.ndarray:
    if frame.ndim != 3 or frame.shape[2] < 3:
        raise ValueError("La captura de Forza no tiene tres canales de color.")
    height, width = frame.shape[:2]
    left, top, right, bottom = SELECTED_PI_DIGITS_BOX
    crop = frame[
        int(top * height) : int(bottom * height),
        int(left * width) : int(right * width),
        :3,
    ].copy()
    if crop.size == 0:
        raise ValueError("El recorte de dígitos del PI seleccionado está vacío.")
    return crop


def parse_pi(text: str | None) -> int:
    digits = re.sub(r"\D", "", text or "")
    if len(digits) != 3:
        raise ValueError("El OCR del PI no devolvió exactamente tres dígitos.")
    value = int(digits)
    if not 100 <= value <= 999:
        raise ValueError("El OCR del PI quedó fuera de 100..999.")
    return value


class StablePiReader:
    def __init__(
        self,
        capture: Callable[[], np.ndarray],
        read_line: Callable[[np.ndarray], str | None],
        diagnostics_dir: Path,
        attempts: int = 3,
    ) -> None:
        if attempts < 2:
            raise ValueError("Se necesitan al menos dos intentos para validar el PI.")
        self._capture = capture
        self._read_line = read_line
        self._diagnostics_dir = diagnostics_dir
        self._attempts = attempts

    def read(self, tune_key: str, identity: Callable[[], str]) -> int:
        previous: int | None = None
        last_crop: np.ndarray | None = None
        for _attempt in range(self._attempts):
            self._require_identity(tune_key, identity())
            crop = crop_selected_pi_digits(self._capture())
            self._require_identity(tune_key, identity())
            last_crop = crop
            try:
                current = parse_pi(self._read_line(crop))
            except ValueError:
                previous = None
                continue
            self._require_identity(tune_key, identity())
            if current == previous:
                return current
            previous = current
        if last_crop is not None:
            self._write_diagnostic(tune_key, last_crop)
        raise RuntimeError("No se obtuvieron dos lecturas consecutivas iguales del PI.")

    def read_block(self, expected_codes: Sequence[str]) -> list[int]:
        if len(expected_codes) < 2:
            raise ValueError("El bloque de PI debe contener al menos dos tuneos.")
        previous: list[int] | None = None
        last_frame: np.ndarray | None = None
        for _attempt in range(self._attempts):
            frame = self._capture()
            last_frame = frame
            try:
                boxes = detect_pi_boxes(frame, len(expected_codes))
                current = [
                    self._read_box_pi(frame, box)
                    for box in boxes
                ]
            except (RuntimeError, ValueError):
                previous = None
                continue
            if current == previous:
                return current
            previous = current
        if last_frame is not None:
            self._write_block_diagnostic(expected_codes, last_frame)
        raise RuntimeError("No se obtuvieron dos lecturas consecutivas iguales del bloque de PI.")

    def read_detected_block(
        self,
        *,
        minimum_count: int = 1,
        maximum_count: int = 11,
    ) -> VisiblePiBlock:
        if minimum_count < 1 or maximum_count < minimum_count:
            raise ValueError("El rango de tarjetas PI visibles no es válido.")
        previous: VisiblePiBlock | None = None
        last_frame: np.ndarray | None = None
        for _attempt in range(self._attempts):
            frame = self._capture()
            last_frame = frame
            try:
                boxes = detect_pi_boxes(frame, expected_count=None)
                if not minimum_count <= len(boxes) <= maximum_count:
                    raise ValueError("Cantidad visible de PI fuera del rango esperado.")
                values = [
                    self._read_box_pi(frame, box)
                    for box in boxes
                ]
                selected_crop = crop_selected_pi_digits(frame)
                current = VisiblePiBlock(
                    performance_indexes=values,
                    selected_pi=parse_pi(self._read_line(selected_crop)),
                )
            except (RuntimeError, ValueError):
                previous = None
                continue
            if current == previous:
                return current
            previous = current
        if last_frame is not None:
            self._write_block_diagnostic(["detected"], last_frame)
        raise RuntimeError("No se obtuvieron dos lecturas iguales del bloque de PI detectado.")

    def _read_box_pi(
        self,
        frame: np.ndarray,
        box: tuple[int, int, int, int],
    ) -> int:
        last_error: ValueError | None = None
        for crop in _box_crops(frame, box):
            try:
                return parse_pi(self._read_line(crop))
            except ValueError as error:
                last_error = error
        raise last_error or ValueError("No se pudo recortar el distintivo PI.")

    @staticmethod
    def _require_identity(expected: str, actual: str) -> None:
        if actual != expected:
            raise RuntimeError(
                "La identidad del tuneo cambió durante la lectura del PI; se descarta el intento."
            )

    def _write_diagnostic(self, tune_key: str, crop: np.ndarray) -> None:
        self._diagnostics_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        safe_key = re.sub(r"[^0-9A-Za-z_-]", "_", tune_key)[:64]
        cv2.imwrite(str(self._diagnostics_dir / f"pi-{timestamp}-{safe_key}.png"), crop)

    def _write_block_diagnostic(
        self,
        expected_codes: Sequence[str],
        frame: np.ndarray,
    ) -> None:
        self._diagnostics_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        first = re.sub(r"[^0-9A-Za-z_-]", "_", expected_codes[0])[:32]
        cv2.imwrite(
            str(self._diagnostics_dir / f"pi-block-{timestamp}-{first}.png"),
            frame,
        )


def _enlarged_box(
    frame: np.ndarray,
    box: tuple[int, int, int, int],
) -> np.ndarray:
    x, y, width, height = box
    return frame[y : y + height, x : x + width, :3].copy()


def _box_crops(
    frame: np.ndarray,
    box: tuple[int, int, int, int],
) -> list[np.ndarray]:
    x, y, width, height = box
    exact = _enlarged_box(frame, box)
    top = max(0, y - 1)
    left = max(0, x - 1)
    bottom = min(frame.shape[0], y + height + 1)
    right = min(frame.shape[1], x + width + 1)
    padded = frame[top:bottom, left:right, :3].copy()
    vertical = frame[
        max(0, y - 2) : min(frame.shape[0], y + height + 2),
        x : x + width,
        :3,
    ].copy()
    padded_two = frame[
        max(0, y - 2) : min(frame.shape[0], y + height + 2),
        max(0, x - 2) : min(frame.shape[1], x + width + 2),
        :3,
    ].copy()
    return [exact, padded, vertical, padded_two]
