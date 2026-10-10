from __future__ import annotations

import ctypes
from ctypes import wintypes
from collections.abc import Callable
import time
from typing import Protocol

import cv2
import numpy as np

from forza_tracker.garage.capture import WindowFrame, crop_window_frame

from .memory import find_process_id


KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
SW_RESTORE = 9
VK_F2 = 0x71
VK_MENU = 0x12
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
KEYS = {"escape": 0x1B}
PHOTO_STRIP_X = 0.25
PHOTO_STRIP_Y = 0.67
TUNE_IDENTITY_BOXES = (
    (0.18, 0.245, 0.40, 0.292),
    (0.15, 0.66, 0.87, 0.80),
)


def tune_selection_fingerprint(frame: np.ndarray) -> bytes:
    if frame.ndim != 3 or frame.shape[2] < 3:
        raise ValueError("La captura de Forza no tiene tres canales de color.")
    height, width = frame.shape[:2]
    identity = bytearray()
    for box_index, (left, top, right, bottom) in enumerate(TUNE_IDENTITY_BOXES):
        crop = frame[
            int(top * height) : int(bottom * height),
            int(left * width) : int(right * width),
            :3,
        ]
        if crop.size == 0:
            raise ValueError("El recorte de identidad del tuneo está vacío.")
        if box_index == 1:
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            lime = cv2.inRange(
                hsv,
                np.array((35, 150, 150)),
                np.array((95, 255, 255)),
            )
            selected_left = int(np.argmax(np.sum(lime > 0, axis=0)))
            crop = crop.copy()
            crop[
                :,
                max(0, selected_left - 35) : min(crop.shape[1], selected_left + 230),
            ] = 0
            identity.extend((selected_left // 8).to_bytes(2, "little"))
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        reduced = cv2.resize(gray, (192, 32), interpolation=cv2.INTER_AREA)
        identity.extend(np.packbits(reduced > 128).tobytes())
    return bytes(identity)


def same_tune_selection(
    left: bytes,
    right: bytes,
    *,
    maximum_changed_bits: int = 64,
) -> bool:
    if len(left) != len(right):
        return False
    changed_bits = sum((first ^ second).bit_count() for first, second in zip(left, right))
    return changed_bits <= maximum_changed_bits


class OsApi(Protocol):
    def find_process(self, name: str) -> int | None: ...
    def find_window(self, pid: int) -> int | None: ...
    def focus_window(self, hwnd: int) -> bool: ...
    def window_rect(self, hwnd: int) -> tuple[int, int, int, int]: ...
    def key_event(self, key: int, flags: int) -> None: ...
    def set_cursor_pos(self, x: int, y: int) -> None: ...
    def wheel(self, delta: int) -> None: ...
    def cancel_requested(self) -> bool: ...


class ForzaWindowController:
    def __init__(
        self,
        *,
        os_api: OsApi | None = None,
        grab: Callable[[tuple[int, int, int, int]], WindowFrame | None] | None = None,
        executable_name: str = "forzahorizon6.exe",
    ) -> None:
        self._os_api = os_api or WindowsOsApi()
        self._grab = grab or _grab_monitor
        self._executable_name = executable_name

    def ensure_ready(self) -> None:
        hwnd = self._ready_window()
        self._point_at_tune_strip(hwnd)

    def capture(self) -> np.ndarray:
        hwnd = self._ready_window()
        rect = self._os_api.window_rect(hwnd)
        desktop = self._grab(rect)
        if desktop is None:
            raise RuntimeError("No se pudo capturar el monitor que contiene Forza.")
        return crop_window_frame(desktop, rect)

    def press(self, key: str) -> None:
        if key not in KEYS:
            raise ValueError(f"Tecla no permitida: {key}")
        self._ready_window()
        virtual_key = KEYS[key]
        self._os_api.key_event(virtual_key, 0)
        self._os_api.key_event(virtual_key, KEYEVENTF_KEYUP)

    def scroll_tunes(self, step: int = -120) -> None:
        if step == 0 or abs(step) > 120:
            raise ValueError("El paso de rueda debe ser pequeño y estar entre -120 y 120.")
        hwnd = self._ready_window()
        self._point_at_tune_strip(hwnd)
        self._os_api.wheel(step)

    def selection_fingerprint(self) -> bytes:
        return tune_selection_fingerprint(self.capture())

    def selection_matches(self, expected: bytes) -> bool:
        return same_tune_selection(expected, self.selection_fingerprint())

    def wait_for_selection_change(self, previous: bytes, timeout: float) -> bytes:
        if timeout < 0:
            raise ValueError("La espera de selección no puede ser negativa.")
        deadline = time.monotonic() + timeout
        while time.monotonic() <= deadline:
            current = self.selection_fingerprint()
            if not same_tune_selection(previous, current):
                time.sleep(0.6)
                return self.selection_fingerprint()
            time.sleep(0.1)
        raise TimeoutError("La ficha seleccionada no cambió después del giro.")

    def cancel_requested(self) -> bool:
        return self._os_api.cancel_requested()

    def _ready_window(self) -> int:
        pid = self._os_api.find_process(self._executable_name)
        if pid is None:
            raise RuntimeError("Forza Horizon 6 no está abierto.")
        hwnd = self._os_api.find_window(pid)
        if hwnd is None:
            raise RuntimeError("No se encontró la ventana principal de Forza Horizon 6.")
        if not self._os_api.focus_window(hwnd):
            raise RuntimeError("No se pudo poner la ventana de Forza en primer plano.")
        return hwnd

    def _point_at_tune_strip(self, hwnd: int) -> None:
        left, top, right, bottom = self._os_api.window_rect(hwnd)
        photo_x = left + int((right - left) * PHOTO_STRIP_X)
        photo_y = top + int((bottom - top) * PHOTO_STRIP_Y)
        self._os_api.set_cursor_pos(photo_x, photo_y)


class WindowsOsApi:
    def __init__(self) -> None:
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)

    def find_process(self, name: str) -> int | None:
        return find_process_id(name)

    def find_window(self, pid: int) -> int | None:
        matches: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        def callback(hwnd: int, _lparam: int) -> bool:
            window_pid = wintypes.DWORD()
            self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
            if window_pid.value == pid and self._user32.IsWindowVisible(hwnd):
                matches.append(int(hwnd))
                return False
            return True

        self._user32.EnumWindows(callback, 0)
        return matches[0] if matches else None

    def focus_window(self, hwnd: int) -> bool:
        if self._user32.IsIconic(hwnd):
            self._user32.ShowWindow(hwnd, SW_RESTORE)
        self._user32.BringWindowToTop(hwnd)
        self._user32.SetForegroundWindow(hwnd)
        if int(self._user32.GetForegroundWindow() or 0) == hwnd:
            return True
        self._user32.keybd_event(VK_MENU, 0, 0, 0)
        self._user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        self._user32.BringWindowToTop(hwnd)
        self._user32.SetForegroundWindow(hwnd)
        return int(self._user32.GetForegroundWindow() or 0) == hwnd

    def window_rect(self, hwnd: int) -> tuple[int, int, int, int]:
        rect = wintypes.RECT()
        if not self._user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)

    def key_event(self, key: int, flags: int) -> None:
        self._user32.keybd_event(key, 0, flags, 0)

    def set_cursor_pos(self, x: int, y: int) -> None:
        if not self._user32.SetCursorPos(x, y):
            raise ctypes.WinError(ctypes.get_last_error())
        virtual_left = int(self._user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
        virtual_top = int(self._user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
        virtual_width = max(2, int(self._user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)))
        virtual_height = max(2, int(self._user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)))
        normalized_x = round((x - virtual_left) * 65535 / (virtual_width - 1))
        normalized_y = round((y - virtual_top) * 65535 / (virtual_height - 1))
        self._user32.mouse_event(
            MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
            normalized_x,
            normalized_y,
            0,
            0,
        )

    def wheel(self, delta: int) -> None:
        self._user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, delta & 0xFFFFFFFF, 0)

    def cancel_requested(self) -> bool:
        return bool(self._user32.GetAsyncKeyState(VK_F2) & 0x8000)


def _grab_monitor(window_rect: tuple[int, int, int, int]) -> WindowFrame | None:
    import mss

    left, top, right, bottom = window_rect
    with mss.mss() as capture:
        candidates = []
        for index, monitor in enumerate(capture.monitors[1:], start=1):
            monitor_right = monitor["left"] + monitor["width"]
            monitor_bottom = monitor["top"] + monitor["height"]
            overlap_width = max(0, min(right, monitor_right) - max(left, monitor["left"]))
            overlap_height = max(0, min(bottom, monitor_bottom) - max(top, monitor["top"]))
            candidates.append((overlap_width * overlap_height, index, monitor))
        area, monitor_index, monitor = max(candidates, default=(0, 0, None))
        if area <= 0 or monitor is None:
            return None
        raw = np.array(capture.grab(monitor))
        image = cv2.cvtColor(raw, cv2.COLOR_BGRA2BGR)
        return WindowFrame(
            image=image,
            left=int(monitor["left"]),
            top=int(monitor["top"]),
            monitor_index=monitor_index,
        )
