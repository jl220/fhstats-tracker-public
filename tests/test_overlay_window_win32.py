import inspect
from ctypes import wintypes
from types import SimpleNamespace

import forza_tracker.overlay.window_win32 as window_win32
from forza_tracker.overlay.window_win32 import (
    Rect,
    WindowsOverlayAdapter,
    _is_supported_forza_process,
    compute_overlay_geometry,
    enable_per_monitor_dpi_awareness,
)


def test_reference_geometry_matches_registration_panel() -> None:
    geometry = compute_overlay_geometry(Rect(0, 0, 2048, 1152), 1.0)
    assert geometry is not None

    assert [round(slot.center_y) for slot in geometry.map_slots] == [320, 464, 607]
    assert all(slot.right <= 348 for slot in geometry.map_slots)
    assert geometry.recommendation_window.left == 356
    assert geometry.recommendation_window.top == 684
    assert geometry.recommendation_window.width == 686
    assert geometry.recommendation_window.height == 102


def test_recommendations_are_three_separate_slots() -> None:
    geometry = compute_overlay_geometry(Rect(0, 0, 2048, 1152), 1.0)
    assert geometry is not None

    slots = geometry.recommendation_slots
    assert len(slots) == 3
    assert slots[0].right < slots[1].left
    assert slots[1].right < slots[2].left
    assert slots[0].left == geometry.recommendation_window.left
    assert slots[2].right == geometry.recommendation_window.right


def test_geometry_scales_inside_1080p_and_1440p_clients() -> None:
    for client in (Rect(0, 0, 1920, 1080), Rect(100, 50, 2660, 1490)):
        for scale in (0.75, 1.0, 1.25):
            geometry = compute_overlay_geometry(client, scale)
            assert geometry is not None
            for rect in (*geometry.map_slots, geometry.recommendation_window):
                assert client.left <= rect.left < rect.right <= client.right
                assert client.top <= rect.top < rect.bottom <= client.bottom


def test_geometry_uses_centered_safe_viewport_for_ultrawide() -> None:
    geometry = compute_overlay_geometry(Rect(0, 0, 3440, 1440), 1.0)
    assert geometry is not None
    safe_left = (3440 - 2560) // 2
    assert geometry.recommendation_window.left > safe_left


def test_geometry_rejects_too_small_client() -> None:
    assert compute_overlay_geometry(Rect(0, 0, 800, 450), 1.0) is None


def test_per_monitor_dpi_awareness_uses_public_windows_api() -> None:
    calls = []
    user32 = type(
        "User32",
        (),
        {"SetProcessDpiAwarenessContext": staticmethod(lambda context: calls.append(context) or True)},
    )()
    assert enable_per_monitor_dpi_awareness(user32) is True
    assert calls


def test_forza_window_requires_supported_process_image() -> None:
    assert _is_supported_forza_process("ForzaHorizon6.exe")
    assert _is_supported_forza_process(r"C:\Games\ForzaHorizon5.exe")
    assert not _is_supported_forza_process("chrome.exe")
    source = inspect.getsource(WindowsOverlayAdapter.find_forza_window)
    assert "self._process_image_name(hwnd)" in source


def test_tk_child_handle_resolves_to_root_before_window_operations() -> None:
    assert hasattr(window_win32, "resolve_toplevel_hwnd")
    calls = []
    user32 = SimpleNamespace(
        GetAncestor=lambda hwnd, flag: calls.append((hwnd, flag)) or 900
    )

    assert window_win32.resolve_toplevel_hwnd(123, user32) == 900
    assert calls == [(123, window_win32.GA_ROOT)]


def test_set_window_pos_uses_pointer_sized_hwnd_arguments() -> None:
    adapter = WindowsOverlayAdapter()

    assert adapter._user32.SetWindowPos.argtypes[:2] == [
        wintypes.HWND,
        wintypes.HWND,
    ]
    assert adapter._user32.SetWindowPos.restype is wintypes.BOOL
