from __future__ import annotations

import ctypes
from collections.abc import Callable
from ctypes import wintypes

from forza_tracker.overlay.controller import OverlayAction


VK_ESCAPE = 0x1B
VK_RETURN = 0x0D
VK_UP = 0x26
XINPUT_GAMEPAD_DPAD_UP = 0x0001
XINPUT_GAMEPAD_A = 0x1000
XINPUT_GAMEPAD_B = 0x2000
THUMB_UP_THRESHOLD = 16000


class _XInputGamepad(ctypes.Structure):
    _fields_ = [
        ("wButtons", wintypes.WORD),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


class _XInputState(ctypes.Structure):
    _fields_ = [("dwPacketNumber", wintypes.DWORD), ("Gamepad", _XInputGamepad)]


class WindowsInputPoller:
    def __init__(
        self,
        key_reader: Callable[[int], bool] | None = None,
        gamepad_reader: Callable[[], tuple[int, int] | None] | None = None,
    ) -> None:
        self._key_reader = key_reader or _read_key
        self._gamepad_reader = gamepad_reader or _create_gamepad_reader()
        self._previous = {action: False for action in OverlayAction}

    def poll(self) -> tuple[OverlayAction, ...]:
        current = self._read_states()
        order = (OverlayAction.BACK, OverlayAction.UP, OverlayAction.CONFIRM)
        actions = tuple(
            action for action in order if current[action] and not self._previous[action]
        )
        self._previous = current
        return actions

    def reset(self) -> None:
        self._previous = self._read_states()

    def _read_states(self) -> dict[OverlayAction, bool]:
        gamepad = self._gamepad_reader()
        buttons, thumb_y = gamepad if gamepad is not None else (0, 0)
        return {
            OverlayAction.BACK: self._key_reader(VK_ESCAPE)
            or bool(buttons & XINPUT_GAMEPAD_B),
            OverlayAction.UP: self._key_reader(VK_UP)
            or bool(buttons & XINPUT_GAMEPAD_DPAD_UP)
            or thumb_y >= THUMB_UP_THRESHOLD,
            OverlayAction.CONFIRM: self._key_reader(VK_RETURN)
            or bool(buttons & XINPUT_GAMEPAD_A),
        }


def _read_key(virtual_key: int) -> bool:
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(virtual_key) & 0x8000)
    except (AttributeError, OSError):
        return False


def _create_gamepad_reader() -> Callable[[], tuple[int, int] | None]:
    function = None
    for library_name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
        try:
            library = ctypes.WinDLL(library_name)
            function = library.XInputGetState
            break
        except (AttributeError, OSError):
            continue

    def read() -> tuple[int, int] | None:
        if function is None:
            return None
        state = _XInputState()
        try:
            if function(0, ctypes.byref(state)) != 0:
                return None
        except OSError:
            return None
        return int(state.Gamepad.wButtons), int(state.Gamepad.sThumbLY)

    return read
