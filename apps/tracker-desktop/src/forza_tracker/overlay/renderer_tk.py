from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image, ImageTk

from forza_tracker.config.settings import OverlaySettings
from forza_tracker.overlay.model import OverlayViewModel
from forza_tracker.overlay.window_win32 import OverlayGeometry, WindowsOverlayAdapter


TRANSPARENT_KEY = "#010203"
TEXT = "#F5F7F8"
MUTED = "#A9B1B5"
LIME = "#C7FF00"
TURQUOISE = "#18D8D0"


class ThumbnailCache:
    def __init__(
        self,
        image_loader: Callable[[Path], Any] | None = None,
        photo_factory: Callable[[Any], Any] | None = None,
    ) -> None:
        self._image_loader = image_loader or Image.open
        self._photo_factory = photo_factory or ImageTk.PhotoImage
        self._values: dict[tuple[str, tuple[int, int], int], Any] = {}

    def get(self, path: Path, size: tuple[int, int]) -> Any | None:
        try:
            modified = path.stat().st_mtime_ns
            key = (str(path), size, modified)
            if key in self._values:
                return self._values[key]
            loaded = self._image_loader(path)
            image = loaded.copy()
            close = getattr(loaded, "close", None)
            if callable(close):
                close()
            image.thumbnail(size, Image.Resampling.LANCZOS)
            photo = self._photo_factory(image)
        except (OSError, ValueError):
            return None
        self._values[key] = photo
        return photo

    def clear(self) -> None:
        self._values.clear()


class OverlayRenderer:
    def __init__(
        self,
        root: tk.Misc | None,
        window_adapter: WindowsOverlayAdapter | None,
        thumbnail_cache: ThumbnailCache | None = None,
    ) -> None:
        self._root = root
        self._adapter = window_adapter
        self._cache = thumbnail_cache or ThumbnailCache()
        self._map_windows: list[tk.Toplevel] = []
        self._recommendation_windows: list[tk.Toplevel] = []
        self._render_key: tuple[object, ...] | None = None

    def render(
        self,
        view: OverlayViewModel,
        geometry: OverlayGeometry,
        settings: OverlaySettings,
    ) -> bool:
        key = (
            view.content_signature,
            geometry.map_slots,
            geometry.recommendation_slots,
            round(settings.scale, 3),
        )
        if key == self._render_key:
            return False
        self._ensure_windows()
        self._draw(view, geometry, settings)
        self._render_key = key
        return True

    def show_no_activate(self, geometry: OverlayGeometry, opacity: float) -> None:
        self._ensure_windows()
        if self._root is not None:
            self._root.update_idletasks()
        if self._adapter is None:
            return
        for window, rect in zip(self._map_windows, geometry.map_slots):
            self._adapter.show_no_activate(window.winfo_id(), rect, opacity)
        for window, rect in zip(
            self._recommendation_windows, geometry.recommendation_slots
        ):
            self._adapter.show_no_activate(window.winfo_id(), rect, opacity)

    def hide(self) -> None:
        if self._adapter is None:
            return
        for window in self._windows():
            self._adapter.hide(window.winfo_id())

    def close(self) -> None:
        for window in self._windows():
            try:
                window.destroy()
            except tk.TclError:
                pass
        self._map_windows.clear()
        self._recommendation_windows.clear()
        self._render_key = None
        self._cache.clear()

    def _ensure_windows(self) -> None:
        if len(self._map_windows) == 3 and len(self._recommendation_windows) == 3:
            return
        if self._root is None:
            return
        self._map_windows = [self._create_live_window() for _ in range(3)]
        self._recommendation_windows = [self._create_live_window() for _ in range(3)]
        self._root.update_idletasks()
        if self._adapter is not None:
            for window in self._windows():
                self._adapter.configure_overlay_hwnd(window.winfo_id())

    def _create_live_window(self) -> tk.Toplevel:
        window = tk.Toplevel(self._root)
        window.withdraw()
        window.overrideredirect(True)
        window.configure(bg=TRANSPARENT_KEY)
        try:
            window.wm_attributes("-transparentcolor", TRANSPARENT_KEY)
        except tk.TclError:
            pass
        return window

    def _draw(
        self, view: OverlayViewModel, geometry: OverlayGeometry, settings: OverlaySettings
    ) -> None:
        if len(self._map_windows) != 3 or len(self._recommendation_windows) != 3:
            return
        for window in self._windows():
            self._clear(window)
        for window, track, slot in zip(
            self._map_windows, view.tracks, geometry.map_slots
        ):
            self._draw_map(window, track, slot)

        recommendations = list(view.recommendations)
        for index, window in enumerate(self._recommendation_windows):
            recommendation = recommendations[index] if index < len(recommendations) else None
            self._draw_recommendation(
                window,
                recommendation.label if recommendation is not None else "",
                recommendation.car_name if recommendation is not None else "",
                view,
                index,
                settings.scale,
            )

    def _draw_map(self, window, track, slot) -> None:
        if not track.image_path:
            return
        photo = self._cache.get(track.image_path, (slot.width, slot.height))
        if photo is None:
            return
        tk.Label(window, image=photo, bg=TRANSPARENT_KEY, borderwidth=0).place(
            relx=0.5, rely=0.5, anchor="center"
        )

    def _draw_recommendation(
        self,
        window,
        label: str,
        car_name: str,
        view: OverlayViewModel,
        index: int,
        scale: float,
    ) -> None:
        font_size = max(8, round(9 * scale))
        metadata = view.car_class if index == 0 else f"{view.recognized_count}/3" if index == 2 else ""
        tk.Label(
            window,
            text=metadata,
            bg=TRANSPARENT_KEY,
            fg=LIME if index == 0 else MUTED,
            font=("Segoe UI Semibold" if index == 0 else "Segoe UI", font_size),
        ).pack(anchor="w" if index != 2 else "e")
        if not car_name:
            return
        row = tk.Frame(window, bg=TRANSPARENT_KEY)
        row.pack(fill="x", pady=(4, 0))
        tk.Label(
            row,
            text=f"{label} ·",
            bg=TRANSPARENT_KEY,
            fg=TURQUOISE,
            font=("Segoe UI", font_size),
        ).pack(side="left")
        tk.Label(
            row,
            text=car_name,
            bg=TRANSPARENT_KEY,
            fg=TEXT,
            font=("Segoe UI Semibold", font_size),
        ).pack(side="left", padx=(4, 0))

    def _windows(self) -> tuple[tk.Toplevel, ...]:
        return tuple(self._map_windows + self._recommendation_windows)

    @staticmethod
    def _clear(parent) -> None:
        for child in parent.winfo_children():
            child.destroy()
