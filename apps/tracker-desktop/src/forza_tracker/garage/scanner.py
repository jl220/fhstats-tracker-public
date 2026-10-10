from __future__ import annotations

import json
import math
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np


NormalizedRegion = tuple[float, float, float, float]


class GarageScreenLostError(RuntimeError):
    pass


class GarageCaptureBackend(Protocol):
    def assert_safe_screen(self) -> None: ...

    def capture_window(self) -> np.ndarray: ...

    def press_right(self) -> None: ...

    def sleep(self, seconds: float) -> None: ...


@dataclass(frozen=True)
class GarageScreenProfile:
    regions: dict[str, NormalizedRegion]

    @classmethod
    def load(cls, path: Path) -> GarageScreenProfile:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_regions = payload.get("regions")
        if not isinstance(raw_regions, dict):
            raise ValueError("El perfil del garaje no contiene regiones.")
        regions: dict[str, NormalizedRegion] = {}
        for name, values in raw_regions.items():
            if not isinstance(values, list) or len(values) != 4:
                raise ValueError(f"Region de garaje no valida: {name}")
            region = tuple(float(value) for value in values)
            if any(value < 0.0 or value > 1.0 for value in region):
                raise ValueError(f"Region de garaje fuera de rango: {name}")
            regions[str(name)] = region  # type: ignore[assignment]
        return cls(regions=regions)

    def crop(self, image: np.ndarray, name: str) -> np.ndarray:
        try:
            x, y, width, height = self.regions[name]
        except KeyError as error:
            raise ValueError(f"Falta la region '{name}' en el perfil del garaje.") from error
        image_height, image_width = image.shape[:2]
        x1 = max(0, min(image_width - 1, int(round(x * image_width))))
        y1 = max(0, min(image_height - 1, int(round(y * image_height))))
        x2 = max(x1 + 1, min(image_width, int(round((x + width) * image_width))))
        y2 = max(y1 + 1, min(image_height, int(round((y + height) * image_height))))
        return image[y1:y2, x1:x2].copy()

    def navigation_descriptor(self, image: np.ndarray) -> np.ndarray:
        parts: list[np.ndarray] = []
        for name in ("selected_card", "navigation_strip"):
            crop = self.crop(image, name)
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            compact = cv2.resize(gray, (64, 32), interpolation=cv2.INTER_AREA)
            parts.append(compact.reshape(-1))
        return np.concatenate(parts).astype(np.uint8)


@dataclass(frozen=True)
class LinearCaptureResult:
    capture_count: int
    right_presses: int
    elapsed_seconds: float


def looks_like_garage_marker(marker: np.ndarray) -> bool:
    if marker.size == 0:
        return False
    gray = cv2.cvtColor(marker, cv2.COLOR_BGR2GRAY)
    dark_ratio = float(np.count_nonzero(gray < 45)) / float(gray.size)
    bright_ratio = float(np.count_nonzero(gray > 180)) / float(gray.size)
    return dark_ratio >= 0.40 and bright_ratio >= 0.01


def navigation_distance(first: np.ndarray, second: np.ndarray) -> float:
    if first.shape != second.shape:
        raise ValueError("Las firmas de navegacion deben tener el mismo tamano.")
    return float(cv2.absdiff(first, second).mean())


@dataclass(frozen=True)
class _CaptureItem:
    index: int
    car: np.ndarray
    name: np.ndarray
    class_pi: np.ndarray


def run_linear_capture(
    backend: GarageCaptureBackend,
    output_dir: Path,
    profile: GarageScreenProfile,
    *,
    unchanged_limit: int = 3,
    loop_detection_min_cars: int = 10,
    change_timeout: float = 0.80,
    stability_interval: float = 0.08,
    max_cars: int | None = None,
) -> LinearCaptureResult:
    if unchanged_limit < 1:
        raise ValueError("unchanged_limit debe ser al menos 1.")
    if loop_detection_min_cars < 2:
        raise ValueError("loop_detection_min_cars debe ser al menos 2.")
    if stability_interval <= 0 or change_timeout <= 0:
        raise ValueError("Los tiempos del escaner deben ser positivos.")

    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    write_queue: queue.Queue[_CaptureItem | None] = queue.Queue(maxsize=32)
    writer_errors: list[BaseException] = []
    writer = threading.Thread(
        target=_capture_writer,
        args=(write_queue, raw_dir, writer_errors),
        name="fhstats-garage-writer",
        daemon=True,
    )
    writer.start()

    started = time.monotonic()
    capture_count = 0
    right_presses = 0

    def enqueue(frame: np.ndarray) -> None:
        nonlocal capture_count
        capture_count += 1
        write_queue.put(
            _CaptureItem(
                index=capture_count,
                car=profile.crop(frame, "car_image"),
                name=profile.crop(frame, "name"),
                class_pi=profile.crop(frame, "class_pi"),
            )
        )

    try:
        backend.assert_safe_screen()
        current_frame = backend.capture_window()
        current_descriptor = profile.navigation_descriptor(current_frame)
        first_descriptor = current_descriptor.copy()
        enqueue(current_frame)
        unchanged_attempts = 0
        poll_limit = max(1, math.ceil(change_timeout / stability_interval))

        while max_cars is None or capture_count < max_cars:
            backend.assert_safe_screen()
            backend.press_right()
            right_presses += 1
            changed_frame: np.ndarray | None = None
            changed_descriptor: np.ndarray | None = None

            for _ in range(poll_limit):
                backend.sleep(stability_interval)
                candidate = backend.capture_window()
                candidate_descriptor = profile.navigation_descriptor(candidate)
                if navigation_distance(current_descriptor, candidate_descriptor) < 2.0:
                    continue
                backend.sleep(stability_interval)
                stable = backend.capture_window()
                stable_descriptor = profile.navigation_descriptor(stable)
                if (
                    navigation_distance(candidate_descriptor, stable_descriptor) <= 0.5
                    and navigation_distance(current_descriptor, stable_descriptor) >= 2.0
                ):
                    changed_frame = stable
                    changed_descriptor = stable_descriptor
                    break

            if changed_frame is None or changed_descriptor is None:
                unchanged_attempts += 1
                if unchanged_attempts >= unchanged_limit:
                    break
                continue

            if (
                capture_count >= loop_detection_min_cars
                and navigation_distance(first_descriptor, changed_descriptor) <= 0.5
            ):
                break

            unchanged_attempts = 0
            current_frame = changed_frame
            current_descriptor = changed_descriptor
            enqueue(current_frame)
    finally:
        write_queue.put(None)
        writer.join()

    if writer_errors:
        raise RuntimeError("No se pudieron guardar las capturas del garaje.") from writer_errors[0]
    return LinearCaptureResult(
        capture_count=capture_count,
        right_presses=right_presses,
        elapsed_seconds=time.monotonic() - started,
    )


def _capture_writer(
    write_queue: queue.Queue[_CaptureItem | None],
    raw_dir: Path,
    errors: list[BaseException],
) -> None:
    records: list[dict[str, object]] = []
    try:
        while True:
            item = write_queue.get()
            if item is None:
                break
            stem = f"{item.index:06d}"
            paths = {
                "car": raw_dir / f"{stem}-car.png",
                "name": raw_dir / f"{stem}-name.png",
                "class_pi": raw_dir / f"{stem}-pi.png",
            }
            for key, image in (("car", item.car), ("name", item.name), ("class_pi", item.class_pi)):
                if not cv2.imwrite(str(paths[key]), image):
                    raise OSError(f"No se pudo escribir {paths[key]}")
            records.append(
                {
                    "capture_index": item.index,
                    "car_image": paths["car"].name,
                    "name_image": paths["name"].name,
                    "class_pi_image": paths["class_pi"].name,
                }
            )
        (raw_dir / "captures.json").write_text(
            json.dumps(records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except BaseException as error:
        errors.append(error)
