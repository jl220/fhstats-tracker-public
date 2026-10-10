from __future__ import annotations

import ctypes
import json
import os
import time
from ctypes import wintypes
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
import pytesseract

from forza_tracker.car_catalog.fh6 import load_fh6_car_catalog
from forza_tracker.garage.capture import WindowFrame, crop_window_frame, frame_difference
from forza_tracker.garage.models import ScanOptions
from forza_tracker.garage.processor import load_aliases, process_draft
from forza_tracker.garage.scanner import GarageScreenLostError, GarageScreenProfile, LinearCaptureResult, looks_like_garage_marker, run_linear_capture
from forza_tracker.garage.storage import GarageStore
from forza_tracker.localization import tr
from forza_tracker.parser.ocr import _available_languages, _resolve_tesseract_cmd
from forza_tracker.paths import resolve_data_path, resolve_resource_path

ProgressCallback = Callable[[str], None]
GARAGE_PROFILE_PATH = Path("assets/garage/profile.json")
FORZA_WINDOW_TITLES = ("forza horizon 5", "forza horizon 6")
FORZA_TRACKER_TITLE = "forza horizon tracker"



@dataclass(frozen=True)
class ScreenFrame:
    image: np.ndarray
    monitor_index: int
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True)
class GarageRawScanResult:
    draft_path: str
    capture_count: int
    right_presses: int
    elapsed_seconds: float


class _ForzaGarageBackend:
    def __init__(
        self,
        settings: Any,
        forza_rect: tuple[int, int, int, int],
        monitor_index: int,
        profile: GarageScreenProfile,
        stop_event: Any,
    ) -> None:
        self.settings = settings
        self.forza_rect = forza_rect
        self.monitor_index = monitor_index
        self.profile = profile
        self.stop_event = stop_event
        self._cached_frame: np.ndarray | None = None

    def assert_safe_screen(self) -> None:
        _raise_if_stopped(self.stop_event)
        if not _foreground_window_is_forza():
            raise GarageScreenLostError("Forza ha perdido el foco; el escaneo se ha detenido sin sustituir tu garaje.")
        frame = self._grab_window()
        marker = self.profile.crop(frame, "screen_marker")
        if not looks_like_garage_marker(marker):
            raise GarageScreenLostError("Ya no se detecta la pantalla Mis coches; no se ha sustituido tu garaje.")
        self._cached_frame = frame

    def capture_window(self) -> np.ndarray:
        _raise_if_stopped(self.stop_event)
        if self._cached_frame is not None:
            frame = self._cached_frame
            self._cached_frame = None
            return frame
        return self._grab_window()

    def press_right(self) -> None:
        _raise_if_stopped(self.stop_event)
        self._cached_frame = None
        _press_key("right")

    def sleep(self, seconds: float) -> None:
        _sleep_or_stop(seconds, self.stop_event)

    def _grab_window(self) -> np.ndarray:
        frames = _capture_frames(self.settings, preferred_monitor_index=self.monitor_index)
        frame = next((item for item in frames if item.monitor_index == self.monitor_index), None)
        if frame is None:
            raise GarageScreenLostError("No se pudo capturar el monitor donde esta Forza.")
        return crop_window_frame(
            WindowFrame(frame.image, frame.left, frame.top, frame.monitor_index),
            self.forza_rect,
        )


def capture_garage_probe(settings: Any, output_dir: Path) -> dict[str, object]:
    if os.name != "nt":
        raise RuntimeError("La prueba de garaje solo funciona en Windows.")
    forza_rect = _activate_forza_window()
    if forza_rect is None:
        raise RuntimeError("Abre Forza Horizon y entra en Mis coches.")
    monitor_index = _monitor_index_for_window(settings, forza_rect)
    if monitor_index is None:
        raise RuntimeError("No se pudo localizar la ventana de Forza en los monitores capturados.")

    frames = _capture_frames(settings, preferred_monitor_index=monitor_index)
    frame = next((item for item in frames if item.monitor_index == monitor_index), None)
    if frame is None:
        raise RuntimeError("No se pudo capturar el monitor donde esta Forza.")
    before = crop_window_frame(
        WindowFrame(frame.image, frame.left, frame.top, frame.monitor_index),
        forza_rect,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output_dir / "before.png"), before):
        raise RuntimeError("No se pudo guardar la captura inicial del garaje.")

    _press_key("right")
    _sleep_or_stop(0.35, None)

    frames = _capture_frames(settings, preferred_monitor_index=monitor_index)
    frame = next((item for item in frames if item.monitor_index == monitor_index), None)
    if frame is None:
        raise RuntimeError("No se pudo capturar Forza despues del movimiento.")
    after = crop_window_frame(
        WindowFrame(frame.image, frame.left, frame.top, frame.monitor_index),
        forza_rect,
    )
    difference = cv2.absdiff(before, after)
    if not cv2.imwrite(str(output_dir / "after.png"), after):
        raise RuntimeError("No se pudo guardar la captura final del garaje.")
    if not cv2.imwrite(str(output_dir / "difference.png"), difference):
        raise RuntimeError("No se pudo guardar la diferencia de capturas del garaje.")

    payload: dict[str, object] = {
        "window_rect": list(forza_rect),
        "monitor_index": monitor_index,
        "width": int(before.shape[1]),
        "height": int(before.shape[0]),
        "mean_difference": frame_difference(before, after),
    }
    try:
        profile_regions = GarageScreenProfile.load(resolve_resource_path(GARAGE_PROFILE_PATH)).regions
        payload["normalized_regions"] = {name: list(region) for name, region in profile_regions.items()}
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    (output_dir / "probe.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def run_garage_scan_script(
    settings: Any,
    options: ScanOptions,
    progress: ProgressCallback,
    stop_event: Any,
    *,
    max_cars: int | None = None,
) -> GarageRawScanResult:
    if os.name != "nt":
        raise RuntimeError("El escaneo de garaje solo funciona en Windows.")
    forza_rect = _activate_forza_window()
    if forza_rect is None:
        raise RuntimeError("Abre Forza Horizon y entra en Mis coches antes de iniciar el escaneo.")
    monitor_index = _monitor_index_for_window(settings, forza_rect)
    if monitor_index is None:
        raise RuntimeError("No se pudo localizar la ventana de Forza en los monitores capturados.")

    profile = GarageScreenProfile.load(resolve_resource_path(GARAGE_PROFILE_PATH))
    store = GarageStore(resolve_data_path(Path("garage")))
    draft = store.create_draft(options)
    progress(tr("garage.scan_capturing"))
    backend = _ForzaGarageBackend(settings, forza_rect, monitor_index, profile, stop_event)
    capture_result: LinearCaptureResult = run_linear_capture(
        backend,
        store.draft_path,
        profile,
        max_cars=max_cars,
    )
    raw_snapshot = replace(
        draft,
        expected_count=capture_result.capture_count,
        observed_count=capture_result.capture_count,
        warning="raw_capture_complete",
    )
    progress(tr("garage.scan_captured", count=capture_result.capture_count))
    languages = _configure_tesseract(getattr(getattr(settings, "ocr", None), "tesseract_cmd", None))
    completed = process_draft(
        raw_snapshot,
        store.draft_path,
        load_fh6_car_catalog(),
        load_aliases(store.root / "aliases.json"),
        lambda image, psm: _ocr_text(image, languages, psm=psm),
    )
    store.write_draft(completed)
    review_count = sum(car.quantity for car in completed.cars if car.confidence != "confirmed")
    progress(
        tr(
            "garage.scan_recognized",
            confirmed=completed.observed_count - review_count,
            review=review_count,
        )
    )
    return GarageRawScanResult(
        draft_path=str(store.draft_path),
        capture_count=capture_result.capture_count,
        right_presses=capture_result.right_presses,
        elapsed_seconds=capture_result.elapsed_seconds,
    )


def _capture_frames(settings: Any, preferred_monitor_index: int | None = None) -> list[ScreenFrame]:
    backend = str(getattr(settings.capture, "backend", "mss")).lower()
    if backend != "mss":
        raise RuntimeError(f"Backend de captura no soportado para el escaneo de garaje: {backend}")

    import mss

    monitor_index = 0 if bool(getattr(settings.capture, "auto_detect_monitors", True)) else int(settings.capture.monitor_index)
    with mss.mss() as capture:
        if monitor_index < 0 or monitor_index >= len(capture.monitors):
            raise RuntimeError(f"Monitor {monitor_index} no disponible para captura.")
        if preferred_monitor_index is not None and 0 < preferred_monitor_index < len(capture.monitors):
            monitor_indexes = (preferred_monitor_index,)
        else:
            monitor_indexes = range(1, len(capture.monitors)) if monitor_index == 0 else (monitor_index,)
        frames: list[ScreenFrame] = []
        for index in monitor_indexes:
            monitor = capture.monitors[index]
            raw = np.array(capture.grab(monitor))
            image = cv2.cvtColor(raw, cv2.COLOR_BGRA2BGR)
            frames.append(
                ScreenFrame(
                    image=image,
                    monitor_index=index,
                    left=int(monitor["left"]),
                    top=int(monitor["top"]),
                    width=int(monitor["width"]),
                    height=int(monitor["height"]),
                )
            )
        return frames


def _monitor_index_for_window(settings: Any, rect: tuple[int, int, int, int]) -> int | None:
    window_left, window_top, window_right, window_bottom = rect
    best_index: int | None = None
    best_area = 0
    try:
        frames = _capture_frames(settings)
    except Exception:
        return None
    for frame in frames:
        frame_right = frame.left + frame.width
        frame_bottom = frame.top + frame.height
        overlap_left = max(window_left, frame.left)
        overlap_top = max(window_top, frame.top)
        overlap_right = min(window_right, frame_right)
        overlap_bottom = min(window_bottom, frame_bottom)
        area = max(0, overlap_right - overlap_left) * max(0, overlap_bottom - overlap_top)
        if area > best_area:
            best_area = area
            best_index = frame.monitor_index
    return best_index


def _configure_tesseract(tesseract_cmd: Path | None) -> str:
    resolved_cmd = _resolve_tesseract_cmd(tesseract_cmd)
    if resolved_cmd is None:
        raise RuntimeError("No se encontro Tesseract OCR. Revisa la ruta en config/tracker.yaml.")
    pytesseract.pytesseract.tesseract_cmd = str(resolved_cmd)
    tessdata_dir = resolved_cmd.parent / "tessdata"
    if tessdata_dir.exists():
        os.environ["TESSDATA_PREFIX"] = str(tessdata_dir)
    return _available_languages(resolved_cmd)


def _ocr_text(image: np.ndarray, languages: str, *, psm: int) -> str:
    prepared = _prepare_ocr_image(image)
    try:
        return pytesseract.image_to_string(prepared, lang=languages, config=f"--psm {psm}")
    except pytesseract.TesseractError:
        return ""


def _prepare_ocr_image(image: np.ndarray) -> np.ndarray:
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    _, threshold = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return threshold


def _activate_forza_window() -> tuple[int, int, int, int] | None:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enum_callback = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows.argtypes = [enum_callback, ctypes.c_void_p]
    user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
    user32.IsWindowVisible.restype = ctypes.c_bool
    user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]

    matches: list[int] = []

    def collect(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.lower()
        if FORZA_TRACKER_TITLE not in title and any(value in title for value in FORZA_WINDOW_TITLES):
            matches.append(hwnd)
            return False
        return True

    user32.EnumWindows(enum_callback(collect), None)
    if not matches:
        return None
    hwnd = ctypes.c_void_p(matches[0])
    user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def _foreground_window_is_forza() -> bool:
    if os.name != "nt":
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return False
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    title = buffer.value.lower()
    return FORZA_TRACKER_TITLE not in title and any(value in title for value in FORZA_WINDOW_TITLES)


def _press_key(key: str) -> None:
    virtual_keys = {
        "esc": 0x1B,
        "escape": 0x1B,
        "enter": 0x0D,
        "left": 0x25,
        "right": 0x27,
        "up": 0x26,
        "down": 0x28,
        "backspace": 0x08,
        "delete": 0x2E,
        "space": 0x20,
        "d": 0x44,
        "x": 0x58,
    }
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    vk = virtual_keys[key]
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.05)
    user32.keybd_event(vk, 0, 0x0002, 0)


def _sleep_or_stop(seconds: float, stop_event: Any) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _raise_if_stopped(stop_event)
        time.sleep(min(0.05, deadline - time.monotonic()))


def _raise_if_stopped(stop_event: Any) -> None:
    if stop_event is not None and stop_event.is_set():
        raise RuntimeError("Escaneo de garaje detenido por el usuario.")
