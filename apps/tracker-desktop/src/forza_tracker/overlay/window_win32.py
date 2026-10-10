from __future__ import annotations

import ctypes
from dataclasses import dataclass
from ctypes import wintypes


REFERENCE_WIDTH = 2048
REFERENCE_HEIGHT = 1152
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
GWL_EXSTYLE = -20
GA_ROOT = 2
LWA_ALPHA = 0x00000002
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
HWND_TOPMOST = -1
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SUPPORTED_FORZA_PROCESSES = {"forzahorizon5.exe", "forzahorizon6.exe"}


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2


@dataclass(frozen=True)
class OverlayGeometry:
    map_window: Rect
    map_slots: tuple[Rect, Rect, Rect]
    recommendation_window: Rect

    @property
    def recommendation_slots(self) -> tuple[Rect, Rect, Rect]:
        gap = max(8, round(self.recommendation_window.width * 0.02))
        usable_width = self.recommendation_window.width - gap * 2
        first_width = usable_width // 3
        second_width = usable_width // 3
        first = Rect(
            self.recommendation_window.left,
            self.recommendation_window.top,
            self.recommendation_window.left + first_width,
            self.recommendation_window.bottom,
        )
        second = Rect(
            first.right + gap,
            self.recommendation_window.top,
            first.right + gap + second_width,
            self.recommendation_window.bottom,
        )
        third = Rect(
            second.right + gap,
            self.recommendation_window.top,
            self.recommendation_window.right,
            self.recommendation_window.bottom,
        )
        return first, second, third


@dataclass(frozen=True)
class ForzaWindowState:
    hwnd: int
    client_rect: Rect
    dpi: int
    minimized: bool
    foreground: bool


def enable_per_monitor_dpi_awareness(user32=None) -> bool:
    try:
        api = user32 or ctypes.windll.user32
        setter = getattr(api, "SetProcessDpiAwarenessContext", None)
        if setter is not None:
            return bool(setter(ctypes.c_void_p(-4)))
        fallback = getattr(api, "SetProcessDPIAware", None)
        return bool(fallback()) if fallback is not None else False
    except (AttributeError, OSError):
        return False


def resolve_toplevel_hwnd(hwnd: int, user32=None) -> int:
    api = user32 or ctypes.windll.user32
    root = api.GetAncestor(hwnd, GA_ROOT)
    return int(root or hwnd)


def _is_supported_forza_process(value: str) -> bool:
    name = str(value).replace("/", "\\").rsplit("\\", 1)[-1].casefold()
    return name in SUPPORTED_FORZA_PROCESSES


def compute_overlay_geometry(client_rect: Rect, scale: float) -> OverlayGeometry | None:
    if client_rect.width < 1280 or client_rect.height < 720:
        return None
    viewport_scale = min(
        client_rect.width / REFERENCE_WIDTH,
        client_rect.height / REFERENCE_HEIGHT,
    )
    safe_width = REFERENCE_WIDTH * viewport_scale
    safe_height = REFERENCE_HEIGHT * viewport_scale
    safe_left = client_rect.left + (client_rect.width - safe_width) / 2
    safe_top = client_rect.top + (client_rect.height - safe_height) / 2
    ui_scale = max(0.75, min(1.25, float(scale)))

    map_right = safe_left + 348 * viewport_scale
    map_width = 260 * viewport_scale * ui_scale
    map_height = 104 * viewport_scale * ui_scale
    slots = []
    for center in (320, 464, 607):
        center_y = safe_top + center * viewport_scale
        slots.append(
            Rect(
                round(map_right - map_width),
                round(center_y - map_height / 2),
                round(map_right),
                round(center_y + map_height / 2),
            )
        )

    recommendation_left = safe_left + 356 * viewport_scale
    recommendation_top = safe_top + 684 * viewport_scale
    recommendation_width = 686 * viewport_scale * ui_scale
    recommendation_height = 102 * viewport_scale * ui_scale
    recommendation = Rect(
        round(recommendation_left),
        round(recommendation_top),
        round(recommendation_left + recommendation_width),
        round(recommendation_top + recommendation_height),
    )
    map_window = Rect(
        min(slot.left for slot in slots),
        min(slot.top for slot in slots),
        max(slot.right for slot in slots),
        max(slot.bottom for slot in slots),
    )
    geometry = OverlayGeometry(map_window, tuple(slots), recommendation)
    if any(
        rect.left < client_rect.left
        or rect.top < client_rect.top
        or rect.right > client_rect.right
        or rect.bottom > client_rect.bottom
        for rect in (*geometry.map_slots, geometry.recommendation_window)
    ):
        return None
    return geometry


class WindowsOverlayAdapter:
    def __init__(self) -> None:
        self._user32 = ctypes.windll.user32
        self._kernel32 = ctypes.windll.kernel32
        self._user32.GetForegroundWindow.restype = wintypes.HWND
        self._user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        self._user32.GetAncestor.restype = wintypes.HWND
        self._user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        self._user32.SetWindowPos.restype = wintypes.BOOL
        self._user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self._kernel32.OpenProcess.restype = wintypes.HANDLE
        self._kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL

    def find_forza_window(self) -> ForzaWindowState | None:
        matches: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        def callback(hwnd, _lparam):
            if not self._user32.IsWindowVisible(hwnd):
                return True
            length = self._user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            self._user32.GetWindowTextW(hwnd, buffer, len(buffer))
            title = buffer.value.casefold()
            process_image = self._process_image_name(hwnd)
            if (
                ("forza horizon" in title or "forzahorizon" in title)
                and process_image is not None
                and _is_supported_forza_process(process_image)
            ):
                matches.append(int(hwnd))
                return False
            return True

        self._user32.EnumWindows(callback, 0)
        if not matches:
            return None
        hwnd = matches[0]
        native_rect = wintypes.RECT()
        if not self._user32.GetClientRect(hwnd, ctypes.byref(native_rect)):
            return None
        origin = wintypes.POINT(0, 0)
        if not self._user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            return None
        dpi_getter = getattr(self._user32, "GetDpiForWindow", None)
        dpi = int(dpi_getter(hwnd)) if dpi_getter else 96
        rect = Rect(
            origin.x,
            origin.y,
            origin.x + native_rect.right - native_rect.left,
            origin.y + native_rect.bottom - native_rect.top,
        )
        return ForzaWindowState(
            hwnd=hwnd,
            client_rect=rect,
            dpi=dpi or 96,
            minimized=bool(self._user32.IsIconic(hwnd)),
            foreground=int(self._user32.GetForegroundWindow()) == hwnd,
        )

    def _process_image_name(self, hwnd: int) -> str | None:
        process_id = wintypes.DWORD()
        self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if not process_id.value:
            return None
        handle = self._kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, process_id.value
        )
        if not handle:
            return None
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not self._kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, ctypes.byref(size)
            ):
                return None
            return buffer.value
        finally:
            self._kernel32.CloseHandle(handle)

    def configure_overlay_hwnd(self, hwnd: int) -> None:
        hwnd = resolve_toplevel_hwnd(hwnd, self._user32)
        getter = getattr(self._user32, "GetWindowLongPtrW", self._user32.GetWindowLongW)
        setter = getattr(self._user32, "SetWindowLongPtrW", self._user32.SetWindowLongW)
        style = getter(hwnd, GWL_EXSTYLE)
        setter(
            hwnd,
            GWL_EXSTYLE,
            style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW,
        )

    def show_no_activate(self, hwnd: int, rect: Rect, opacity: float) -> None:
        hwnd = resolve_toplevel_hwnd(hwnd, self._user32)
        alpha = round(max(0.0, min(1.0, opacity)) * 255)
        self._user32.SetLayeredWindowAttributes(hwnd, 0, alpha, LWA_ALPHA)
        self._user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            rect.left,
            rect.top,
            rect.width,
            rect.height,
            SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )
        self._user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)

    def hide(self, hwnd: int) -> None:
        hwnd = resolve_toplevel_hwnd(hwnd, self._user32)
        self._user32.ShowWindow(hwnd, SW_HIDE)
