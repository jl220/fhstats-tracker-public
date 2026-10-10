from __future__ import annotations

import atexit
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import time
import uuid
import webbrowser
from collections import deque
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from forza_tracker.paths import application_root, ensure_user_data_layout, user_data_root

_INSTANCE_LOCK_FILE: Any = None


def _early_project_root() -> Path:
    return user_data_root()


def _acquire_file_instance_lock() -> bool:
    global _INSTANCE_LOCK_FILE
    if _INSTANCE_LOCK_FILE is not None:
        return True

    data_dir = _early_project_root() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    lock_dir = data_dir / "fhstats-tracker.lockdir"
    pid_file = lock_dir / "pid"

    try:
        lock_dir.mkdir()
    except FileExistsError:
        stale_pid = _read_lock_pid(pid_file)
        if stale_pid is not None and not _is_pid_running(stale_pid):
            shutil.rmtree(lock_dir, ignore_errors=True)
            try:
                lock_dir.mkdir()
            except FileExistsError:
                return False
        else:
            return False

    pid_file.write_text(str(os.getpid()), encoding="ascii")
    atexit.register(lambda: shutil.rmtree(lock_dir, ignore_errors=True))
    _INSTANCE_LOCK_FILE = lock_dir
    return True


def _read_lock_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


def _is_pid_running(pid: int) -> bool:
    if os.name != "nt":
        return Path(f"/proc/{pid}").exists()
    creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    try:
        result = subprocess.run(
            ["tasklist", "/fi", f"PID eq {pid}", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            timeout=2,
            creationflags=creationflags,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    return str(pid) in result.stdout


def _explorer_reveal_command(directory: Path, selected_file: Path | None) -> list[str] | None:
    if os.name != "nt" or selected_file is None or not selected_file.exists():
        return None
    try:
        selected_file.resolve().relative_to(directory.resolve())
    except (OSError, ValueError):
        return None
    return ["explorer.exe", f"/select,{selected_file}"]


def _activate_existing_dashboard_window() -> None:
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enum_callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_callback, wintypes.LPARAM]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]

    def activate(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        if title.value == "Forza Horizon Tracker":
            user32.ShowWindow(hwnd, 9)
            user32.SetForegroundWindow(hwnd)
            return False
        return True

    user32.EnumWindows(enum_callback(activate), 0)


def _apply_windows_titlebar(window: tk.Misc) -> None:
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    try:
        window.update_idletasks()
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        dwmapi.DwmSetWindowAttribute.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            wintypes.LPCVOID,
            wintypes.DWORD,
        ]
        dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
        hwnd = user32.GetAncestor(window.winfo_id(), 2) or window.winfo_id()

        dark_mode = ctypes.c_int(1)
        dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark_mode), ctypes.sizeof(dark_mode))

        def colorref(hex_color: str) -> ctypes.c_uint:
            value = hex_color.lstrip("#")
            red, green, blue = (int(value[index:index + 2], 16) for index in (0, 2, 4))
            return ctypes.c_uint(red | (green << 8) | (blue << 16))

        caption = colorref(COLORS["bg"])
        border = colorref(COLORS["bg"])
        text = colorref(COLORS["text"])
        dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), ctypes.sizeof(caption))
        dwmapi.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border), ctypes.sizeof(border))
        dwmapi.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(text), ctypes.sizeof(text))
    except (AttributeError, OSError, tk.TclError):
        return


if (
    __name__ == "__main__"
    and "--capture" not in sys.argv
    and "--restart-wait" not in sys.argv
    and not _acquire_file_instance_lock()
):
    _activate_existing_dashboard_window()
    sys.exit(0)

import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageTk
import pystray

from forza_tracker.capture.screen_capture import create_capture_backend
from forza_tracker.api.client import ApiClient
from forza_tracker.car_catalog.fh6 import load_fh6_car_catalog
from forza_tracker.config.settings import load_settings
from forza_tracker.config.user_config import (
    load_editable_config as load_user_config,
    save_language,
    write_local_config as write_user_config,
)
from forza_tracker.garage.export import export_fhstats_package
from forza_tracker.garage.models import GarageCar, ScanOptions
from forza_tracker.garage.profile_backup import (
    ProfileBackupError,
    create_profile_backup,
    load_profile_backup_manifest,
)
from forza_tracker.garage.processor import save_alias
from forza_tracker.garage.recognition import card_text_to_catalog_query
from forza_tracker.garage.storage import GarageCountDropWarning, GarageInvalidDraftError, GarageStore
from forza_tracker.garage.video import copy_finalized_recording
from forza_tracker.fhstats_client import FhstatsCatalogClient, SyncAsset
from forza_tracker.local_catalog import data_root as local_catalog_root, load_tierlist
from forza_tracker.rivals_runtime import RivalsRuntime, EventContextGate
from forza_tracker.overlay.event_state import read_overlay_event
from forza_tracker.ui.rivals_panel import RivalsLivePanel, RivalsHistoryPanel
from forza_tracker.local_recommender import recommend_championship
from forza_tracker.overlay.runtime import OverlayRuntime
from forza_tracker.overlay.window_win32 import enable_per_monitor_dpi_awareness
from forza_tracker.parser.ocr import TesseractOcr
from forza_tracker.telemetry.status import read_telemetry_status
from forza_tracker.ui.theme import COLORS, configure_theme
from forza_tracker.ui.viewport import PageViewport
from forza_tracker.ui.dashboard_responsive import select_dashboard_layout
from forza_tracker.ui.dashboard_view_model import DashboardViewModel, build_dashboard_view_model
from forza_tracker.ui.garage_view_model import GarageCardView, build_garage_cards
from forza_tracker.ui.catalog_view_model import (
    build_meta_tier_sections,
)
from forza_tracker.garage.runner import GarageRawScanResult, run_garage_scan_script
from forza_tracker.garage.runner import GarageRawScanResult, run_garage_scan_script
from forza_tracker.recommendation import read_recommendation, recommendation_signature, write_recommendation
from forza_tracker.recording.control import CONTROL_PATH, read_recording_status, send_recording_command
from forza_tracker.restart import build_restart_command
from forza_tracker.runtime_status import STATUS_PATH, read_status, sanitize_error_message
from forza_tracker.updater import UpdateInfo, check_for_update, download_update, launch_installer, is_newer_version
from forza_tracker.release_notes import cache_release, installed_release_notes, load_cached_release
from forza_tracker.ui.news import NewsPage
from forza_tracker.ui.history import HistoryEntry, HistoryRepository, RaceEntry, build_race_history, summarize
from forza_tracker.ui.event_progress_view_model import build_event_progress_rows, format_event_times
from forza_tracker.ui.track_map_view_model import build_track_map_panel
from forza_tracker.version import APP_VERSION
from forza_tracker.localization import configure_language, current_language, normalize_language, tr, translate_source
from forza_tracker.ui.localized_tk import install_tkinter_localization


install_tkinter_localization(tk, ttk, messagebox, filedialog)


class LocalizedStringVar(tk.StringVar):
    def __init__(self, master: tk.Misc | None = None, value: object = "", name: str | None = None) -> None:
        super().__init__(master=master, value=translate_source(value), name=name)

    def set(self, value: object) -> None:
        super().set(translate_source(value))


class LocalizedChoiceVar(tk.StringVar):
    def __init__(
        self,
        master: tk.Misc | None = None,
        *,
        choices: dict[str, str],
        value: str,
        name: str | None = None,
    ) -> None:
        self._choice_labels = {choice: tr(label_key) for choice, label_key in choices.items()}
        self._label_choices = {label: choice for choice, label in self._choice_labels.items()}
        super().__init__(master=master, value=self._choice_labels.get(value, value), name=name)

    def get(self) -> str:
        displayed = super().get()
        return self._label_choices.get(displayed, displayed)

    def set(self, value: object) -> None:
        internal = str(value)
        super().set(self._choice_labels.get(internal, internal))

    def display_values(self, values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
        return tuple(self._choice_labels.get(value, value) for value in values)


def _project_root() -> Path:
    return application_root()


PROJECT_ROOT = _project_root()
DATA_ROOT = user_data_root()
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "tracker.yaml"
APP_ICON = PROJECT_ROOT / "assets" / "fhstats-tracker.ico"
AUTO_REFRESH_MS = 250
VISUAL_REFRESH_VERSION = "0.4.6"
FHSTATS_RIVALS_URL = "https://fhstats.com/rivals"
FHSTATS_META_CARS_URL = "https://fhstats.com/metacars"
GAME_PROCESS_NAMES = {"forzahorizon6.exe", "forzahorizon5.exe"}
GAME_POLL_MS = 2500
CAPTURE_HEARTBEAT_STALE_SECONDS = 12
CAPTURE_STARTUP_GRACE_SECONDS = 20
WATCHDOG_RESTART_WINDOW_SECONDS = 300
WATCHDOG_MAX_RESTARTS = 3
PLAYER_PLACEHOLDERS = {"", "player", "setyourgamertag", "unknown"}
_INSTANCE_MUTEX_HANDLE: int | None = None

PAGE_SPECS = (
    ("home", "nav.home", "⌂"),
    ("history", "nav.history", "≡"),
    ("videos", "nav.videos", "▶"),
    ("tierlist", "nav.tierlist", "◆"),
    ("rivals", "nav.rivals", "⚑"),
    ("garage", "nav.garage", "▦"),
    ("news", "nav.news", "◷"),
    ("settings", "nav.settings", "⚙"),
)

_PAGE_ALIASES = {
    "inicio": "home",
    "home": "home",
    "historial": "history",
    "history": "history",
    "vídeos": "videos",
    "videos": "videos",
    "tierlist": "tierlist",
    "meta cars": "tierlist",
    "rivals": "rivals",
    "mi garaje": "garage",
    "my garage": "garage",
    "garage": "garage",
    "novedades": "news",
    "what's new": "news",
    "ajustes": "settings",
    "settings": "settings",
}


def normalize_page_id(value: str) -> str:
    normalized = str(value or "").strip().casefold()
    page_ids = {page_id for page_id, _label_key, _icon in PAGE_SPECS}
    if normalized in page_ids:
        return normalized
    return _PAGE_ALIASES.get(normalized, "home")




def visible_page_specs() -> tuple[tuple[str, str, str], ...]:
    return PAGE_SPECS


def language_choice_required(settings: Any) -> bool:
    return normalize_language(getattr(settings.app, "language", None)) is None


SUMMARY_DISCIPLINE_FILTERS = [
    ("all", "Todas"),
    ("road", "Carretera"),
    ("dirt", "Tierra"),
    ("cross_country", "Campo a través"),
    ("street", "Callejera"),
]
SUMMARY_CLASS_FILTERS = ["all", "D 400", "C 500", "B 600", "A 700", "S1 800", "S2 900", "R 998"]


def should_auto_start_capture(
    *,
    game_running: bool,
    capture_running: bool,
    has_valid_player_name: bool,
    auto_capture_suppressed: bool,
) -> bool:
    return game_running and not capture_running and has_valid_player_name and not auto_capture_suppressed








class EmbeddedMpvPlayer:
    def __init__(self, hwnd: int, mpv_path: Path) -> None:
        self._hwnd = hwnd
        self._mpv_path = mpv_path
        self._pipe_name = rf"\\.\pipe\fhstats-mpv-{os.getpid()}-{uuid.uuid4().hex}"
        self._process: subprocess.Popen[str] | None = None
        self._pipe: Any = None
        self._worker: threading.Thread | None = None
        self._commands: queue.Queue[list[Any] | None] = queue.Queue()
        self._state_lock = threading.Lock()
        self._running = False
        self.time_pos = 0.0
        self.duration = 0.0
        self.pause = True
        self.eof = False

    @property
    def is_running(self) -> bool:
        return self._running and self._process is not None and self._process.poll() is None and self._pipe is not None

    def start(self) -> bool:
        if self.is_running:
            return True
        if not self._mpv_path.exists():
            return False
        creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        command = [
            str(self._mpv_path),
            "--idle=yes",
            "--force-window=yes",
            f"--wid={self._hwnd}",
            f"--input-ipc-server={self._pipe_name}",
            "--no-terminal",
            "--msg-level=all=no",
            "--osc=no",
            "--osd-level=0",
            "--keep-open=yes",
            "--hwdec=auto-safe",
            "--vo=gpu-next",
            "--profile=fast",
        ]
        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                creationflags=creationflags,
            )
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline:
                if self._process.poll() is not None:
                    return False
                try:
                    self._pipe = open(self._pipe_name, "r+b", buffering=0)
                    break
                except OSError:
                    time.sleep(0.05)
            if self._pipe is None:
                self.close()
                return False
            self._running = True
            self._worker = threading.Thread(target=self._ipc_loop, name="fhstats-mpv-ipc", daemon=True)
            self._worker.start()
            for name in ("time-pos", "duration", "pause", "eof-reached"):
                self.command("observe_property", 0, name)
            self.command("set_property", "pause", True)
            return True
        except OSError:
            self.close()
            return False

    def load(self, path: Path, paused: bool = True) -> None:
        with self._state_lock:
            self.time_pos = 0.0
            self.duration = 0.0
            self.pause = paused
            self.eof = False
        self.command("set_property", "pause", paused)
        self.command("loadfile", str(path), "replace")
        self.command("set_property", "pause", paused)

    def set_pause(self, paused: bool) -> None:
        with self._state_lock:
            self.pause = paused
        self.command("set_property", "pause", paused)

    def stop(self) -> None:
        self.set_pause(True)
        self.seek(0.0)

    def seek(self, seconds: float) -> None:
        seconds = max(0.0, float(seconds))
        with self._state_lock:
            self.time_pos = seconds
            self.eof = False
        self.command("seek", seconds, "absolute", "exact")

    def snapshot(self) -> tuple[float, float, bool, bool]:
        with self._state_lock:
            return self.time_pos, self.duration, self.pause, self.eof

    def command(self, *command: Any) -> None:
        if not self._running:
            return
        self._commands.put(list(command))

    def _write_command(self, command: list[Any]) -> None:
        pipe = self._pipe
        if pipe is None:
            return
        payload = json.dumps({"command": list(command)}, ensure_ascii=True).encode("utf-8") + b"\n"
        try:
            pipe.write(payload)
            pipe.flush()
        except (OSError, ValueError):
            self._running = False

    def close(self) -> None:
        self._running = False
        try:
            self._commands.put(["quit"])
            self._commands.put(None)
        except Exception:
            pass
        pipe = self._pipe
        self._pipe = None
        if pipe is not None:
            try:
                pipe.close()
            except OSError:
                pass
        process = self._process
        self._process = None
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass

    def _ipc_loop(self) -> None:
        pipe = self._pipe
        if pipe is None:
            return
        while self._running:
            try:
                self._flush_pending_commands()
                self._read_available_events(pipe)
            except (OSError, ValueError):
                break
            time.sleep(0.015)
        self._running = False

    def _flush_pending_commands(self) -> None:
        while self._running:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                return
            if command is None:
                self._running = False
                return
            self._write_command(command)

    def _read_available_events(self, pipe: Any) -> None:
        while self._running and self._pipe_bytes_available(pipe) > 0:
            line = pipe.readline()
            if not line:
                return
            try:
                event = json.loads(line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            if event.get("event") == "property-change":
                self._handle_property_change(event)
            elif event.get("event") == "end-file":
                with self._state_lock:
                    self.eof = True
                    self.pause = True

    @staticmethod
    def _pipe_bytes_available(pipe: Any) -> int:
        if os.name != "nt":
            return 0
        try:
            import ctypes
            import msvcrt

            available = ctypes.c_ulong(0)
            handle = msvcrt.get_osfhandle(pipe.fileno())
            ok = ctypes.windll.kernel32.PeekNamedPipe(
                ctypes.c_void_p(handle),
                None,
                0,
                None,
                ctypes.byref(available),
                None,
            )
            return int(available.value) if ok else 0
        except (OSError, ValueError, AttributeError):
            return 0

    def _handle_property_change(self, event: dict[str, Any]) -> None:
        name = event.get("name")
        data = event.get("data")
        with self._state_lock:
            if name == "time-pos" and isinstance(data, (int, float)):
                self.time_pos = max(0.0, float(data))
            elif name == "duration" and isinstance(data, (int, float)):
                self.duration = max(0.0, float(data))
            elif name == "pause" and isinstance(data, bool):
                self.pause = data
            elif name == "eof-reached" and isinstance(data, bool):
                self.eof = data


class TrackerDashboard(tk.Tk):
    def __init__(self, history_path: Path, *, rivals_runtime=None) -> None:
        super().__init__()
        self.title("Forza Horizon Tracker")
        self.geometry("1180x820")
        self.minsize(880, 540)
        self.configure(bg=COLORS["bg"])
        if APP_ICON.exists():
            self.iconbitmap(default=str(APP_ICON))

        self._config_path = _resolve_config_path()
        self._settings = load_settings(self._config_path)
        if language_choice_required(self._settings):
            self.withdraw()
            selected_language = self._choose_initial_language()
            if selected_language is None:
                tk.Tk.destroy(self)
                raise SystemExit(0)
            self._config_path = _resolve_config_path()
            self._settings = load_settings(self._config_path)
        configure_language(self._settings.app.language or "en")
        self.deiconify()

        self._repository = HistoryRepository(history_path)
        self._entries: list[HistoryEntry] = []
        self._races: list[RaceEntry] = []
        self._summary_discipline_filter = tk.StringVar(value="all")
        self._summary_class_filter = LocalizedChoiceVar(
            choices={"all": "filter.all"},
            value="all",
        )
        self._summary_filter_buttons: list[tuple[tk.Button, str, str]] = []
        self._capture_active = tk.BooleanVar(value=False)
        self._player_name = tk.StringVar(value=self._settings.app.player_name)
        self._player_display = LocalizedStringVar(value=self._player_display_text())
        self._status = LocalizedStringVar(value="Esperando datos...")
        self._capture_status = LocalizedStringVar(value="Captura detenida")
        self._game_status = LocalizedStringVar(value="Esperando Forza Horizon")
        self._capture_process: subprocess.Popen[bytes] | None = None
        self._capture_started_monotonic: float | None = None
        self._watchdog_restarts: deque[float] = deque()
        self._last_reported_error: str | None = None
        self._history_signature: tuple[int, int] | None = None
        self._recommendation_signature: tuple[int, int] | None = None
        self._last_auto_opened_event: tuple[tuple[str, ...], str] | None = None
        self._recommendation_monitor_initialized = False
        self._recommendation_retry_started = False
        self._available_update: UpdateInfo | None = None
        self._update_busy = False
        self._update_events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._news_cache_path = DATA_ROOT / "data" / "runtime" / "release-notes.json"
        self._release_snapshot = load_cached_release(self._news_cache_path)
        self._release_snapshot_cached = True
        self._tray_icon: pystray.Icon | None = None
        self._is_exiting = False
        self._game_was_running = False
        self._auto_capture_suppressed = False
        self._active_page = "home"
        self._dashboard_layout = None
        self._dashboard_layout_after_id: str | None = None
        self._event_map_size = select_dashboard_layout(1280, 720).event_map_size
        self._recording_state = LocalizedStringVar(value="Grabación detenida")
        self._recording_fps = tk.IntVar(value=self._settings.recording.framerate)
        self._recording_audio = tk.BooleanVar(value=self._settings.recording.capture_audio)
        self._recording_storage = tk.StringVar(value=f"{self._settings.recording.max_storage_gb:g}")
        self._recording_mode = tk.StringVar(value=self._settings.recording.mode)
        self._language_setting = tk.StringVar(value=self._settings.app.language or "en")
        self._recording_output_dir = tk.StringVar(value=self._recording_output_dir_display(self._settings.recording.output_dir))
        self._telemetry_enabled = tk.BooleanVar(value=self._settings.telemetry.enabled)
        self._overlay_enabled = tk.BooleanVar(value=self._settings.overlay.enabled)
        self._overlay_opacity = tk.DoubleVar(value=self._settings.overlay.opacity)
        self._overlay_scale = tk.DoubleVar(value=self._settings.overlay.scale)
        self._overlay_duration = tk.IntVar(value=self._settings.overlay.duration_seconds)
        self._video_paths: dict[str, Path] = {}
        self._selected_video_path: Path | None = None
        self._video_player: EmbeddedMpvPlayer | None = None
        self._video_after_job: str | None = None
        self._video_overlay_after_job: str | None = None
        self._video_pointer_after_job: str | None = None
        self._video_overlay_hidden = False
        self._video_overlay_hidden_pointer: tuple[int, int] | None = None
        self._video_playing = False
        self._video_dragging = False
        self._video_seek_was_playing = False
        self._video_fps = 30.0
        self._video_total_frames = 0
        self._video_duration_seconds = 0.0
        self._video_display_frame_index = 0
        self._video_play_anchor_frame = 0
        self._video_play_anchor_time = 0.0
        self._video_current_frame: Any = None
        self._video_frame_photo: ImageTk.PhotoImage | None = None
        self._video_title = LocalizedStringVar(value="Selecciona una partida")
        self._video_meta = LocalizedStringVar(value="Los videos grabados apareceran aqui.")
        self._video_status = LocalizedStringVar(value="Sin video seleccionado")
        self._video_position = tk.DoubleVar(value=0.0)
        self._video_time = tk.StringVar(value="00:00 / 00:00")
        self._videos_summary = LocalizedStringVar(value="0 videos")
        self._tierlist_rows: list[dict[str, str]] = []
        self._catalog_sync_status = LocalizedStringVar(value="Catalogo local listo")
        self._catalog_sync_running = False
        self._garage_store = GarageStore(DATA_ROOT / "garage")
        self._profile_backup_path = DATA_ROOT / "garage" / "profile_backup"
        self._profile_backup_status = LocalizedStringVar(value="Sin copia del progreso")
        self._garage_status = LocalizedStringVar(value="Sin escaneos guardados")
        self._garage_summary = LocalizedStringVar(value="0 coches")
        self._garage_record_video = tk.BooleanVar(value=True)
        self._garage_slot = tk.StringVar(value="current")
        self._garage_query = tk.StringVar(value="")
        self._garage_manufacturer = LocalizedChoiceVar(choices={"all": "filter.all"}, value="all")
        self._garage_class = LocalizedChoiceVar(choices={"all": "filter.all"}, value="all")
        self._garage_confidence = LocalizedChoiceVar(
            choices={"all": "filter.all", "confirmed": "filter.confirmed", "review": "filter.review"},
            value="all",
        )
        self._garage_worker_running = False
        self._garage_stop_event: threading.Event | None = None
        self._garage_card_photos: list[ImageTk.PhotoImage] = []
        self._garage_query.trace_add("write", lambda *_args: self._refresh_garage_cards())

        self._rivals_runtime = rivals_runtime or RivalsRuntime(DATA_ROOT, local_catalog_root(), self._player_name.get())
        self._event_context_gate = EventContextGate()
        self._rivals_profile = self._player_name.get()
        self._event_display_mode = None
        self._configure_style()
        self._build_layout()
        self._rivals_runtime.start()
        self.after(250, self._poll_rivals)
        self._overlay_runtime = OverlayRuntime(self, self._settings.overlay)
        self._overlay_runtime.start()
        self.after(0, lambda: _apply_windows_titlebar(self))
        self.after(250, lambda: _apply_windows_titlebar(self))
        if not self._has_valid_player_name():
            self.after(300, lambda: self._open_gamertag_settings(first_run=True))
        self.protocol("WM_DELETE_WINDOW", self._hide_to_tray)
        self.refresh(force=True)
        self.after(AUTO_REFRESH_MS, self._auto_refresh)
        self.after(1000, self._poll_capture_process)
        self.after(1500, self._monitor_game_process)
        self.after(100, self._drain_update_events)
        self.after(2500, self._check_for_updates_silently)
        self.after(100, self._ensure_window_visible)
        self.after(1200, self._start_tray_icon)

    def _choose_initial_language(self) -> str | None:
        selected: list[str | None] = [None]
        dialog = tk.Toplevel(self)
        dialog.title("Choose language / Elegir idioma")
        dialog.geometry("430x380")
        dialog.resizable(False, False)
        dialog.configure(bg=COLORS["bg"])
        dialog.transient(self)
        dialog.grab_set()

        tk.Label(
            dialog,
            text="Choose your language",
            bg=COLORS["bg"],
            fg=COLORS["text"],
            font=("Segoe UI Semibold", 22),
        ).pack(anchor="w", padx=26, pady=(26, 2))
        tk.Label(
            dialog,
            text="Elige tu idioma",
            bg=COLORS["bg"],
            fg=COLORS["muted"],
            font=("Segoe UI", 10),
        ).pack(anchor="w", padx=26, pady=(0, 18))

        language = tk.StringVar(value="en")
        for label, value in (("English", "en"), ("Español", "es")):
            tk.Radiobutton(
                dialog,
                text=label,
                value=value,
                variable=language,
                bg=COLORS["panel"],
                fg=COLORS["text"],
                activebackground=COLORS["panel"],
                activeforeground=COLORS["text"],
                selectcolor=COLORS["panel2"],
                highlightthickness=0,
                bd=0,
                anchor="w",
                padx=14,
                pady=10,
                font=("Segoe UI Semibold", 11),
            ).pack(fill="x", padx=26, pady=3)

        feedback = tk.StringVar(value="")
        tk.Label(
            dialog,
            textvariable=feedback,
            bg=COLORS["bg"],
            fg=COLORS["danger"],
            font=("Segoe UI", 9),
        ).pack(anchor="w", padx=26, pady=(5, 0))

        def continue_with_selection() -> None:
            chosen = normalize_language(language.get()) or "en"
            try:
                save_language(chosen)
            except OSError:
                feedback.set("Could not save the language / No se pudo guardar el idioma")
                return
            selected[0] = chosen
            dialog.destroy()

        tk.Button(
            dialog,
            text="Continue / Continuar",
            command=continue_with_selection,
            bg=COLORS["accent"],
            fg=COLORS["bg"],
            activebackground="#d7ff52",
            activeforeground=COLORS["bg"],
            bd=0,
            padx=18,
            pady=9,
            font=("Segoe UI Semibold", 10),
            cursor="hand2",
        ).pack(anchor="e", padx=26, pady=(12, 20))
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.wait_window()
        return selected[0]

    def _configure_style(self) -> None:
        configure_theme(self)

    def _build_section_header(
        self,
        parent: tk.Misc,
        title: str,
        subtitle: str,
        *,
        background: str | None = None,
        pady: tuple[int, int] = (0, 14),
    ) -> tk.Frame:
        bg = background or COLORS["bg"]
        header = tk.Frame(parent, bg=bg)
        header.pack(fill="x", pady=pady)
        copy = tk.Frame(header, bg=bg)
        copy.pack(side="left", fill="x", expand=True)
        tk.Label(
            copy,
            text=title,
            bg=bg,
            fg=COLORS["text"],
            font=("Segoe UI Semibold", 22),
            anchor="w",
        ).pack(fill="x")
        subtitle_label = tk.Label(
            copy,
            text=subtitle,
            bg=bg,
            fg=COLORS["muted"],
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
            wraplength=760,
        )
        subtitle_label.pack(fill="x", pady=(3, 0))
        copy.bind("<Configure>", lambda event: subtitle_label.configure(wraplength=max(200, event.width)), add="+")
        return header

    def _build_empty_state(
        self,
        parent: tk.Misc,
        title: str,
        message: str,
        *,
        background: str | None = None,
    ) -> tk.Frame:
        bg = background or COLORS["panel"]
        frame = tk.Frame(
            parent,
            bg=bg,
            highlightthickness=1,
            highlightbackground=COLORS["line_soft"],
            padx=16,
            pady=14,
        )
        tk.Label(
            frame,
            text=title,
            bg=bg,
            fg=COLORS["text"],
            font=("Segoe UI Semibold", 11),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            frame,
            text=message,
            bg=bg,
            fg=COLORS["muted"],
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
            wraplength=520,
        ).pack(fill="x", pady=(4, 0))
        return frame

    def _insert_tree_empty_state(self, tree: ttk.Treeview, message: str, columns: int) -> None:
        tree.insert(
            "",
            "end",
            iid=f"{id(tree)}-empty",
            tags=("empty",),
            values=(message, *[""] * max(0, columns - 1)),
        )

    def _make_status_badge(
        self,
        parent: tk.Misc,
        textvariable: tk.StringVar,
        *,
        foreground: str | None = None,
        background: str | None = None,
    ) -> tk.Label:
        bg = background or COLORS["surface2"]
        return tk.Label(
            parent,
            textvariable=textvariable,
            bg=bg,
            fg=foreground or COLORS["accent2"],
            font=("Segoe UI Semibold", 9),
            padx=10,
            pady=6,
            anchor="center",
        )

    def _build_segmented_button(
        self,
        parent: tk.Misc,
        text: str,
        variable: tk.Variable,
        value: Any,
        *,
        command: Any = None,
    ) -> tk.Radiobutton:
        return tk.Radiobutton(
            parent,
            text=text,
            variable=variable,
            value=value,
            command=command,
            bg=COLORS["surface2"],
            fg=COLORS["text"],
            activebackground=COLORS["surface"],
            activeforeground=COLORS["text"],
            selectcolor=COLORS["surface"],
            indicatoron=False,
            bd=0,
            highlightthickness=1,
            highlightbackground=COLORS["line_soft"],
            padx=12,
            pady=7,
            font=("Segoe UI Semibold", 9),
            cursor="hand2",
        )

    def _build_layout(self) -> None:
        self.configure(menu="")
        shell = tk.Frame(self, bg=COLORS["bg"])
        shell.pack(fill="both", expand=True)
        self._build_sidebar(shell)
        root = ttk.Frame(shell, padding=20)
        root.pack(side="left", fill="both", expand=True)

        header = tk.Frame(root, bg=COLORS["bg"])
        header.pack(fill="x", pady=(2, 18))
        identity = tk.Frame(header, bg=COLORS["bg"])
        identity.pack(side="left", fill="x", expand=True)
        tk.Label(identity, text="FHStats Tracker", bg=COLORS["bg"], fg=COLORS["text"],
                 font=("Segoe UI Semibold", 16), anchor="w").pack(anchor="w")
        tk.Label(identity, textvariable=self._player_display, bg=COLORS["bg"],
                 fg=COLORS["muted"], font=("Segoe UI", 10)).pack(anchor="w", pady=(3, 0))
        actions = tk.Frame(header, bg=COLORS["bg"])
        actions.pack(side="right")
        self._capture_button = ttk.Button(actions, text="Iniciar captura", style="Accent.TButton", command=self._toggle_capture)
        self._capture_button.pack(side="right")
        self._update_button = ttk.Button(actions, text="", command=lambda: self._show_page("news"))
        status_row = tk.Frame(root, bg=COLORS["bg"])
        status_row.pack(fill="x", pady=(0, 18))
        self._make_status_badge(status_row, self._game_status).pack(side="left")
        self._make_status_badge(status_row, self._capture_status, foreground=COLORS["muted"]).pack(side="left", padx=8)
        tk.Frame(root, bg=COLORS["line_soft"], height=1).pack(fill="x", pady=(0, 18))

        self._page_host = tk.Frame(root, bg=COLORS["bg"])
        self._page_host.pack(fill="both", expand=True)
        self._pages: dict[str, tk.Frame] = {}
        page_content: dict[str, tk.Frame] = {}
        for page_id, _label_key, _icon in PAGE_SPECS:
            if page_id in {"history", "videos", "rivals", "tierlist", "garage"}:
                viewport = PageViewport(self._page_host, background=COLORS["bg"])
                self._pages[page_id] = viewport
                page_content[page_id] = viewport.content
            else:
                self._pages[page_id] = tk.Frame(self._page_host, bg=COLORS["bg"])
                page_content[page_id] = self._pages[page_id]

        home_page = self._pages["home"]
        self._home_canvas = tk.Canvas(
            home_page,
            bg=COLORS["bg"],
            borderwidth=0,
            highlightthickness=0,
            takefocus=0,
        )
        self._home_scrollbar = ttk.Scrollbar(home_page, orient="vertical", command=self._home_canvas.yview)
        self._home_canvas.configure(yscrollcommand=self._home_scrollbar.set)
        self._home_canvas.pack(side="left", fill="both", expand=True)
        self._compact_frame = ttk.Frame(self._home_canvas, padding=16)
        self._home_canvas_window = self._home_canvas.create_window((0, 0), anchor="nw", window=self._compact_frame)
        self._home_canvas.bind("<Configure>", self._schedule_dashboard_layout)
        self._compact_frame.bind("<Configure>", self._sync_home_scroll_region)
        self.bind_all("<MouseWheel>", self._scroll_home_on_mousewheel, add="+")
        # Keep historical summary callbacks alive without mounting the former
        # race dashboard above the active event. Responsive packing stays here.
        self._race_summary_host = tk.Frame(self._compact_frame, bg=COLORS["bg"])
        self._build_home_stats(self._race_summary_host)
        self._build_compact_panel(self._compact_frame)
        self.after(0, self._schedule_dashboard_layout)

        self._history_tab = ttk.Frame(page_content["history"], padding=12)
        self._history_tab.pack(fill="both", expand=True)
        self._build_history_tab(self._history_tab)
        self._build_rivals_page(page_content["rivals"])
        self._build_tierlist_page(page_content["tierlist"])
        self._build_videos_page(page_content["videos"])
        self._build_garage_page(page_content["garage"])
        self._build_settings_page(self._pages["settings"])
        self._news_page = NewsPage(self._pages["news"], colors=COLORS,
                                   on_check=self._check_for_updates, on_install=self._accept_available_update)
        self._news_page.pack(fill="both", expand=True)
        self._render_release_notes()
        self._show_page(os.environ.get("FHSTATS_START_PAGE", "home"))

    def _schedule_dashboard_layout(self, event: tk.Event[tk.Misc] | None = None) -> None:
        del event
        if self._dashboard_layout_after_id is not None:
            self.after_cancel(self._dashboard_layout_after_id)
        self._dashboard_layout_after_id = self.after(100, self._apply_dashboard_layout)

    def _apply_dashboard_layout(self) -> None:
        self._dashboard_layout_after_id = None
        if not hasattr(self, "_home_canvas"):
            return

        profile = select_dashboard_layout(self.winfo_width(), self.winfo_height())
        if profile == self._dashboard_layout:
            self._reflow_dashboard_content()
            self._sync_home_scroll_region()
            return

        map_size_changed = profile.event_map_size != self._event_map_size
        self._dashboard_layout = profile
        self._event_map_size = profile.event_map_size
        self._compact_frame.configure(padding=profile.panel_padding)

        gap = max(12, profile.section_gap)
        self._home_stats_header.pack_configure(pady=(0, gap))
        self._home_stats_row.pack_configure(pady=(0, gap))
        self._dashboard_hero_panel.configure(padx=profile.panel_padding, pady=profile.panel_padding)
        self._dashboard_hero_panel.pack_configure(pady=(0, gap))
        self._dashboard_hero_title_label.configure(
            font=("Segoe UI Semibold", max(22, profile.dashboard_hero_title_font))
        )
        self._dashboard_hero_subtitle_label.configure(
            font=("Segoe UI", 10)
        )
        self._dashboard_card_grid.pack_configure(pady=(0, gap))
        self._event_progress_header.pack_configure(pady=(0, gap))
        self._recommendation_panel.configure(padx=profile.panel_padding, pady=profile.panel_padding)
        self._recommendation_panel.pack_configure(pady=(gap, 0))
        self._recommendation_header.pack_configure(pady=(0, gap))

        for cards in (self._home_stat_cards, self._dashboard_cards, self._recommendation_cards):
            for index, card in enumerate(cards):
                card.configure(padx=profile.panel_padding, pady=profile.panel_padding)
                card.grid_configure(padx=(0 if index == 0 else gap, 0))

        for widgets in self._dashboard_card_widgets:
            widgets["title"].configure(font=("Segoe UI", 10))
            widgets["value"].configure(font=("Segoe UI Semibold", 15))
            widgets["detail"].configure(font=("Segoe UI", 9))

        for widgets in self._home_stat_widgets:
            widgets["title"].configure(font=("Segoe UI", 10))
            widgets["value"].configure(font=("Segoe UI Semibold", 26))

        self._recommendation_widgets[0]["header_meta"].configure(
            wraplength=profile.recommendation_header_wraplength
        )
        for widgets in self._recommendation_widgets:
            for key in ("name", "details", "reason"):
                widgets[key].configure(wraplength=profile.recommendation_card_wraplength)

        for index, widgets in enumerate(self._event_slot_widgets):
            frame = widgets["frame"]
            frame.configure(padx=profile.event_row_padding[0], pady=profile.event_row_padding[1])
            frame.grid_configure(pady=(0 if index == 0 else profile.event_row_gap, 0))
            for column, minimum in enumerate(profile.column_mins):
                frame.columnconfigure(column, minsize=minimum)
            frame.columnconfigure(2, weight=4)
            frame.columnconfigure(5, weight=2)
            widgets["number"].configure(font=("Segoe UI Semibold", profile.event_number_font))
            widgets["status"].configure(font=("Segoe UI", profile.event_status_font))
            for key in ("circuit_label", "start_label", "finish_label", "car_label", "time_label"):
                widgets[key].configure(font=("Segoe UI", profile.field_title_font))
            for key in ("circuit", "start", "finish", "car", "time"):
                widgets[key].configure(font=("Segoe UI Semibold", profile.field_value_font))
            widgets["map_frame"].configure(width=profile.event_map_size[0], height=profile.event_map_size[1])

        if map_size_changed and hasattr(self, "_event_map_paths"):
            self._event_map_paths[:] = [None, None, None]
            self._refresh_event_progress()
        self._reflow_dashboard_content()
        self._sync_home_scroll_region()

    def _reflow_dashboard_content(self) -> None:
        width = max(300, self._home_canvas.winfo_width() - 32)
        columns = 6 if width >= 1300 else 3
        for column in range(6):
            self._dashboard_card_grid.columnconfigure(column, weight=1 if column < columns else 0, minsize=0)
        for index, (card, widgets) in enumerate(zip(self._dashboard_cards, self._dashboard_card_widgets)):
            card.grid_configure(row=index // columns, column=index % columns,
                                padx=(0 if index % columns == 0 else 10, 0), pady=(0, 10))
            for widget in widgets.values():
                widget.configure(wraplength=max(100, width // columns - 42), justify="left")
        self._dashboard_hero_subtitle_label.configure(wraplength=max(220, width - 80), justify="left")
        recommendation_columns = 1 if width < 720 else 3
        for index, (card, widgets) in enumerate(zip(self._recommendation_cards, self._recommendation_widgets)):
            card.grid_configure(row=index // recommendation_columns, column=index % recommendation_columns,
                                padx=(0 if index % recommendation_columns == 0 else 10, 0), pady=(0, 8))
            for key in ("name", "details", "reason"):
                widgets[key].configure(wraplength=max(140, width // recommendation_columns - 50))
        for column in range(3):
            self._recommendation_cards[0].master.columnconfigure(column, weight=1 if column < recommendation_columns else 0, minsize=0)
        for widgets in self._event_slot_widgets:
            show_map = width >= 1050
            if show_map:
                widgets["map_frame"].grid()
            else:
                widgets["map_frame"].grid_remove()
            widths = (42, self._event_map_size[0] + 14 if show_map else 0, 130, 45, 45, 105, 110)
            for column, minimum in enumerate(widths):
                widgets["frame"].columnconfigure(column, minsize=minimum)
            for key in ("circuit", "car"):
                widgets[key].configure(wraplength=max(110, (width - 310 - widths[1]) // 2), justify="left")

    def _sync_home_scroll_region(self, event: tk.Event[tk.Misc] | None = None) -> None:
        del event
        canvas_width = self._home_canvas.winfo_width()
        if canvas_width <= 1:
            return
        self._home_canvas.itemconfigure(self._home_canvas_window, width=canvas_width)
        self._home_canvas.update_idletasks()
        bbox = self._home_canvas.bbox("all")
        if bbox is None:
            return
        content_height = max(self._compact_frame.winfo_reqheight(), bbox[3] - bbox[1])
        self._home_canvas.configure(scrollregion=(0, 0, canvas_width, content_height))
        should_scroll = content_height > self._home_canvas.winfo_height()
        if should_scroll:
            if not self._home_scrollbar.winfo_manager():
                self._home_scrollbar.pack(side="right", fill="y")
            return
        if self._home_scrollbar.winfo_manager():
            self._home_scrollbar.pack_forget()
        self._home_canvas.yview_moveto(0)

    def _scroll_home_on_mousewheel(self, event: tk.Event[tk.Misc]) -> str | None:
        if self._active_page != "home":
            return None
        widget = self.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            if widget in (self._home_canvas, self._compact_frame):
                units = -1 if event.delta > 0 else 1
                self._home_canvas.yview_scroll(units, "units")
                return "break"
            widget = widget.master
        return None

    def _build_sidebar(self, parent: tk.Frame) -> None:
        self._sidebar = tk.Frame(parent, bg=COLORS["sidebar"], width=212, padx=12, pady=20,
                                highlightthickness=1, highlightbackground=COLORS["line_soft"])
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)
        self._sidebar_expanded = True
        self._sidebar_prefer_expanded = True
        self._sidebar_collapse_job = None
        self._sidebar_logo = tk.Label(self._sidebar, text="FHSTATS", bg=COLORS["sidebar"],
                                     fg=COLORS["accent"], font=("Segoe UI Black", 19), anchor="w")
        self._sidebar_logo.pack(fill="x", padx=10, pady=(4, 20))
        self._sidebar_toggle = tk.Button(self._sidebar, text=tr("desktop.nav_toggle"), command=self._toggle_sidebar,
            bg=COLORS["sidebar"], fg=COLORS["muted"], activebackground=COLORS["surface"],
            activeforeground=COLORS["text"], bd=0, highlightthickness=1,
            highlightbackground=COLORS["sidebar"], highlightcolor=COLORS["accent"],
            anchor="w", padx=12, pady=6, font=("Segoe UI Symbol", 12), cursor="hand2")
        self._sidebar_toggle.pack(fill="x", pady=(0, 14))
        self._nav_buttons, self._nav_icons, self._nav_labels = {}, {}, {}
        for page, label_key, icon in visible_page_specs():
            button = tk.Button(self._sidebar, text=f"{icon}   {tr(label_key)}",
                command=lambda value=page: self._show_page(value), anchor="w",
                bg=COLORS["sidebar"], fg=COLORS["muted"], activebackground=COLORS["surface"],
                activeforeground=COLORS["text"], bd=0, padx=12, pady=11,
                highlightthickness=1, highlightbackground=COLORS["sidebar"],
                highlightcolor=COLORS["accent"], font=("Segoe UI", 11), cursor="hand2")
            button.pack(fill="x", pady=3)
            self._nav_buttons[page], self._nav_icons[page], self._nav_labels[page] = button, icon, tr(label_key)
        self._sidebar_recording = tk.Label(self._sidebar, textvariable=self._recording_state,
            bg=COLORS["sidebar"], fg=COLORS["muted"], wraplength=175, justify="left", font=("Segoe UI", 9))
        self._sidebar_recording.pack(side="bottom", anchor="w", padx=10, pady=(10, 0))
        self._sidebar_version = tk.Label(self._sidebar, text=tr("desktop.build", version=APP_VERSION),
            bg=COLORS["sidebar"], fg=COLORS["subtle"], font=("Segoe UI Semibold", 9))
        self._sidebar_version.pack(side="bottom", anchor="w", padx=10)
        self.bind("<Configure>", self._on_shell_resize, add="+")


    def _toggle_sidebar(self) -> None:
        self._sidebar_prefer_expanded = not self._sidebar_expanded
        self._set_sidebar_expanded(self._sidebar_prefer_expanded)

    def _on_shell_resize(self, event) -> None:
        if event.widget is self:
            self._set_sidebar_expanded(self._sidebar_prefer_expanded and event.width >= 1100 and event.height >= 700)

    def _set_sidebar_expanded(self, expanded: bool) -> None:
        if expanded == self._sidebar_expanded:
            return
        self._sidebar_expanded = expanded
        self._sidebar.configure(width=212 if expanded else 72, padx=12 if expanded else 8, pady=20 if expanded else 8)
        self._sidebar_logo.configure(text="FHSTATS" if expanded else "F", anchor="w" if expanded else "center")
        self._sidebar_logo.pack_configure(padx=10 if expanded else 0, pady=(4, 20) if expanded else (0, 8))
        self._sidebar_toggle.pack_configure(pady=(0, 14) if expanded else (0, 6))
        self._sidebar_toggle.configure(text=tr("desktop.nav_toggle") if expanded else tr("desktop.nav_icon"))
        for page, button in self._nav_buttons.items():
            button.configure(text=f"{self._nav_icons[page]}   {self._nav_labels[page]}" if expanded else self._nav_icons[page],
                             anchor="w" if expanded else "center", padx=12 if expanded else 2,
                             pady=11 if expanded else 2, font=("Segoe UI", 11 if expanded else 10))
            button.pack_configure(pady=3 if expanded else 0)
        self._sidebar_recording.configure(textvariable=self._recording_state if expanded else "", text="●")
        self._sidebar_version.configure(text=tr("desktop.build", version=APP_VERSION) if expanded else APP_VERSION)

    def _show_page(self, name: str) -> None:
        name = normalize_page_id(name)
        if name not in self._pages or (name == "garage" and name not in self._nav_buttons):
            name = "home"
        previous_page = self._active_page
        self._active_page = name
        for page in self._pages.values():
            page.pack_forget()
        self._pages[name].pack(fill="both", expand=True)
        for page_name, button in self._nav_buttons.items():
            active = page_name == name
            button.configure(
                bg=COLORS["current"] if active else COLORS["sidebar"],
                highlightbackground=COLORS["line"] if active else COLORS["sidebar"],
                fg=COLORS["accent"] if active else COLORS["muted"],
            )
        if previous_page == "videos" and name != "videos":
            self._pause_video()
            self._hide_video_overlay_for_inactive_page()
        if name == "videos":
            self._refresh_videos()
            self.after(50, self._update_video_play_button)
        elif name == "garage":
            self._refresh_garage_cards()

    def _build_menu(self) -> None:
        menu_bar = tk.Menu(self)
        help_menu = tk.Menu(menu_bar, tearoff=False)
        settings_menu = tk.Menu(help_menu, tearoff=False)
        settings_menu.add_command(label="Cambiar gamertag...", command=self._open_gamertag_settings)
        settings_menu.add_command(label="Grabación de partidas...", command=self._open_recording_settings)
        help_menu.add_cascade(label="Ajustes", menu=settings_menu)
        help_menu.add_separator()
        help_menu.add_command(label="Privacidad y envío de datos...", command=self._open_privacy_settings)
        help_menu.add_command(
            label="Política de privacidad",
            command=lambda: webbrowser.open("https://fhstats.com/es/legal"),
        )
        menu_bar.add_cascade(label="Ayuda", menu=help_menu)
        self.configure(menu=menu_bar)

    def _open_gamertag_settings(self, first_run: bool = False) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Configurar gamertag" if first_run else "Cambiar gamertag")
        dialog.geometry("500x280")
        dialog.resizable(False, False)
        dialog.configure(bg=COLORS["bg"])
        dialog.transient(self)
        dialog.grab_set()

        content = ttk.Frame(dialog, padding=22)
        content.pack(fill="both", expand=True)
        title = "Configura tu gamertag" if first_run else "Cambiar gamertag"
        ttk.Label(content, text=title, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            content,
            text=(
                "Se guardará en este ordenador y no volverá a cambiar automáticamente. "
                "Después solo podrás modificarlo desde Ayuda > Ajustes."
            ),
            style="Muted.TLabel",
            wraplength=450,
            justify="left",
        ).pack(anchor="w", pady=(8, 16))

        current = self._player_name.get().strip()
        player_value = tk.StringVar(value=current if current.lower() not in PLAYER_PLACEHOLDERS else "")
        player_entry = ttk.Entry(content, textvariable=player_value, width=32)
        player_entry.pack(anchor="w")
        player_entry.focus_set()
        detection_status = LocalizedStringVar(value="")
        ttk.Label(content, textvariable=detection_status, style="Muted.TLabel").pack(anchor="w", pady=(7, 0))

        actions = ttk.Frame(content)
        actions.pack(fill="x", side="bottom", pady=(18, 0))
        if not first_run:
            ttk.Button(actions, text="Cancelar", command=dialog.destroy).pack(side="right")
        ttk.Button(
            actions,
            text="Guardar gamertag",
            style="Accent.TButton",
            command=lambda: self._save_gamertag_from_settings(player_value.get(), detection_status, dialog),
        ).pack(side="right", padx=(0, 8))
        ttk.Button(
            actions,
            text="Detectar",
            command=lambda: self._detect_gamertag(player_value, detection_status),
        ).pack(side="left")

        if first_run:
            dialog.protocol("WM_DELETE_WINDOW", lambda: self._capture_status.set("Configura tu gamertag para iniciar"))

    def _save_gamertag_from_settings(
        self,
        player_name: str,
        status: tk.StringVar,
        dialog: tk.Toplevel,
    ) -> None:
        cleaned = player_name.strip()
        if cleaned.lower() in PLAYER_PLACEHOLDERS:
            status.set("Escribe o detecta un gamertag válido.")
            return
        self._player_name.set(cleaned)
        self._save_player_profile(show_status=False)
        self._player_display.set(self._player_display_text())
        self._capture_status.set("Gamertag guardado")
        dialog.destroy()

    def _detect_gamertag(self, target: tk.StringVar, status: tk.StringVar) -> None:
        detected = self._read_player_name_from_game()
        if detected:
            target.set(detected)
            status.set("Gamertag detectado. Pulsa Guardar gamertag.")
        else:
            status.set("No pude detectar el gamertag. Puedes escribirlo manualmente.")

    def _open_privacy_settings(self) -> None:
        settings = load_settings(self._config_path)
        dialog = tk.Toplevel(self)
        dialog.title("Privacidad y envío de datos")
        dialog.geometry("520x360")
        dialog.resizable(False, False)
        dialog.configure(bg=COLORS["bg"])
        dialog.transient(self)
        dialog.grab_set()

        content = ttk.Frame(dialog, padding=22)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="Privacidad y envío de datos", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            content,
            text=(
                "El Tracker guarda el historial en este ordenador. Puedes decidir por "
                "separado si también envía resultados y diagnósticos a FHStats."
            ),
            style="Muted.TLabel",
            wraplength=465,
            justify="left",
        ).pack(anchor="w", pady=(8, 18))

        results_enabled = tk.BooleanVar(value=settings.api.enabled)
        diagnostics_enabled = tk.BooleanVar(value=settings.api.diagnostics_enabled)
        ttk.Checkbutton(
            content,
            text="Enviar resultados procesados de carreras a FHStats",
            variable=results_enabled,
        ).pack(anchor="w", pady=(0, 8))
        ttk.Checkbutton(
            content,
            text="Enviar diagnósticos sanitizados cuando ocurre un error",
            variable=diagnostics_enabled,
        ).pack(anchor="w", pady=(0, 12))
        ttk.Label(
            content,
            text=(
                "Desactivar estas opciones no borra tu historial local ni detiene la "
                "captura. Nunca se envían capturas de pantalla desde estos controles."
            ),
            style="Muted.TLabel",
            wraplength=465,
            justify="left",
        ).pack(anchor="w")

        actions = ttk.Frame(content)
        actions.pack(fill="x", side="bottom", pady=(20, 0))
        ttk.Button(actions, text="Cancelar", command=dialog.destroy).pack(side="right")
        ttk.Button(
            actions,
            text="Guardar preferencias",
            style="Accent.TButton",
            command=lambda: self._save_privacy_settings(
                results_enabled.get(),
                diagnostics_enabled.get(),
                dialog,
            ),
        ).pack(side="right", padx=(0, 8))

    def _save_privacy_settings(
        self,
        results_enabled: bool,
        diagnostics_enabled: bool,
        dialog: tk.Toplevel,
    ) -> None:
        data = self._load_editable_config()
        api = data.setdefault("api", {})
        if not isinstance(api, dict):
            api = {}
            data["api"] = api
        api["enabled"] = bool(results_enabled)
        api["diagnostics_enabled"] = bool(diagnostics_enabled)
        api.setdefault("base_url", "https://fhstats.com")
        self._write_local_config(data)
        self._settings = load_settings(self._config_path)
        dialog.destroy()
        self._capture_status.set(
            tr("privacy.saved_restart") if self._capture_process is not None else tr("privacy.saved")
        )

    def _open_recording_settings(self) -> None:
        settings = load_settings(self._config_path)
        dialog = tk.Toplevel(self)
        dialog.title("Grabación de partidas")
        dialog.geometry("560x430")
        dialog.resizable(False, False)
        dialog.configure(bg=COLORS["bg"])
        dialog.transient(self)
        dialog.grab_set()

        content = ttk.Frame(dialog, padding=22)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="Grabación de partidas", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            content,
            text=(
                "Crea un vídeo por carrera: desde la parrilla de salida hasta su "
                "pantalla de resultados. El vídeo se guarda a 1080p y usa el "
                "codificador de NVIDIA o AMD cuando está disponible."
            ),
            style="Muted.TLabel",
            wraplength=505,
            justify="left",
        ).pack(anchor="w", pady=(8, 18))

        enabled = tk.BooleanVar(value=settings.recording.enabled)
        capture_audio = tk.BooleanVar(value=settings.recording.capture_audio)
        max_storage = tk.StringVar(value=f"{settings.recording.max_storage_gb:g}")
        ttk.Checkbutton(
            content,
            text="Grabar automáticamente las partidas online",
            variable=enabled,
        ).pack(anchor="w", pady=(0, 8))
        ttk.Checkbutton(
            content,
            text="Incluir únicamente el audio del juego",
            variable=capture_audio,
        ).pack(anchor="w", pady=(0, 16))

        limit_row = ttk.Frame(content)
        limit_row.pack(fill="x")
        ttk.Label(limit_row, text="Espacio máximo para vídeos (GB):").pack(side="left")
        ttk.Entry(limit_row, textvariable=max_storage, width=9).pack(side="left", padx=(12, 0))
        ttk.Label(
            content,
            text=(
                "Al superar el límite se eliminan primero las grabaciones más antiguas. "
                "Los vídeos permanecen en este ordenador y nunca se suben a FHStats."
            ),
            style="Muted.TLabel",
            wraplength=505,
            justify="left",
        ).pack(anchor="w", pady=(14, 0))

        feedback = LocalizedStringVar(value="")
        ttk.Label(content, textvariable=feedback, style="Muted.TLabel").pack(anchor="w", pady=(12, 0))
        actions = ttk.Frame(content)
        actions.pack(fill="x", side="bottom")
        ttk.Button(actions, text="Cancelar", command=dialog.destroy).pack(side="right")
        ttk.Button(
            actions,
            text="Guardar",
            style="Accent.TButton",
            command=lambda: self._save_recording_settings(
                enabled.get(),
                capture_audio.get(),
                max_storage.get(),
                feedback,
                dialog,
            ),
        ).pack(side="right", padx=(0, 8))

    def _save_recording_settings(
        self,
        enabled: bool,
        capture_audio: bool,
        max_storage_text: str,
        feedback: tk.StringVar,
        dialog: tk.Toplevel,
    ) -> None:
        try:
            max_storage_gb = float(max_storage_text.replace(",", "."))
        except ValueError:
            feedback.set("Introduce un límite válido, por ejemplo 20.")
            return
        if max_storage_gb < 1 or max_storage_gb > 1000:
            feedback.set("El límite debe estar entre 1 y 1000 GB.")
            return
        data = self._load_editable_config()
        recording = data.setdefault("recording", {})
        if not isinstance(recording, dict):
            recording = {}
            data["recording"] = recording
        recording["enabled"] = bool(enabled)
        recording["capture_audio"] = bool(capture_audio)
        recording["max_storage_gb"] = max_storage_gb
        recording.setdefault("output_dir", self._recording_output_dir_display(self._settings.recording.output_dir))
        recording.setdefault("framerate", 60)
        recording.setdefault("width", 1920)
        recording.setdefault("height", 1080)
        recording.setdefault("quality", 20)
        recording.setdefault("keyframe_interval_seconds", 2)
        recording.setdefault("ffmpeg_path", "runtime/ffmpeg/bin/ffmpeg.exe")
        recording.setdefault("audio_helper_path", "runtime/audio/FHStatsAudioCapture.exe")
        self._write_local_config(data)
        self._settings = load_settings(self._config_path)
        dialog.destroy()
        self._capture_status.set(
            tr("recording.settings_saved_next_event")
            if self._capture_process is not None
            else tr("recording.settings_saved")
        )

    def _build_compact_panel(self, parent: ttk.Frame) -> None:
        self._compact_values: dict[str, tk.StringVar] = {
            "event": LocalizedStringVar(value="Sin detecciones"),
            "circuit": tk.StringVar(value="-"),
            "start": tk.StringVar(value="-"),
            "finish": tk.StringVar(value="-"),
            "car": tk.StringVar(value="-"),
            "time": tk.StringVar(value="-"),
        }
        summary_parent = self._race_summary_host
        self._dashboard_hero_panel = tk.Frame(summary_parent, bg=COLORS["panel"], highlightthickness=1, highlightbackground=COLORS["line_soft"], padx=16, pady=14)
        hero = self._dashboard_hero_panel
        hero.pack(fill="x", pady=(0, 18), before=self._home_stats_row)
        tk.Frame(hero, bg=COLORS["accent"], width=3).pack(side="left", fill="y", padx=(0, 16))
        hero_copy = tk.Frame(hero, bg=COLORS["panel"])
        hero_copy.pack(side="left", fill="x", expand=True)
        tk.Label(
            hero_copy,
            text=tr("desktop.session"),
            bg=COLORS["panel"],
            fg=COLORS["accent2"],
            font=("Segoe UI Semibold", 8),
            anchor="w",
        ).pack(fill="x")
        self._dashboard_hero_title = LocalizedStringVar(value="Sin carreras guardadas")
        self._dashboard_hero_subtitle = LocalizedStringVar(value="Activa la captura y termina una carrera para llenar el command center.")
        self._dashboard_hero_title_label = tk.Label(
            hero_copy,
            textvariable=self._dashboard_hero_title,
            bg=COLORS["panel"],
            fg=COLORS["text"],
            font=("Segoe UI Semibold", 19),
            anchor="w",
        )
        self._dashboard_hero_title_label.pack(fill="x", pady=(2, 0))
        self._dashboard_hero_subtitle_label = tk.Label(
            hero_copy,
            textvariable=self._dashboard_hero_subtitle,
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=("Segoe UI", 9),
            anchor="w",
        )
        self._dashboard_hero_subtitle_label.pack(fill="x", pady=(4, 0))

        self._dashboard_card_values: dict[str, dict[str, tk.StringVar]] = {}
        self._dashboard_card_grid = tk.Frame(summary_parent, bg=COLORS["bg"])
        card_grid = self._dashboard_card_grid
        card_grid.pack(fill="x", pady=(0, 12))
        self._dashboard_cards: list[tk.Frame] = []
        self._dashboard_card_widgets: list[dict[str, tk.Label]] = []
        for index, key in enumerate(["last_race", "last_car", "circuit", "car_class", "tracker", "telemetry"]):
            card = tk.Frame(
                card_grid,
                bg=COLORS["surface"],
                highlightthickness=1,
                highlightbackground=COLORS["line_soft"],
                padx=12,
                pady=10,
            )
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 8, 0))
            self._dashboard_cards.append(card)
            label = LocalizedStringVar(value="-")
            value = LocalizedStringVar(value="-")
            detail = LocalizedStringVar(value="-")
            title_label = tk.Label(card, textvariable=label, bg=COLORS["surface"], fg=COLORS["muted"], font=("Segoe UI", 8), anchor="w")
            title_label.pack(fill="x")
            value_label = tk.Label(card, textvariable=value, bg=COLORS["surface"], fg=COLORS["accent"], font=("Segoe UI Semibold", 14), anchor="w")
            value_label.pack(fill="x", pady=(2, 0))
            detail_label = tk.Label(card, textvariable=detail, bg=COLORS["surface"], fg=COLORS["subtle"], font=("Segoe UI", 8), anchor="w")
            detail_label.pack(fill="x", pady=(2, 0))
            self._dashboard_card_widgets.append(
                {"title": title_label, "value": value_label, "detail": detail_label}
            )
            self._dashboard_card_values[key] = {
                "label": label,
                "value": value,
                "detail": detail,
                "value_label": value_label,
            }
            card_grid.columnconfigure(index, weight=1)
        grid = ttk.Frame(summary_parent, style="Panel.TFrame")
        grid.pack(fill="x")
        for index, (label, key) in enumerate([
            ("Último", "event"),
            ("Circuito", "circuit"),
            ("Salida", "start"),
            ("Llegada", "finish"),
            ("Coche", "car"),
            ("Tiempo", "time"),
        ]):
            card = ttk.Frame(grid, style="Panel.TFrame", padding=(10, 6))
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 8, 0))
            ttk.Label(card, text=label, style="CardTitle.TLabel").pack(anchor="w")
            ttk.Label(card, textvariable=self._compact_values[key], style="CardValue.TLabel").pack(anchor="w")
            grid.columnconfigure(index, weight=1)
        grid.pack_forget()

        self._build_event_progress(parent)

    def _build_home_stats(self, parent: ttk.Frame) -> None:
        self._summary_vars = {
            "races": tk.StringVar(value="0"),
            "wins": tk.StringVar(value="0"),
            "podiums": tk.StringVar(value="0"),
            "avg": tk.StringVar(value="-"),
        }
        self._home_stats_header = self._build_section_header(
            parent,
            "Panel de carrera",
            "Sigue la captura, el progreso del evento online y las recomendaciones sin salir de esta pantalla.",
            background=COLORS["bg"],
            pady=(0, 18),
        )
        self._home_stats_row = ttk.Frame(parent)
        row = self._home_stats_row
        row.pack(fill="x", pady=(0, 12))
        self._home_stat_cards: list[tk.Frame] = []
        self._home_stat_widgets: list[dict[str, tk.Label]] = []
        for index, (label, key) in enumerate([
            ("Carreras", "races"),
            ("Victorias", "wins"),
            ("Podios", "podiums"),
            ("Posición media", "avg"),
        ]):
            card = tk.Frame(
                row,
                bg=COLORS["surface"],
                padx=14,
                pady=10,
                highlightthickness=1,
                highlightbackground=COLORS["line_soft"],
            )
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 8, 0))
            self._home_stat_cards.append(card)
            title_label = tk.Label(card, text=label, bg=COLORS["surface"], fg=COLORS["muted"], font=("Segoe UI", 8))
            title_label.pack(anchor="w")
            value_label = tk.Label(card, textvariable=self._summary_vars[key], bg=COLORS["surface"], fg=COLORS["text"], font=("Segoe UI Semibold", 17))
            value_label.pack(anchor="w", pady=(2, 0))
            self._home_stat_widgets.append({"title": title_label, "value": value_label})
            row.columnconfigure(index, weight=1)

    def _build_event_progress(self, parent: ttk.Frame) -> None:
        self._event_context_host = tk.Frame(parent, bg=COLORS["panel"])
        self._event_context_host.pack(fill="both", expand=True)
        self._rivals_live = RivalsLivePanel(self._event_context_host, self._rivals_runtime,
                                          lambda: self._show_page("rivals"))
        self._event_progress_wrapper = tk.Frame(self._event_context_host, bg=COLORS["panel"])
        wrapper = self._event_progress_wrapper
        wrapper.pack(fill="both", expand=True)
        self._event_progress_header = tk.Frame(wrapper, bg=COLORS["panel"])
        header = self._event_progress_header
        header.pack(fill="x", pady=(0, 8))
        tk.Label(header, text="Evento online", bg=COLORS["panel"], fg=COLORS["text"], font=("Segoe UI Semibold", 12)).pack(side="left")
        tk.Label(header, text="Se completa con inscripción, parrilla y resultado.", bg=COLORS["panel"], fg=COLORS["muted"], font=("Segoe UI", 9)).pack(side="left", padx=(12, 0))

        bars = tk.Frame(wrapper, bg=COLORS["panel"])
        bars.pack(fill="x")
        self._event_slot_widgets: list[dict[str, tk.Widget]] = []
        self._event_map_paths: list[Path | None] = [None, None, None]
        self._event_map_photos: list[ImageTk.PhotoImage | None] = [None, None, None]
        initial_profile = select_dashboard_layout(1280, 720)
        for index in range(3):
            frame = tk.Frame(bars, bg=COLORS["pending"], highlightthickness=1, highlightbackground=COLORS["line"], padx=12, pady=8)
            frame.grid(row=index, column=0, sticky="ew", pady=(0 if index == 0 else 6, 0))
            for column, minimum in enumerate(initial_profile.column_mins):
                frame.columnconfigure(column, minsize=minimum)
            frame.columnconfigure(2, weight=4)
            frame.columnconfigure(5, weight=2)

            number = tk.Label(frame, text=f"{index + 1}/3", bg=COLORS["pending"], fg=COLORS["accent"], font=("Segoe UI Semibold", 14), width=5, anchor="w")
            number.grid(row=0, column=0, sticky="ew", padx=(0, 10))
            status = tk.Label(frame, text="-", bg=COLORS["pending"], fg=COLORS["muted"], font=("Segoe UI", 8), anchor="w")
            status.grid(row=1, column=0, sticky="ew", padx=(0, 10))

            fields: dict[str, tk.Widget] = {"frame": frame, "number": number, "status": status}
            map_frame = tk.Frame(frame, bg=COLORS["surface2"], width=self._event_map_size[0], height=self._event_map_size[1])
            map_frame.grid(row=0, column=1, rowspan=2, sticky="w", padx=(0, 14))
            map_frame.grid_propagate(False)
            map_image = tk.Label(
                map_frame,
                text="",
                bg=COLORS["surface2"],
                fg=COLORS["muted"],
                font=("Segoe UI Semibold", 7),
            )
            map_image.pack(fill="both", expand=True)
            fields["map_frame"] = map_frame
            fields["map_image"] = map_image
            for column, (key, label) in enumerate([
                ("circuit", "Circuito"),
                ("start", "Salida"),
                ("finish", "Llegada"),
                ("car", "Coche"),
                ("time", "Total · vuelta"),
            ], start=2):
                field_anchor = "e" if key == "time" else "w"
                field_padding = (8, 0) if key == "time" else (0, 10)
                title = tk.Label(frame, text=label, bg=COLORS["pending"], fg=COLORS["muted"], font=("Segoe UI", 9), anchor=field_anchor)
                title.grid(row=0, column=column, sticky="ew", padx=field_padding)
                value = tk.Label(frame, text="-", bg=COLORS["pending"], fg=COLORS["text"], font=("Segoe UI Semibold", 11), anchor=field_anchor)
                value.grid(row=1, column=column, sticky="ew", padx=field_padding)
                fields[f"{key}_label"] = title
                fields[key] = value

            bars.rowconfigure(index, weight=0)
            bars.columnconfigure(0, weight=1)
            self._event_slot_widgets.append(fields)

        self._build_recommendation_panel(wrapper)

    def _build_track_map_panel(self, parent: tk.Frame) -> None:
        panel = tk.Frame(
            parent,
            bg=COLORS["panel2"],
            highlightthickness=1,
            highlightbackground=COLORS["line_soft"],
            padx=12,
            pady=10,
        )
        panel.pack(fill="x", pady=(12, 0))
        self._track_map_title = LocalizedStringVar(value="Mapa del circuito")
        self._track_map_meta = LocalizedStringVar(value="Esperando circuito detectado por OCR o telemetria.")
        self._track_map_photo: ImageTk.PhotoImage | None = None

        image_frame = tk.Frame(panel, bg=COLORS["surface2"], width=220, height=138)
        image_frame.pack(side="left")
        image_frame.pack_propagate(False)
        self._track_map_image_label = tk.Label(
            image_frame,
            text="",
            bg=COLORS["surface2"],
            fg=COLORS["muted"],
            font=("Segoe UI Semibold", 9),
        )
        self._track_map_image_label.pack(fill="both", expand=True)

        copy = tk.Frame(panel, bg=COLORS["panel2"])
        copy.pack(side="left", fill="x", expand=True, padx=(14, 0))
        tk.Label(
            copy,
            text="MAPA DETECTADO",
            bg=COLORS["panel2"],
            fg=COLORS["accent2"],
            font=("Segoe UI Semibold", 8),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            copy,
            textvariable=self._track_map_title,
            bg=COLORS["panel2"],
            fg=COLORS["text"],
            font=("Segoe UI Semibold", 13),
            anchor="w",
        ).pack(fill="x", pady=(2, 0))
        tk.Label(
            copy,
            textvariable=self._track_map_meta,
            bg=COLORS["panel2"],
            fg=COLORS["muted"],
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
            wraplength=540,
        ).pack(fill="x", pady=(4, 0))

    def _build_recommendation_panel(self, parent: tk.Frame) -> None:
        self._recommendation_panel = tk.Frame(
            parent,
            bg=COLORS["panel2"],
            highlightthickness=1,
            highlightbackground=COLORS["line"],
            padx=12,
            pady=10,
        )
        panel = self._recommendation_panel
        panel.pack(fill="x", pady=(12, 0))

        self._recommendation_header = tk.Frame(panel, bg=COLORS["panel2"])
        header = self._recommendation_header
        header.pack(fill="x", pady=(0, 8))
        self._recommendation_title = LocalizedStringVar(value="Recomendador de coches")
        self._recommendation_meta = LocalizedStringVar(value="Esperando una inscripción con tres carreras para proponer el coche adecuado.")
        tk.Label(
            header,
            textvariable=self._recommendation_title,
            bg=COLORS["panel2"],
            fg=COLORS["accent"],
            font=("Segoe UI Semibold", 12),
        ).pack(side="left")
        header_meta = tk.Label(
            header,
            textvariable=self._recommendation_meta,
            bg=COLORS["panel2"],
            fg=COLORS["muted"],
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
            wraplength=select_dashboard_layout(1280, 720).recommendation_header_wraplength,
        )
        header_meta.pack(side="left", fill="x", expand=True, padx=(12, 0))

        cards = tk.Frame(panel, bg=COLORS["panel2"])
        cards.pack(fill="x")
        self._recommendation_widgets: list[dict[str, tk.Widget]] = []
        self._recommendation_cards: list[tk.Frame] = []
        initial_profile = select_dashboard_layout(1280, 720)
        for index in range(3):
            background = COLORS["current"] if index == 0 else COLORS["surface2"]
            card = tk.Frame(
                cards,
                bg=background,
                highlightthickness=1,
                highlightbackground=COLORS["accent"] if index == 0 else COLORS["line"],
                padx=10,
                pady=8,
            )
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 8, 0))
            self._recommendation_cards.append(card)
            cards.columnconfigure(index, weight=1)
            rank = tk.Label(
                card,
                text="PRINCIPAL" if index == 0 else f"ALTERNATIVA {index}",
                bg=background,
                fg=COLORS["accent"] if index == 0 else COLORS["accent2"],
                font=("Segoe UI Semibold", 8),
                anchor="w",
            )
            rank.pack(fill="x")
            name = tk.Label(
                card,
                text="-",
                bg=background,
                fg=COLORS["text"],
                font=("Segoe UI Semibold", 11),
                anchor="w",
                justify="left",
                wraplength=initial_profile.recommendation_card_wraplength,
            )
            name.pack(fill="x", pady=(2, 0))
            details = tk.Label(
                card,
                text="",
                bg=background,
                fg=COLORS["muted"],
                font=("Segoe UI", 8),
                anchor="w",
                justify="left",
                wraplength=initial_profile.recommendation_card_wraplength,
            )
            details.pack(fill="x", pady=(2, 0))
            reason = tk.Label(
                card,
                text="",
                bg=background,
                fg=COLORS["muted"],
                font=("Segoe UI", 8),
                anchor="w",
                justify="left",
                wraplength=initial_profile.recommendation_card_wraplength,
            )
            reason.pack(fill="x", pady=(3, 0))
            widgets: dict[str, tk.Widget] = {
                "frame": card,
                "rank": rank,
                "name": name,
                "details": details,
                "reason": reason,
            }
            if index == 0:
                widgets["header_meta"] = header_meta
            self._recommendation_widgets.append(widgets)

    def _build_rivals_page(self, parent: tk.Frame) -> None:
        self._rivals_history = RivalsHistoryPanel(parent, self._rivals_runtime)
        self._rivals_history.pack(fill="both", expand=True)

    def _reload_rivals(self, *, reset_circuit: bool = False) -> None:
        self._rivals_history.render(self._rivals_runtime.snapshot())

    def _poll_rivals(self) -> None:
        if self._is_exiting:
            return
        profile = self._player_name.get()
        if profile != self._rivals_profile:
            self._rivals_profile = profile
            self._rivals_runtime.set_profile(profile)
        state = self._rivals_runtime.snapshot()
        capture_active = self._capture_process is not None and self._capture_process.poll() is None
        mode = self._event_context_gate.select(
            state.get("active"), read_overlay_event(), capture_active,
            datetime.now(timezone.utc), memory_status=state.get("status", ""),
        )
        if mode != self._event_display_mode:
            self._event_display_mode = mode
            if mode == "online":
                self._rivals_live.pack_forget()
                self._event_progress_wrapper.pack(fill="both", expand=True)
                self._refresh_event_progress()
                self._refresh_recommendation()
            else:
                self._event_progress_wrapper.pack_forget()
                self._rivals_live.pack(fill="x")
            self._sync_home_scroll_region()
        if mode != "online":
            self._rivals_live.render(state, mode)
        self._rivals_history.render(state)
        self.after(250, self._poll_rivals)

    def _build_tierlist_page(self, parent: tk.Frame) -> None:
        wrapper = ttk.Frame(parent, padding=12)
        wrapper.pack(fill="both", expand=True)
        self._build_fhstats_web_hub(
            wrapper,
            "Meta Cars",
            "Explora coches meta de FHStats con una experiencia de escritorio y acceso directo a la vista web.",
            FHSTATS_META_CARS_URL,
            "Abrir Meta Cars web",
            self._sync_metacars_catalog_async,
        )
        self._build_section_header(
            wrapper,
            "Tierlist",
            "Explora coches meta de FHStats por modalidad y clase, con tune y enlace a la ficha completa.",
        )
        self._tier_discipline = tk.StringVar(value="road")
        self._tier_class = tk.StringVar(value="R 998")
        self._tier_search = tk.StringVar(value="")
        self._tier_filter_buttons: list[tuple[tk.Button, tk.StringVar, str]] = []
        filters = tk.Frame(
            wrapper,
            bg=COLORS["panel2"],
            highlightthickness=1,
            highlightbackground=COLORS["line"],
            padx=12,
            pady=10,
        )
        filters.pack(fill="x", pady=(0, 12))
        self._build_tier_filter_row(
            filters,
            "MODALIDAD",
            self._tier_discipline,
            [
                ("Carretera", "road"),
                ("Tierra", "dirt"),
                ("Campo a través", "cross-country"),
            ],
        )
        self._build_tier_filter_row(
            filters,
            "CLASE",
            self._tier_class,
            [(value, value) for value in ("R 998", "S2 900", "S1 800", "B 600", "A 700", "C 500")],
        )
        search_row = tk.Frame(filters, bg=COLORS["panel2"])
        search_row.pack(fill="x", pady=(8, 0))
        tk.Label(
            search_row,
            text="BUSCAR",
            width=14,
            anchor="w",
            bg=COLORS["panel2"],
            fg=COLORS["muted"],
            font=("Segoe UI Semibold", 8),
        ).pack(side="left")
        search = tk.Entry(
            search_row,
            textvariable=self._tier_search,
            bg=COLORS["panel"],
            fg=COLORS["text"],
            insertbackground=COLORS["accent"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=COLORS["line"],
            highlightcolor=COLORS["accent2"],
            font=("Segoe UI", 10),
        )
        search.pack(side="left", fill="x", expand=True, ipady=7)
        search.bind("<KeyRelease>", lambda _event: self._refresh_tierlist_tree())
        gallery = tk.Frame(wrapper, bg=COLORS["bg"])
        gallery.pack(fill="both", expand=True)
        self._tierlist_canvas = tk.Canvas(gallery, bg=COLORS["bg"], highlightthickness=0)
        self._tierlist_cards = tk.Frame(self._tierlist_canvas, bg=COLORS["bg"])
        self._tierlist_window = self._tierlist_canvas.create_window((0, 0), window=self._tierlist_cards, anchor="nw")
        self._tierlist_canvas.pack(fill="both", expand=True)
        self._tierlist_cards.bind(
            "<Configure>",
            lambda _event: self._tierlist_canvas.configure(scrollregion=self._tierlist_canvas.bbox("all")),
        )
        self._tierlist_canvas.bind(
            "<Configure>",
            lambda event: self._tierlist_canvas.itemconfigure(self._tierlist_window, width=event.width),
        )
        self.bind_all("<MouseWheel>", self._scroll_tierlist, add="+")
        self._tierlist_rows = load_tierlist()
        self._refresh_tier_filter_buttons()
        self._refresh_tierlist_tree()

    def _build_fhstats_web_hub(
        self,
        parent: tk.Frame,
        title: str,
        subtitle: str,
        url: str,
        open_text: str,
        refresh_command: Any,
    ) -> None:
        hub = tk.Frame(
            parent,
            bg=COLORS["panel2"],
            highlightthickness=1,
            highlightbackground=COLORS["line_soft"],
            padx=16,
            pady=14,
        )
        hub.pack(fill="x", pady=(0, 12))
        copy = tk.Frame(hub, bg=COLORS["panel2"])
        copy.pack(side="left", fill="x", expand=True)
        tk.Label(
            copy,
            text="FHStats Live Hub",
            bg=COLORS["panel2"],
            fg=COLORS["accent2"],
            font=("Segoe UI Semibold", 8),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            copy,
            text=title,
            bg=COLORS["panel2"],
            fg=COLORS["text"],
            font=("Segoe UI Semibold", 16),
            anchor="w",
        ).pack(fill="x", pady=(2, 0))
        subtitle_label = tk.Label(
            copy,
            text=subtitle,
            bg=COLORS["panel2"],
            fg=COLORS["muted"],
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
            wraplength=660,
        )
        subtitle_label.pack(fill="x", pady=(4, 0))
        copy.bind("<Configure>", lambda event: subtitle_label.configure(wraplength=max(160, event.width)), add="+")
        actions = tk.Frame(hub, bg=COLORS["panel2"])
        actions.pack(side="right", padx=(16, 0))
        ttk.Button(
            actions,
            text=open_text,
            style="Quiet.TButton",
            command=lambda target=url: self._open_fhstats_url(target),
        ).pack(fill="x")
        ttk.Button(actions, text="Actualizar desde FHStats", command=refresh_command).pack(fill="x", pady=(8, 0))
        tk.Label(
            actions,
            textvariable=self._catalog_sync_status,
            bg=COLORS["panel2"],
            fg=COLORS["muted"],
            font=("Segoe UI", 8),
            anchor="e",
        ).pack(fill="x", pady=(8, 0))

    def _open_fhstats_url(self, url: str) -> None:
        webbrowser.open(url)

    def _reload_tierlist(self) -> None:
        self._tierlist_rows = load_tierlist()
        self._refresh_tierlist_tree()

    def _sync_rivals_catalog_async(self) -> None:
        event = self._rivals_runtime.snapshot().get("current", {}).get("event")
        self._rivals_runtime.refresh_snapshot(event["discipline"] if event else "dirt")

    def _sync_metacars_catalog_async(self) -> None:
        self._sync_catalog_async(
            [
                SyncAsset("metacars", "tierlist-cars.ts"),
                SyncAsset("metacars", "tierlist-image-attribution.json"),
            ],
            on_success=self._reload_tierlist,
        )

    def _sync_catalog_async(self, assets: list[SyncAsset], *, on_success: Any) -> None:
        if self._catalog_sync_running:
            return
        self._catalog_sync_running = True
        self._catalog_sync_status.set("Sincronizando FHStats...")
        threading.Thread(
            target=self._sync_catalog_worker,
            args=(assets, on_success),
            daemon=True,
        ).start()

    def _sync_catalog_worker(self, assets: list[SyncAsset], on_success: Any) -> None:
        try:
            result = FhstatsCatalogClient().sync_assets(assets)
            if result.source == "remote" and any(asset.group == "metacars" for asset in assets):
                from dataclasses import replace
                from forza_tracker.metacars_images import sync_images
                failed = sync_images(load_tierlist(cache_root=result.cache_dir), result.cache_dir)
                if failed:
                    result = replace(result, error=tr("metacars.images_partial", count=len(failed)))
        except Exception as error:
            self.after(0, lambda value=str(error): self._finish_catalog_sync(False, value, None))
            return
        self.after(0, lambda value=result: self._finish_catalog_sync(True, "", (value, on_success)))

    def _finish_catalog_sync(self, ok: bool, message: str, payload: Any) -> None:
        self._catalog_sync_running = False
        if not ok:
            self._catalog_sync_status.set(f"No se pudo sincronizar: {message[:80]}")
            return
        result, on_success = payload
        on_success()
        if result.source == "cache":
            self._catalog_sync_status.set(f"FHStats sin conexion; usando cache local ({result.error})")
        else:
            self._catalog_sync_status.set(f"Actualizado desde FHStats: {len(result.files_updated)} archivos")
            if result.error:
                self._catalog_sync_status.set(self._catalog_sync_status.get() + " · " + result.error)

    def _build_tier_filter_row(
        self,
        parent: tk.Frame,
        label: str,
        variable: tk.StringVar,
        options: list[tuple[str, str]],
    ) -> None:
        row = tk.Frame(parent, bg=COLORS["panel2"])
        row.pack(fill="x", pady=(0, 6))
        tk.Label(
            row,
            text=label,
            width=14,
            anchor="w",
            bg=COLORS["panel2"],
            fg=COLORS["muted"],
            font=("Segoe UI Semibold", 8),
        ).pack(side="left")
        for text, value in options:
            button = tk.Button(
                row,
                text=text,
                command=lambda selected=value, target=variable: self._set_tier_filter(target, selected),
                bd=0,
                padx=11,
                pady=7,
                font=("Segoe UI Semibold", 9),
                cursor="hand2",
            )
            button.pack(side="left", padx=(0, 6))
            self._tier_filter_buttons.append((button, variable, value))

    def _set_tier_filter(self, variable: tk.StringVar, value: str) -> None:
        variable.set(value)
        self._refresh_tier_filter_buttons()
        self._refresh_tierlist_tree()

    def _refresh_tier_filter_buttons(self) -> None:
        for button, variable, value in self._tier_filter_buttons:
            active = variable.get() == value
            button.configure(
                bg=COLORS["accent"] if active else COLORS["panel"],
                fg=COLORS["bg"] if active else COLORS["text"],
                activebackground="#d7ff52" if active else COLORS["line"],
                activeforeground=COLORS["bg"] if active else COLORS["accent2"],
                highlightthickness=1,
                highlightbackground=COLORS["accent"] if active else COLORS["line"],
            )

    def _scroll_tierlist(self, event: tk.Event) -> None:
        if self._active_page != "tierlist" or not hasattr(self, "_tierlist_canvas"):
            return
        self._tierlist_canvas.yview_scroll(int(-event.delta / 120), "units")

    def _refresh_tierlist_tree(self) -> None:
        if not hasattr(self, "_tierlist_cards"):
            return
        for child in self._tierlist_cards.winfo_children():
            child.destroy()
        self._tierlist_images: list[ImageTk.PhotoImage] = []
        discipline = self._tier_discipline.get()
        car_class = self._tier_class.get()
        query = self._tier_search.get().strip().lower()
        rows = self._tierlist_rows
        rows = [row for row in rows if row["discipline"] == discipline]
        rows = [row for row in rows if row["class"] == car_class]
        if query:
            rows = [row for row in rows if query in f"{row['make']} {row['model']} {row['tuner']}".lower()]
        if not rows:
            empty = self._build_empty_state(
                self._tierlist_cards,
                "No hay coches para esta busqueda.",
                "Prueba otra clase, modalidad o texto de búsqueda.",
                background=COLORS["panel"],
            )
            empty.pack(fill="x")
            return
        tier_colors = {"S+": COLORS["accent"], "S": COLORS["accent2"], "A": COLORS["warning"]}
        self._tierlist_cards.columnconfigure(0, weight=1)
        for section_index, tier_section in enumerate(build_meta_tier_sections(rows)):
            section_color = tier_colors[tier_section.tier]
            section = tk.Frame(
                self._tierlist_cards,
                bg=COLORS["panel2"],
                highlightthickness=1,
                highlightbackground=section_color,
            )
            section.grid(row=section_index, column=0, sticky="ew", pady=(0, 10))

            rail = tk.Frame(section, bg=COLORS["surface2"], width=132)
            rail.pack(side="left", fill="y")
            rail.pack_propagate(False)
            tk.Label(
                rail,
                text=tier_section.label,
                bg=COLORS["surface2"],
                fg=section_color,
                font=("Segoe UI Semibold", 20),
                anchor="w",
            ).pack(fill="x", padx=14, pady=(18, 0))
            tk.Label(
                rail,
                text=f"{len(tier_section.rows)} coches",
                bg=COLORS["surface2"],
                fg=COLORS["muted"],
                font=("Segoe UI Semibold", 8),
                anchor="w",
            ).pack(fill="x", padx=14, pady=(4, 14))

            cards = tk.Frame(section, bg=COLORS["panel2"], padx=8, pady=8)
            cards.pack(side="left", fill="both", expand=True)
            for index, row in enumerate(tier_section.rows):
                card = tk.Frame(
                    cards,
                    bg=COLORS["panel"],
                    highlightthickness=1,
                    highlightbackground=COLORS["line"],
                    padx=12,
                    pady=10,
                )
                card.grid(
                    row=index // 3,
                    column=index % 3,
                    sticky="nsew",
                    padx=(0 if index % 3 == 0 else 8, 0),
                    pady=(0, 8),
                )
                cards.columnconfigure(index % 3, weight=1, uniform=f"tier-card-{section_index}")
                self._build_tierlist_card(card, row, tier_section.label, section_color)

    def _build_tierlist_card(self, card: tk.Frame, row: dict[str, Any], tier_label: str, accent: str) -> None:
        image_frame = tk.Frame(card, bg=COLORS["panel2"], height=110)
        image_frame.pack(fill="x")
        image_frame.pack_propagate(False)
        image_label = tk.Label(image_frame, bg=COLORS["panel2"])
        image_label.pack(fill="both", expand=True)
        for candidate in dict.fromkeys([row.get("image"), row.get("fallback_image")]):
            if not candidate:
                continue
            try:
                with Image.open(candidate) as source:
                    image = source.convert("RGBA")
                image.thumbnail((290, 110), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(image)
                self._tierlist_images.append(photo)
                image_label.configure(image=photo)
                break
            except OSError:
                continue
        else:
            image_label.configure(text="SIN IMAGEN", fg=COLORS["muted"])

        title = f"{row['make']} {row['model']}"
        tk.Label(card, text=f"{tier_label}  ·  {row['class']}  ·  {row['discipline']}", bg=COLORS["panel"], fg=accent, font=("Segoe UI Semibold", 8), anchor="w").pack(fill="x", pady=(8, 2))
        tk.Label(card, text=title, bg=COLORS["panel"], fg=COLORS["text"], font=("Segoe UI Semibold", 11), anchor="w", wraplength=280, justify="left").pack(fill="x")
        tk.Label(card, text=f"Tune: {row['tune'] or '-'}  ·  {row['tuner'] or '-'}", bg=COLORS["panel"], fg=COLORS["muted"], font=("Segoe UI", 8), anchor="w").pack(fill="x", pady=(4, 0))
        tk.Label(card, text=f"{row['build'] or 'Build sin especificar'}  ·  {row['difficulty'] or 'Dificultad libre'}", bg=COLORS["panel"], fg=COLORS["muted"], font=("Segoe UI", 8), anchor="w").pack(fill="x")
        url = row["url"] or "https://fhstats.com/es/tierlist"
        tk.Button(
            card,
            text="Más información ↗",
            command=lambda target=url: webbrowser.open(target),
            bg=COLORS["panel2"],
            fg=COLORS["text"],
            activebackground=COLORS["line"],
            activeforeground=accent,
            bd=0,
            padx=10,
            pady=6,
            cursor="hand2",
            font=("Segoe UI Semibold", 8),
        ).pack(anchor="w", pady=(9, 0))

    def _recordings_directory(self) -> Path:
        path = self._settings.recording.output_dir
        return path if path.is_absolute() else DATA_ROOT / path

    def _open_recordings_folder(self) -> None:
        directory = self._recordings_directory()
        directory.mkdir(parents=True, exist_ok=True)
        reveal_command = _explorer_reveal_command(directory, self._selected_video_path)
        if reveal_command is not None:
            creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
            try:
                subprocess.Popen(
                    reveal_command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags,
                )
                return
            except OSError:
                pass
        os.startfile(directory)

    def _build_videos_page(self, parent: tk.Frame) -> None:
        wrapper = ttk.Frame(parent, padding=12)
        wrapper.pack(fill="both", expand=True)
        header = self._build_section_header(
            wrapper,
            "Biblioteca de partidas",
            "Reproduce, revisa y gestiona grabaciones locales. Los vídeos no se suben a FHStats.",
        )
        actions = tk.Frame(wrapper, bg=COLORS["bg"])
        actions.pack(fill="x", pady=(0, 12))
        ttk.Button(actions, text="Abrir carpeta", command=self._open_recordings_folder).pack(side="right")
        ttk.Button(actions, text="Actualizar lista", style="Quiet.TButton", command=self._refresh_videos).pack(side="right", padx=8)
        tk.Label(
            actions,
            textvariable=self._videos_summary,
            bg=COLORS["bg"],
            fg=COLORS["muted"],
            font=("Segoe UI Semibold", 9),
        ).pack(side="right", padx=(0, 12))

        content = tk.Frame(wrapper, bg=COLORS["bg"])
        content.pack(fill="both", expand=True)

        library = tk.Frame(
            content,
            bg=COLORS["panel"],
            highlightthickness=1,
            highlightbackground=COLORS["line"],
            padx=12,
            pady=12,
            width=390,
        )
        library.pack(side="left", fill="both")
        library.pack_propagate(False)

        library_header = tk.Frame(library, bg=COLORS["panel"])
        library_header.pack(fill="x", pady=(0, 10))
        tk.Label(
            library_header,
            text="GRABACIONES",
            bg=COLORS["panel"],
            fg=COLORS["accent2"],
            font=("Segoe UI Semibold", 9),
        ).pack(side="left")
        ttk.Button(library_header, text="Borrar seleccionados", style="Danger.TButton", command=self._delete_selected_video).pack(side="right")

        select_row = tk.Frame(library, bg=COLORS["panel"])
        select_row.pack(fill="x", pady=(0, 10))
        ttk.Button(select_row, text="Seleccionar todo", style="Quiet.TButton", command=self._select_all_videos).pack(side="left")
        ttk.Button(select_row, text="Limpiar selección", style="Quiet.TButton", command=self._clear_video_selection).pack(side="left", padx=(8, 0))

        columns = ("name", "date", "size")
        self._videos_tree = ttk.Treeview(library, columns=columns, show="headings", selectmode="extended")
        self._videos_tree.heading("name", text="Partida")
        self._videos_tree.heading("date", text="Fecha")
        self._videos_tree.heading("size", text="MB")
        self._videos_tree.column("name", width=210, anchor="w")
        self._videos_tree.column("date", width=105, anchor="w")
        self._videos_tree.column("size", width=58, anchor="e")
        videos_scroll = ttk.Scrollbar(library, orient="vertical", command=self._videos_tree.yview)
        self._videos_tree.configure(yscrollcommand=videos_scroll.set)
        self._videos_tree.pack(side="left", fill="both", expand=True)
        videos_scroll.pack(side="left", fill="y")
        self._videos_tree.bind("<<TreeviewSelect>>", self._on_video_selected)
        self._videos_tree.bind("<Double-1>", lambda _event: self._play_video())

        player = tk.Frame(
            content,
            bg=COLORS["panel2"],
            highlightthickness=1,
            highlightbackground=COLORS["line"],
            padx=14,
            pady=14,
        )
        player.pack(side="left", fill="both", expand=True, padx=(12, 0))

        player_header = tk.Frame(player, bg=COLORS["panel2"])
        player_header.pack(fill="x", pady=(0, 10))
        tk.Label(
            player_header,
            textvariable=self._video_title,
            bg=COLORS["panel2"],
            fg=COLORS["text"],
            font=("Segoe UI Semibold", 14),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(player_header, text="Abrir externo", command=self._open_selected_video).pack(side="right")

        tk.Label(
            player,
            textvariable=self._video_meta,
            bg=COLORS["panel2"],
            fg=COLORS["muted"],
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(fill="x", pady=(0, 10))

        self._video_canvas = tk.Canvas(
            player,
            bg="#020304",
            highlightthickness=1,
            highlightbackground=COLORS["line"],
            height=350,
        )
        self._video_canvas.pack(fill="both", expand=True)
        self._video_canvas.bind("<Configure>", self._on_video_canvas_configure)
        self._create_video_play_overlay()

        slider_row = tk.Frame(player, bg=COLORS["panel2"])
        slider_row.pack(fill="x", pady=(10, 0))
        self._video_progress_canvas = tk.Canvas(
            slider_row,
            bg="#0d1518",
            height=34,
            highlightthickness=1,
            highlightbackground=COLORS["line"],
            highlightcolor=COLORS["accent2"],
            cursor="hand2",
        )
        self._video_progress_canvas.pack(side="left", fill="x", expand=True)
        self._video_progress_canvas.bind("<Configure>", lambda _event: self._redraw_video_timebar())
        self._video_progress_canvas.bind("<ButtonPress-1>", self._begin_video_seek)
        self._video_progress_canvas.bind("<B1-Motion>", self._drag_video_seek)
        self._video_progress_canvas.bind("<ButtonRelease-1>", self._finish_video_seek)
        tk.Label(
            slider_row,
            textvariable=self._video_time,
            bg=COLORS["panel2"],
            fg=COLORS["muted"],
            font=("Segoe UI", 9),
            width=15,
            anchor="e",
        ).pack(side="right", padx=(12, 0))

        controls = tk.Frame(player, bg=COLORS["panel2"])
        controls.pack(fill="x", pady=(12, 0))
        ttk.Button(controls, text="Stop", command=self._stop_video).pack(side="left")
        ttk.Button(controls, text="-10s", command=lambda: self._seek_video_relative(-10)).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="+10s", command=lambda: self._seek_video_relative(10)).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Borrar video", style="Danger.TButton", command=self._delete_selected_video).pack(side="left", padx=(8, 0))

        status_row = tk.Frame(player, bg=COLORS["panel2"])
        status_row.pack(fill="x", pady=(8, 0))
        tk.Label(
            status_row,
            textvariable=self._video_status,
            bg=COLORS["panel2"],
            fg=COLORS["accent2"],
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        self._draw_video_placeholder("Selecciona una grabación para verla aquí.")

    def _refresh_videos(self) -> None:
        if not hasattr(self, "_videos_tree"):
            return
        self._videos_tree.delete(*self._videos_tree.get_children())
        directory = self._recordings_directory()
        directory.mkdir(parents=True, exist_ok=True)
        videos = sorted(
            (
                path
                for path in directory.iterdir()
                if path.is_file()
                and not path.name.startswith(".")
                and path.suffix.lower() in {".mkv", ".mp4"}
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        self._video_paths = {}
        total_size = 0
        selected_item: str | None = None
        for index, path in enumerate(videos):
            item_id = f"video-{index}"
            stat = path.stat()
            total_size += stat.st_size
            self._video_paths[item_id] = path
            if path == self._selected_video_path:
                selected_item = item_id
            self._videos_tree.insert(
                "",
                "end",
                iid=item_id,
                values=(
                    path.stem.replace("_", " - "),
                    datetime.fromtimestamp(stat.st_mtime).strftime("%d/%m %H:%M"),
                    f"{stat.st_size / 1024**2:.1f}",
                ),
            )
        self._videos_summary.set(f"{len(videos)} videos - {total_size / 1024**3:.2f} GB guardados")
        if selected_item:
            self._videos_tree.selection_set(selected_item)
            self._videos_tree.focus(selected_item)
        elif videos and self._selected_video_path is None:
            first_item = "video-0"
            self._videos_tree.selection_set(first_item)
            self._videos_tree.focus(first_item)
            self._load_video(videos[0], autoplay=False)
        elif not videos:
            self._release_video_player()
            self._selected_video_path = None
            self._video_title.set("Sin grabaciones")
            self._video_meta.set("Cuando grabes una partida aparecerá en esta biblioteca.")
            self._video_status.set("Biblioteca vacía")
            self._video_time.set("00:00 / 00:00")
            self._video_position.set(0.0)
            self._redraw_video_timebar()
            self._update_video_play_button()
            self._draw_video_placeholder("Todavía no hay vídeos grabados.")

    def _on_video_selected(self, _event: Any = None) -> None:
        paths = self._selected_videos_from_tree()
        if len(paths) == 1:
            path = paths[0]
            self._load_video(path, autoplay=False)
        elif len(paths) > 1:
            self._video_status.set(f"{len(paths)} videos seleccionados")

    def _selected_video_from_tree(self) -> Path | None:
        paths = self._selected_videos_from_tree()
        return paths[0] if paths else None

    def _selected_videos_from_tree(self) -> list[Path]:
        selection = self._videos_tree.selection() if hasattr(self, "_videos_tree") else ()
        paths: list[Path] = []
        for item_id in selection:
            path = self._video_paths.get(item_id)
            if path is not None:
                paths.append(path)
        return paths

    def _select_all_videos(self) -> None:
        if not hasattr(self, "_videos_tree"):
            return
        items = self._videos_tree.get_children()
        self._videos_tree.selection_set(items)
        self._video_status.set(f"{len(items)} videos seleccionados")

    def _clear_video_selection(self) -> None:
        if not hasattr(self, "_videos_tree"):
            return
        self._videos_tree.selection_remove(self._videos_tree.selection())
        self._video_status.set("Seleccion limpia")

    def _load_video(self, path: Path, autoplay: bool) -> None:
        if self._selected_video_path == path and self._video_player is not None:
            if autoplay:
                self._play_video()
            return
        self._release_video_player()
        self._selected_video_path = path
        player = self._ensure_mpv_player()
        if player is None:
            self._video_status.set("No se pudo iniciar mpv. Usa Abrir externo.")
            self._draw_video_placeholder("No se pudo cargar el reproductor.")
            return
        self._video_player = player
        self._video_duration_seconds = 0.0

        stat = path.stat()
        self._video_title.set(path.stem.replace("_", " - "))
        self._video_meta.set(
            f"{datetime.fromtimestamp(stat.st_mtime).strftime('%d/%m/%Y %H:%M')} - "
            f"{stat.st_size / 1024**2:.1f} MB"
        )
        self._video_status.set("Listo para reproducir")
        self._video_position.set(0.0)
        self._video_time.set("00:00 / 00:00")
        self._redraw_video_timebar()
        self._update_video_play_button()
        player.load(path, paused=not autoplay)
        self._schedule_video_tick(150)
        self.after(100, self._position_video_play_button)
        if autoplay:
            self._play_video()

    def _ensure_mpv_player(self) -> EmbeddedMpvPlayer | None:
        if self._video_player is not None and self._video_player.is_running:
            return self._video_player
        if not hasattr(self, "_video_canvas"):
            return None
        self.update_idletasks()
        mpv_path = PROJECT_ROOT / "runtime" / "mpv" / "mpv.exe"
        player = EmbeddedMpvPlayer(self._video_canvas.winfo_id(), mpv_path)
        return player if player.start() else None

    def _play_video(self) -> None:
        if self._video_player is None:
            path = self._selected_video_from_tree()
            if path is None:
                self._video_status.set("Selecciona primero una grabacion")
                return
            self._load_video(path, autoplay=True)
            return
        if self._video_playing:
            self._pause_video()
            return
        self._reset_video_play_clock()
        self._video_playing = True
        self._video_player.set_pause(False)
        self._video_status.set("Reproduciendo")
        self._update_video_play_button()
        self._schedule_video_tick(120)

    def _pause_video(self) -> None:
        self._video_playing = False
        if self._video_player is not None:
            self._video_player.set_pause(True)
        self._update_video_play_button()
        if self._video_player is not None:
            self._video_status.set("Pausado")

    def _stop_video(self) -> None:
        self._pause_video()
        if self._video_player is None:
            return
        self._video_player.stop()
        self._video_position.set(0.0)
        self._video_time.set(f"00:00 / {self._format_video_time(self._video_duration_seconds)}")
        self._update_video_progress()
        self._video_status.set("Detenido")

    def _schedule_video_tick(self, delay_ms: int | None = None) -> None:
        self._cancel_video_tick()
        delay = 250 if delay_ms is None else max(1, delay_ms)
        self._video_after_job = self.after(delay, self._video_tick)

    def _cancel_video_tick(self) -> None:
        if self._video_after_job is not None:
            try:
                self.after_cancel(self._video_after_job)
            except tk.TclError:
                pass
            self._video_after_job = None

    def _cancel_video_overlay_jobs(self) -> None:
        if self._video_overlay_after_job is not None:
            try:
                self.after_cancel(self._video_overlay_after_job)
            except tk.TclError:
                pass
            self._video_overlay_after_job = None
        if self._video_pointer_after_job is not None:
            try:
                self.after_cancel(self._video_pointer_after_job)
            except tk.TclError:
                pass
            self._video_pointer_after_job = None

    def _video_tick(self) -> None:
        self._video_after_job = None
        if self._video_player is None:
            return
        self._update_video_progress()
        _time_pos, _duration, paused, eof = self._video_player.snapshot()
        if eof:
            self._video_playing = False
            self._update_video_play_button()
            self._video_status.set("Fin del video")
            return
        if paused:
            self._video_playing = False
            self._update_video_play_button()
            if self._video_duration_seconds <= 0:
                self._schedule_video_tick(350)
            return
        self._video_playing = not paused
        self._schedule_video_tick(120)

    def _next_video_delay_ms(self) -> int:
        return 120

    def _reset_video_play_clock(self) -> None:
        self._video_play_anchor_frame = self._video_display_frame_index
        self._video_play_anchor_time = time.monotonic()

    def _on_video_canvas_configure(self, _event: Any = None) -> None:
        self._redraw_video_frame()
        self._position_video_play_button()

    def _redraw_video_frame(self) -> None:
        if self._video_player is None and hasattr(self, "_video_canvas"):
            self._draw_video_placeholder("Selecciona una grabación para verla aquí.")

    def _create_video_play_overlay(self) -> None:
        transparent_color = "#ff00ff"
        self._video_play_overlay = tk.Toplevel(self)
        self._video_play_overlay.withdraw()
        self._video_play_overlay.overrideredirect(True)
        self._video_play_overlay.transient(self)
        self._video_play_overlay.configure(bg=transparent_color)
        try:
            self._video_play_overlay.attributes("-transparentcolor", transparent_color)
        except tk.TclError:
            transparent_color = "#020304"
            self._video_play_overlay.configure(bg=transparent_color)
        self._video_play_overlay.bind("<Button-1>", self._on_video_play_overlay_click)
        self._video_play_button = tk.Label(
            self._video_play_overlay,
            text="\u25b6",
            bg=transparent_color,
            fg="#ffffff",
            cursor="hand2",
            font=("Segoe UI Symbol", 34),
        )
        self._video_play_button.pack(fill="both", expand=True)
        self._video_play_button.bind("<Button-1>", self._on_video_play_overlay_click)
        self._video_play_hitbox = tk.Toplevel(self)
        self._video_play_hitbox.withdraw()
        self._video_play_hitbox.overrideredirect(True)
        self._video_play_hitbox.transient(self)
        self._video_play_hitbox.configure(bg="#000000", cursor="hand2")
        try:
            self._video_play_hitbox.attributes("-alpha", 0.02)
        except tk.TclError:
            pass
        self._video_play_hitbox.bind("<Button-1>", self._on_video_play_overlay_click)

    def _on_video_play_overlay_click(self, _event: Any = None) -> str:
        self._play_video()
        return "break"

    def _position_video_play_button(self) -> None:
        if not hasattr(self, "_video_play_overlay") or not hasattr(self, "_video_canvas"):
            return
        if self._active_page != "videos":
            self._hide_video_overlay_windows()
            return
        current_or_selected = self._selected_video_path is not None or self._selected_video_from_tree() is not None
        if not current_or_selected or self._video_overlay_hidden:
            self._hide_video_overlay_windows()
            return
        canvas_width = max(1, self._video_canvas.winfo_width())
        canvas_height = max(1, self._video_canvas.winfo_height())
        button_size = 58
        x = self._video_canvas.winfo_rootx() + (canvas_width - button_size) // 2
        y = self._video_canvas.winfo_rooty() + (canvas_height - button_size) // 2
        geometry = f"{button_size}x{button_size}+{x}+{y}"
        self._video_play_overlay.geometry(geometry)
        self._video_play_hitbox.geometry(geometry)
        self._video_play_overlay.deiconify()
        self._video_play_hitbox.deiconify()
        self._raise_video_overlay_windows()

    def _raise_video_overlay_windows(self) -> None:
        if not hasattr(self, "_video_play_overlay") or not hasattr(self, "_video_play_hitbox"):
            return
        for window in (self._video_play_overlay, self._video_play_hitbox):
            try:
                window.attributes("-topmost", True)
            except tk.TclError:
                pass
        self._video_play_overlay.lift()
        self._video_play_hitbox.lift()
        self.after(150, self._release_video_overlay_topmost)

    def _release_video_overlay_topmost(self) -> None:
        if not hasattr(self, "_video_play_overlay") or not hasattr(self, "_video_play_hitbox"):
            return
        for window in (self._video_play_overlay, self._video_play_hitbox):
            try:
                window.attributes("-topmost", False)
            except tk.TclError:
                pass

    def _update_video_play_button(self) -> None:
        if not hasattr(self, "_video_play_button"):
            return
        if self._active_page != "videos":
            self._hide_video_overlay_for_inactive_page()
            return
        can_play = self._selected_video_path is not None or self._selected_video_from_tree() is not None
        if not can_play:
            self._cancel_video_overlay_jobs()
            self._video_overlay_hidden = True
            self._hide_video_overlay_windows()
            return
        self._video_overlay_hidden = False
        self._video_overlay_hidden_pointer = None
        self._video_play_button.configure(
            text="\u23f8" if self._video_playing else "\u25b6",
            fg="#ffffff" if self._video_playing else COLORS["accent2"],
        )
        self._position_video_play_button()
        if self._video_playing:
            self._schedule_video_overlay_hide()
        else:
            self._cancel_video_overlay_jobs()

    def _schedule_video_overlay_hide(self) -> None:
        self._cancel_video_overlay_jobs()
        self._video_overlay_after_job = self.after(3000, self._hide_video_overlay_after_playing)

    def _hide_video_overlay_after_playing(self) -> None:
        self._video_overlay_after_job = None
        if not self._video_playing or not hasattr(self, "_video_play_overlay"):
            return
        self._video_overlay_hidden = True
        self._video_overlay_hidden_pointer = self.winfo_pointerxy()
        self._hide_video_overlay_windows()
        self._schedule_video_pointer_watch()

    def _hide_video_overlay_windows(self) -> None:
        for attribute in ("_video_play_hitbox", "_video_play_overlay"):
            if hasattr(self, attribute):
                getattr(self, attribute).withdraw()

    def _hide_video_overlay_for_inactive_page(self) -> None:
        self._cancel_video_overlay_jobs()
        self._video_overlay_hidden = True
        self._video_overlay_hidden_pointer = None
        self._hide_video_overlay_windows()

    def _schedule_video_pointer_watch(self) -> None:
        if self._video_pointer_after_job is not None:
            return
        self._video_pointer_after_job = self.after(200, self._watch_video_pointer_for_overlay)

    def _watch_video_pointer_for_overlay(self) -> None:
        self._video_pointer_after_job = None
        if self._active_page != "videos":
            self._hide_video_overlay_for_inactive_page()
            return
        if not self._video_playing or not self._video_overlay_hidden or not hasattr(self, "_video_canvas"):
            return
        pointer = self.winfo_pointerxy()
        inside_video = self._is_pointer_inside_video(pointer)
        pointer_moved = pointer != self._video_overlay_hidden_pointer
        if inside_video and pointer_moved:
            self._video_overlay_hidden = False
            self._update_video_play_button()
            return
        self._schedule_video_pointer_watch()

    def _is_pointer_inside_video(self, pointer: tuple[int, int]) -> bool:
        if not hasattr(self, "_video_canvas"):
            return False
        x, y = pointer
        left = self._video_canvas.winfo_rootx()
        top = self._video_canvas.winfo_rooty()
        return left <= x <= left + self._video_canvas.winfo_width() and top <= y <= top + self._video_canvas.winfo_height()

    def _draw_video_placeholder(self, message: str) -> None:
        if not hasattr(self, "_video_canvas"):
            return
        self._video_current_frame = None
        self._video_canvas.delete("all")
        self._video_canvas.create_text(
            max(1, self._video_canvas.winfo_width()) // 2,
            max(1, self._video_canvas.winfo_height()) // 2,
            text=message,
            fill=COLORS["muted"],
            font=("Segoe UI Semibold", 12),
            width=max(240, self._video_canvas.winfo_width() - 80),
            justify="center",
        )

    def _update_video_progress(self) -> None:
        if self._video_player is None:
            return
        current_seconds, duration, paused, eof = self._video_player.snapshot()
        if duration > 0:
            self._video_duration_seconds = duration
        if not self._video_dragging and self._video_duration_seconds > 0:
            self._video_position.set(min(1000.0, current_seconds / self._video_duration_seconds * 1000.0))
        self._redraw_video_timebar()
        self._video_time.set(
            f"{self._format_video_time(current_seconds)} / {self._format_video_time(self._video_duration_seconds)}"
        )

    def _begin_video_seek(self, event: Any = None) -> None:
        if self._video_player is not None:
            _time_pos, _duration, paused, _eof = self._video_player.snapshot()
            self._video_seek_was_playing = not paused
            if self._video_seek_was_playing:
                self._video_player.set_pause(True)
        self._video_dragging = True
        self._cancel_video_tick()
        self._set_video_position_from_event(event)

    def _drag_video_seek(self, event: Any = None) -> None:
        if not self._video_dragging:
            return
        self._set_video_position_from_event(event)

    def _finish_video_seek(self, event: Any = None) -> None:
        self._set_video_position_from_event(event)
        self._video_dragging = False
        self._seek_video(float(self._video_position.get()), resume_tick=True)

    def _set_video_position_from_event(self, event: Any) -> None:
        if event is None or not hasattr(self, "_video_progress_canvas"):
            return
        canvas_width = max(1, self._video_progress_canvas.winfo_width())
        left = 8
        right = max(left + 1, canvas_width - 8)
        ratio = max(0.0, min(1.0, (float(event.x) - left) / float(right - left)))
        self._video_position.set(ratio * 1000.0)
        self._redraw_video_timebar()
        if self._video_duration_seconds > 0:
            preview_seconds = ratio * self._video_duration_seconds
            self._video_time.set(
                f"{self._format_video_time(preview_seconds)} / {self._format_video_time(self._video_duration_seconds)}"
            )

    def _seek_video(self, slider_value: float, resume_tick: bool = True) -> None:
        if self._video_player is None or self._video_duration_seconds <= 0:
            return
        ratio = max(0.0, min(1.0, slider_value / 1000.0))
        target_seconds = self._video_duration_seconds * ratio
        self._video_player.seek(target_seconds)
        self._update_video_progress()
        if self._video_seek_was_playing and resume_tick and not self._video_dragging:
            self._video_player.set_pause(False)
            self._video_playing = True
            self._update_video_play_button()
            self._schedule_video_tick()

    def _seek_video_relative(self, delta_seconds: float) -> None:
        if self._video_player is None:
            return
        current_seconds, duration, paused, _eof = self._video_player.snapshot()
        if duration > 0:
            self._video_duration_seconds = duration
        if self._video_duration_seconds <= 0:
            return
        target_seconds = max(0.0, min(self._video_duration_seconds, current_seconds + delta_seconds))
        self._video_seek_was_playing = not paused
        self._video_position.set(target_seconds / self._video_duration_seconds * 1000.0)
        self._video_player.seek(target_seconds)
        self._update_video_progress()
        if self._video_seek_was_playing:
            self._video_player.set_pause(False)
            self._video_playing = True
            self._update_video_play_button()
            self._schedule_video_tick()

    def _redraw_video_timebar(self) -> None:
        if not hasattr(self, "_video_progress_canvas"):
            return
        canvas = self._video_progress_canvas
        canvas.delete("all")
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        left = 8
        right = max(left + 1, width - 8)
        y = height // 2
        track_height = 6
        ratio = max(0.0, min(1.0, float(self._video_position.get()) / 1000.0))
        knob_x = int(left + (right - left) * ratio)
        canvas.create_rectangle(0, 0, width, height, fill="#0d1518", outline="")
        canvas.create_rectangle(
            left,
            y - track_height // 2,
            right,
            y + track_height // 2,
            fill=COLORS["line"],
            outline="",
        )
        canvas.create_rectangle(
            left,
            y - track_height // 2,
            knob_x,
            y + track_height // 2,
            fill=COLORS["accent"],
            outline="",
        )
        canvas.create_oval(
            knob_x - 6,
            y - 6,
            knob_x + 6,
            y + 6,
            fill=COLORS["accent2"],
            outline=COLORS["panel2"],
            width=2,
        )
        if self._video_duration_seconds <= 0:
            canvas.create_text(
                width // 2,
                y,
                text="barra de tiempo",
                fill=COLORS["muted"],
                font=("Segoe UI Semibold", 9),
            )

    def _release_video_player(self) -> None:
        self._pause_video()
        self._cancel_video_overlay_jobs()
        if self._video_player is not None:
            self._video_player.close()
            self._video_player = None
        self._video_display_frame_index = 0
        self._video_play_anchor_frame = 0
        self._video_play_anchor_time = 0.0
        self._video_current_frame = None
        self._video_frame_photo = None
        self._update_video_play_button()

    def _delete_selected_video(self) -> None:
        paths = self._selected_videos_from_tree()
        if not paths and self._selected_video_path is not None:
            paths = [self._selected_video_path]
        if not paths:
            self._video_status.set("Selecciona una grabacion para borrarla")
            return
        unique_paths = list(dict.fromkeys(paths))
        existing_paths = [path for path in unique_paths if path.exists()]
        if not existing_paths:
            self._video_status.set("Los videos seleccionados ya no existen")
            self._refresh_videos()
            return
        total_size = sum(path.stat().st_size for path in existing_paths)
        if len(existing_paths) == 1:
            message = f"Quieres borrar esta grabacion?\n\n{existing_paths[0].name}"
        else:
            message = (
                f"Quieres borrar {len(existing_paths)} videos seleccionados?\n\n"
                f"Se liberaran aproximadamente {total_size / 1024**3:.2f} GB."
            )
        if not messagebox.askyesno("Borrar videos", message):
            return
        if self._selected_video_path in existing_paths:
            self._release_video_player()
            self._selected_video_path = None
        deleted = 0
        errors: list[str] = []
        for path in existing_paths:
            try:
                path.unlink()
                deleted += 1
            except OSError as error:
                errors.append(f"{path.name}: {error}")
        if errors:
            messagebox.showerror("Borrar videos", "No se pudieron borrar algunos videos:\n\n" + "\n".join(errors[:5]))
        if deleted:
            self._video_status.set(f"{deleted} videos borrados" if deleted > 1 else "Video borrado")
        else:
            self._video_status.set("No se borro ningun video")
        self._refresh_videos()

    def _open_selected_video(self, _event: Any = None) -> None:
        paths = self._selected_videos_from_tree()
        path = paths[0] if len(paths) == 1 else self._selected_video_path
        if path and path.exists():
            os.startfile(path)

    @staticmethod
    def _format_video_time(seconds: float) -> str:
        total = max(0, int(seconds))
        hours, remainder = divmod(total, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def _build_garage_page(self, parent: tk.Frame) -> None:
        wrapper = ttk.Frame(parent, padding=12)
        wrapper.pack(fill="both", expand=True)
        self._build_section_header(
            wrapper,
            "Mi garaje",
            "Guarda una copia visual de Mis coches para proteger tu coleccion y preparar fotos para FHStats.",
        )

        controls = ttk.Frame(wrapper, style="Panel.TFrame", padding=16)
        controls.pack(fill="x")
        tk.Label(
            controls,
            text="Antes de ejecutar, abre Mis coches y deja seleccionado el primer coche.",
            bg=COLORS["panel"],
            fg=COLORS["warning"],
            font=("Segoe UI Semibold", 10),
            anchor="w",
        ).pack(fill="x")
        actions = ttk.Frame(controls, style="Panel.TFrame")
        actions.pack(fill="x", pady=(12, 0))
        self._garage_scan_button = ttk.Button(
            actions,
            text="Escanear garaje",
            style="Accent.TButton",
            command=self._start_garage_scan,
        )
        self._garage_scan_button.pack(side="left")
        self._garage_stop_button = ttk.Button(
            actions,
            text="Parar",
            style="Danger.TButton",
            command=self._stop_garage_scan,
            state="disabled",
        )
        self._garage_stop_button.pack(side="left", padx=(8, 0))
        ttk.Checkbutton(
            actions,
            text="Grabar video del recorrido",
            variable=self._garage_record_video,
        ).pack(side="left", padx=(16, 0))
        tk.Label(
            actions,
            textvariable=self._garage_status,
            bg=COLORS["panel"],
            fg=COLORS["accent2"],
            font=("Segoe UI", 9),
            anchor="e",
        ).pack(side="right")

        profile_actions = ttk.Frame(controls, style="Panel.TFrame")
        profile_actions.pack(fill="x", pady=(10, 0))
        ttk.Button(
            profile_actions,
            text="Guardar copia del progreso",
            command=self._save_profile_backup,
        ).pack(side="left")
        tk.Label(
            profile_actions,
            textvariable=self._profile_backup_status,
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(side="left", padx=(12, 0))
        self._refresh_profile_backup_status()

        tools = ttk.Frame(wrapper, style="Panel.TFrame", padding=14)
        tools.pack(fill="x", pady=(12, 0))
        slot_row = ttk.Frame(tools, style="Panel.TFrame")
        slot_row.pack(fill="x")
        ttk.Label(slot_row, text="COPIA", style="PanelMuted.TLabel").pack(side="left")
        for label, value in (("Actual", "current"), ("Backup", "backup")):
            ttk.Radiobutton(
                slot_row,
                text=label,
                value=value,
                variable=self._garage_slot,
                command=self._refresh_garage_cards,
            ).pack(side="left", padx=(10, 0))
        ttk.Button(slot_row, text="Comparar con backup", command=self._compare_garage_backup).pack(side="right")
        self._garage_video_button = ttk.Button(slot_row, text="Abrir vídeo", command=self._open_garage_video)
        self._garage_video_button.pack(side="right", padx=(0, 8))
        self._garage_export_button = ttk.Button(
            slot_row,
            text="Exportar para FHStats",
            command=self._export_garage_for_fhstats,
        )
        self._garage_export_button.pack(side="right", padx=(0, 8))

        filters = ttk.Frame(tools, style="Panel.TFrame")
        filters.pack(fill="x", pady=(12, 0))
        ttk.Entry(filters, textvariable=self._garage_query, width=30).pack(side="left")
        self._garage_manufacturer_filter = ttk.Combobox(
            filters,
            textvariable=self._garage_manufacturer,
            values=self._garage_manufacturer.display_values(("all",)),
            state="readonly",
            width=18,
        )
        self._garage_manufacturer_filter.pack(side="left", padx=(8, 0))
        self._garage_class_filter = ttk.Combobox(
            filters,
            textvariable=self._garage_class,
            values=self._garage_class.display_values(("all", "R", "S2", "S1", "A", "B", "C", "D")),
            state="readonly",
            width=8,
        )
        self._garage_class_filter.pack(side="left", padx=(8, 0))
        self._garage_confidence_filter = ttk.Combobox(
            filters,
            textvariable=self._garage_confidence,
            values=self._garage_confidence.display_values(("all", "confirmed", "review")),
            state="readonly",
            width=12,
        )
        self._garage_confidence_filter.pack(side="left", padx=(8, 0))
        for widget in (
            self._garage_manufacturer_filter,
            self._garage_class_filter,
            self._garage_confidence_filter,
        ):
            widget.bind("<<ComboboxSelected>>", lambda _event: self._refresh_garage_cards())
        tk.Label(
            filters,
            textvariable=self._garage_summary,
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=("Segoe UI", 9),
        ).pack(side="right")

        body = tk.Frame(wrapper, bg=COLORS["bg"])
        body.pack(fill="both", expand=True, pady=(12, 0))
        self._garage_canvas = tk.Canvas(body, bg=COLORS["bg"], borderwidth=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=self._garage_canvas.yview)
        self._garage_canvas.configure(yscrollcommand=scrollbar.set)
        self._garage_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self._garage_grid = tk.Frame(self._garage_canvas, bg=COLORS["bg"])
        self._garage_canvas_window = self._garage_canvas.create_window((0, 0), anchor="nw", window=self._garage_grid)
        self._garage_grid.bind("<Configure>", self._sync_garage_scroll_region)
        self._garage_canvas.bind("<Configure>", self._sync_garage_scroll_region)
        self._garage_canvas.bind("<MouseWheel>", self._scroll_garage)
        self._refresh_garage_cards()

    def _sync_garage_scroll_region(self, _event: Any = None) -> None:
        if not hasattr(self, "_garage_canvas"):
            return
        width = max(1, self._garage_canvas.winfo_width())
        self._garage_canvas.itemconfigure(self._garage_canvas_window, width=width)
        self._garage_canvas.configure(scrollregion=self._garage_canvas.bbox("all"))

    def _scroll_garage(self, event: tk.Event[tk.Misc]) -> str:
        self._garage_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def _refresh_garage_cards(self) -> None:
        if not hasattr(self, "_garage_grid"):
            return
        for child in self._garage_grid.winfo_children():
            child.destroy()
        self._garage_card_photos.clear()
        slot = "backup" if self._garage_slot.get() == "backup" else "current"
        snapshot = self._garage_store.load_slot(slot)
        if snapshot is None:
            self._garage_status.set("No hay una copia guardada en esta posicion")
            self._garage_summary.set("0 coches")
            self._garage_video_button.configure(state="disabled")
            self._garage_export_button.configure(state="disabled")
            self._build_empty_state(self._garage_grid, tr("desktop.garage.empty"),
                                    tr("desktop.garage.empty_help")).pack(fill="x", padx=4, pady=4)
            return
        slot_root = self._garage_store.backup_path if slot == "backup" else self._garage_store.current_path
        video_path = slot_root / snapshot.video_path if snapshot.video_path else None
        self._garage_video_button.configure(state="normal" if video_path and video_path.is_file() else "disabled")
        self._garage_export_button.configure(state="normal")
        manufacturer = "" if self._garage_manufacturer.get() == "all" else self._garage_manufacturer.get()
        class_name = "" if self._garage_class.get() == "all" else self._garage_class.get()
        confidence = "" if self._garage_confidence.get() == "all" else self._garage_confidence.get()
        cards = build_garage_cards(
            snapshot,
            snapshot_root=slot_root,
            query=self._garage_query.get(),
            manufacturer=manufacturer,
            class_name=class_name,
            confidence=confidence,
        )
        manufacturers = sorted({car.make for car in snapshot.cars if car.make}, key=str.casefold)
        self._garage_manufacturer_filter.configure(
            values=self._garage_manufacturer.display_values(("all", *manufacturers))
        )
        review_count = sum(car.quantity for car in snapshot.cars if car.confidence != "confirmed")
        shown_count = sum(card.quantity for card in cards)
        self._garage_summary.set(
            f"{shown_count}/{snapshot.observed_count} coches · {review_count} por revisar"
        )
        completed = _parse_iso_datetime(snapshot.completed_at)
        slot_label = tr("garage.backup") if slot == "backup" else tr("event.current")
        snapshot_value = completed.astimezone().strftime("%d/%m/%Y %H:%M") if completed else snapshot.scan_id[:8]
        self._garage_status.set(tr("garage.snapshot_status", slot=slot_label, value=snapshot_value))
        if not cards:
            self._build_empty_state(self._garage_grid, tr("desktop.garage.no_matches"),
                                    tr("desktop.garage.filter_help")).pack(fill="x", padx=4, pady=4)
            return
        columns = 3
        for index, card in enumerate(cards):
            self._build_garage_card(self._garage_grid, card, index // columns, index % columns, slot)
        for column in range(columns):
            self._garage_grid.columnconfigure(column, weight=1, uniform="garage")
        self.after_idle(self._sync_garage_scroll_region)

    def _build_garage_card(
        self,
        parent: tk.Frame,
        card: GarageCardView,
        row: int,
        column: int,
        slot: str,
    ) -> None:
        panel = tk.Frame(
            parent,
            bg=COLORS["panel"],
            highlightthickness=1,
            highlightbackground=COLORS["warning"] if card.confidence != "confirmed" else COLORS["line"],
            padx=10,
            pady=10,
        )
        panel.grid(row=row, column=column, sticky="nsew", padx=5, pady=5)
        try:
            with Image.open(card.image_path) as source:
                image = source.convert("RGB")
                image.thumbnail((260, 138), Image.Resampling.LANCZOS)
                canvas = Image.new("RGB", (260, 138), COLORS["surface"])
                canvas.paste(image, ((260 - image.width) // 2, (138 - image.height) // 2))
            photo = ImageTk.PhotoImage(canvas)
            self._garage_card_photos.append(photo)
            tk.Label(panel, image=photo, bg=COLORS["panel"]).pack(fill="x")
        except (OSError, ValueError):
            tk.Label(
                panel,
                text="Sin imagen",
                bg=COLORS["surface"],
                fg=COLORS["muted"],
                height=7,
            ).pack(fill="x")
        title = card.title + (f"  ×{card.quantity}" if card.quantity > 1 else "")
        tk.Label(
            panel,
            text=title,
            bg=COLORS["panel"],
            fg=COLORS["text"],
            font=("Segoe UI Semibold", 10),
            anchor="w",
        ).pack(fill="x", pady=(8, 0))
        tk.Label(
            panel,
            text=f"{card.subtitle}  ·  {card.class_pi}".strip(" ·"),
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(fill="x", pady=(3, 0))
        if card.confidence != "confirmed" and slot == "current":
            ttk.Button(panel, text="Revisar", command=lambda value=card: self._review_garage_card(value)).pack(
                anchor="w", pady=(8, 0)
            )

    def _refresh_profile_backup_status(self) -> None:
        manifest = load_profile_backup_manifest(self._profile_backup_path)
        if manifest is None or not manifest.verified:
            self._profile_backup_status.set("Sin copia del progreso")
            return
        size_mb = manifest.total_size / (1024 * 1024)
        created = manifest.created_at.replace("T", " ").split("+")[0]
        self._profile_backup_status.set(f"Copia verificada: {created} · {size_mb:.1f} MB")

    def _save_profile_backup(self) -> None:
        if _is_game_process_running():
            messagebox.showwarning(
                "Mi garaje",
                "Cierra Forza Horizon 6 antes de guardar la copia del progreso.",
                parent=self,
            )
            return
        if load_profile_backup_manifest(self._profile_backup_path) is not None:
            replace_existing = messagebox.askyesno(
                "Mi garaje",
                "Ya existe una copia del progreso. ¿Deseas sustituirla?",
                parent=self,
            )
            if not replace_existing:
                return
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            messagebox.showerror("Mi garaje", "Windows no proporciono LOCALAPPDATA.", parent=self)
            return
        try:
            manifest = create_profile_backup(
                Path(local_app_data),
                self._profile_backup_path,
                is_game_running=_is_game_process_running,
            )
        except ProfileBackupError as error:
            messagebox.showerror("Mi garaje", str(error), parent=self)
            return
        self._refresh_profile_backup_status()
        messagebox.showinfo(
            "Mi garaje",
            f"Copia verificada correctamente ({manifest.total_size / (1024 * 1024):.1f} MB).",
            parent=self,
        )

    def _start_garage_scan(self) -> None:
        if self._garage_worker_running:
            messagebox.showwarning("Mi garaje", "Ya hay un escaneo de garaje en curso.", parent=self)
            return
        if not _is_game_process_running():
            messagebox.showwarning("Mi garaje", "Abre Forza Horizon y entra en Mis coches.", parent=self)
            return
        if self._garage_record_video.get():
            if bool(read_recording_status().get("recording")):
                messagebox.showwarning(
                    "Mi garaje",
                    "Ya hay otra grabación activa. Deténla antes de escanear el garaje.",
                    parent=self,
                )
                return
            if self._capture_process is None or self._capture_process.poll() is not None:
                self._start_capture()
        if self._garage_store.load_slot("draft") is not None:
            replace_draft = messagebox.askyesno(
                "Mi garaje",
                "Hay un escaneo provisional anterior. ¿Deseas descartarlo e iniciar uno nuevo?",
                parent=self,
            )
            if not replace_draft:
                return
            self._garage_store.discard_draft()
        preserve_duplicates = messagebox.askyesno(
            "Mi garaje",
            "¿Deseas conservar los coches repetidos?\n\nSí: una tarjeta por coche.\nNo: agrupar por modelo.",
            parent=self,
        )
        options = ScanOptions(
            record_video=bool(self._garage_record_video.get()),
            preserve_duplicates=preserve_duplicates,
        )
        self._garage_worker_running = True
        self._garage_stop_event = threading.Event()
        self._garage_scan_button.configure(state="disabled")
        self._garage_stop_button.configure(state="normal")
        self._garage_status.set("Activando Forza...")
        settings = self._settings
        stop_event = self._garage_stop_event
        threading.Thread(
            target=self._garage_scan_worker,
            args=(settings, options, stop_event),
            name="fhstats-garage-scan",
            daemon=True,
        ).start()

    def _garage_scan_worker(self, settings: Any, options: ScanOptions, stop_event: threading.Event) -> None:
        def progress(message: str) -> None:
            self.after(0, lambda value=message: self._garage_status.set(value))

        recording_path: Path | None = None
        video_warning: str | None = None
        if options.record_video:
            try:
                recording_path = self._begin_garage_recording()
            except Exception as error:
                video_warning = f"No se pudo iniciar el vídeo: {sanitize_error_message(str(error))}"
        try:
            result = run_garage_scan_script(settings, options, progress, stop_event)
        except Exception as error:
            message = sanitize_error_message(str(error))
            cancelled = stop_event.is_set()
            self.after(0, lambda value=message, stopped=cancelled: self._on_garage_scan_error(value, stopped))
        else:
            if recording_path is not None:
                try:
                    self._finish_garage_recording(recording_path)
                except Exception as error:
                    video_warning = f"El inventario se guardó, pero el vídeo falló: {sanitize_error_message(str(error))}"
            self.after(
                0,
                lambda value=result, warning=video_warning: self._on_garage_scan_complete(value, warning),
            )
            return
        if recording_path is not None:
            try:
                self._finish_garage_recording(recording_path)
            except Exception:
                pass

    def _begin_garage_recording(self) -> Path:
        if bool(read_recording_status().get("recording")):
            raise RuntimeError("Ya existe otra grabación activa.")
        send_recording_command("start_session")
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            status = read_recording_status()
            output_path = status.get("output_path")
            if bool(status.get("recording")) and output_path:
                return Path(str(output_path)).expanduser().resolve()
            time.sleep(0.15)
        raise RuntimeError("El capturador no confirmó el inicio en 20 segundos.")

    def _finish_garage_recording(self, source: Path) -> Path:
        send_recording_command("stop")
        video_dir = self._garage_store.draft_path / "video"
        destination = copy_finalized_recording(
            source,
            video_dir / "garage.mkv",
            status_reader=read_recording_status,
        )
        draft = self._garage_store.load_slot("draft")
        if draft is None:
            raise GarageInvalidDraftError("El borrador desapareció antes de asociar el vídeo.")
        self._garage_store.write_draft(replace(draft, video_path="video/garage.mkv"))
        return destination

    def _on_garage_scan_complete(self, result: GarageRawScanResult, video_warning: str | None = None) -> None:
        try:
            try:
                promoted = self._garage_store.promote_draft()
            except GarageCountDropWarning as warning:
                confirmed = messagebox.askyesno(
                    "Mi garaje",
                    tr(
                        "garage.confirm_count_drop",
                        current_count=warning.current_count,
                        draft_count=warning.draft_count,
                    ),
                    parent=self,
                )
                if not confirmed:
                    self._garage_status.set("Escaneo conservado como provisional; la copia actual no cambio")
                    return
                promoted = self._garage_store.promote_draft(confirm_count_drop=True)
            self._garage_slot.set("current")
            self._garage_status.set(
                f"Garaje guardado: {promoted.observed_count} coches en {result.elapsed_seconds:.1f} s"
            )
            self._refresh_garage_cards()
            if video_warning:
                messagebox.showwarning("Mi garaje", video_warning, parent=self)
        except Exception as error:
            messagebox.showerror("Mi garaje", sanitize_error_message(str(error)), parent=self)
        finally:
            self._finish_garage_scan()

    def _on_garage_scan_error(self, message: str, cancelled: bool) -> None:
        self._garage_status.set(
            "Escaneo cancelado; la copia actual no cambio"
            if cancelled
            else tr("common.error", message=message[:100])
        )
        if not cancelled:
            messagebox.showerror("Mi garaje", message, parent=self)
        self._refresh_garage_cards()
        self._finish_garage_scan()

    def _finish_garage_scan(self) -> None:
        self._garage_worker_running = False
        self._garage_stop_event = None
        self._garage_scan_button.configure(state="normal")
        self._garage_stop_button.configure(state="disabled")

    def _stop_garage_scan(self) -> None:
        if self._garage_stop_event is not None:
            self._garage_stop_event.set()
            self._garage_status.set("Parando al terminar la captura actual...")

    def _open_garage_video(self) -> None:
        slot = "backup" if self._garage_slot.get() == "backup" else "current"
        snapshot = self._garage_store.load_slot(slot)
        slot_root = self._garage_store.backup_path if slot == "backup" else self._garage_store.current_path
        if snapshot is None or not snapshot.video_path:
            return
        path = (slot_root / snapshot.video_path).resolve()
        if path.is_file() and os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]

    def _export_garage_for_fhstats(self) -> None:
        slot = "backup" if self._garage_slot.get() == "backup" else "current"
        snapshot = self._garage_store.load_slot(slot)
        if snapshot is None:
            return
        selected = filedialog.askdirectory(title="Carpeta donde crear la exportación FHStats", parent=self)
        if not selected:
            return
        slot_root = self._garage_store.backup_path if slot == "backup" else self._garage_store.current_path
        destination = Path(selected) / f"fhstats-garage-{snapshot.scan_id[:8]}"
        try:
            package = export_fhstats_package(snapshot, destination, source_root=slot_root)
        except Exception as error:
            messagebox.showerror("Mi garaje", sanitize_error_message(str(error)), parent=self)
            return
        self._garage_status.set(f"Exportación creada en {package}")
        if os.name == "nt":
            os.startfile(package)  # type: ignore[attr-defined]

    def _compare_garage_backup(self) -> None:
        current = self._garage_store.load_slot("current")
        backup = self._garage_store.load_slot("backup")
        if current is None or backup is None:
            messagebox.showinfo("Mi garaje", "Necesitas una copia actual y un backup para comparar.", parent=self)
            return
        current_ids = {car.canonical_id for car in current.cars if car.canonical_id}
        backup_ids = {car.canonical_id for car in backup.cars if car.canonical_id}
        current_review = sum(car.quantity for car in current.cars if car.confidence != "confirmed")
        backup_review = sum(car.quantity for car in backup.cars if car.confidence != "confirmed")
        messagebox.showinfo(
            "Comparar garaje",
            tr(
                "garage.compare_summary",
                current_count=current.observed_count,
                current_review=current_review,
                backup_count=backup.observed_count,
                backup_review=backup_review,
                added=len(current_ids - backup_ids),
                missing=len(backup_ids - current_ids),
            ),
            parent=self,
        )

    def _review_garage_card(self, card: GarageCardView) -> None:
        snapshot = self._garage_store.load_slot("current")
        if snapshot is None:
            return
        catalog = load_fh6_car_catalog()
        dialog = tk.Toplevel(self)
        dialog.title("Revisar coche")
        dialog.geometry("620x440")
        dialog.configure(bg=COLORS["bg"])
        dialog.transient(self)
        dialog.grab_set()
        content = ttk.Frame(dialog, padding=20)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="Confirmar coche", style="Title.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        variables = {
            "year": tk.StringVar(value=str(card.car.year or "")),
            "make": tk.StringVar(value=card.car.make or ""),
            "model": tk.StringVar(value=card.car.model or ""),
            "class": tk.StringVar(value=card.car.car_class or ""),
            "pi": tk.StringVar(value=str(card.car.performance_index or "")),
        }
        for row, (label, key) in enumerate(
            (("Año", "year"), ("Marca", "make"), ("Modelo", "model"), ("Clase", "class"), ("PI", "pi")),
            start=1,
        ):
            ttk.Label(content, text=label).grid(row=row, column=0, sticky="w", pady=(10, 0))
            ttk.Entry(content, textvariable=variables[key], width=45).grid(row=row, column=1, sticky="ew", padx=(12, 0), pady=(10, 0))
        catalog_values = tuple(
            f"{car_id} · {car.display_name}" for car_id, car in sorted(catalog.items(), key=lambda item: item[1].display_name.casefold())
        )
        selected_catalog = tk.StringVar(value="")
        if card.car.canonical_id and int(card.car.canonical_id) in catalog:
            selected_catalog.set(f"{card.car.canonical_id} · {catalog[int(card.car.canonical_id)].display_name}")
        ttk.Label(content, text="Catálogo FH6").grid(row=6, column=0, sticky="w", pady=(10, 0))
        ttk.Combobox(content, textvariable=selected_catalog, values=catalog_values, width=58).grid(
            row=6, column=1, sticky="ew", padx=(12, 0), pady=(10, 0)
        )
        content.columnconfigure(1, weight=1)

        def save_review() -> None:
            match = re.match(r"^(\d+)\s+·", selected_catalog.get())
            if match is None or int(match.group(1)) not in catalog:
                messagebox.showwarning("Mi garaje", "Selecciona un coche del catálogo FH6.", parent=dialog)
                return
            try:
                year = int(variables["year"].get())
                pi = int(variables["pi"].get())
            except ValueError:
                messagebox.showwarning("Mi garaje", "Año y PI deben ser números.", parent=dialog)
                return
            canonical_id = match.group(1)
            catalog_car = catalog[int(canonical_id)]
            updated_car = replace(
                card.car,
                display_name=catalog_car.display_name,
                year=year,
                make=variables["make"].get().strip(),
                model=variables["model"].get().strip(),
                car_class=variables["class"].get().strip().upper(),
                performance_index=pi,
                canonical_id=canonical_id,
                confidence="confirmed",
            )
            updated_cars = tuple(updated_car if car.capture_index == card.capture_index else car for car in snapshot.cars)
            self._garage_store.write_current(replace(snapshot, cars=updated_cars))
            raw_lines = card.car.raw_ocr.splitlines()
            raw_name = "\n".join(raw_lines[:-1]) if len(raw_lines) > 1 else card.car.raw_ocr
            alias_query = card_text_to_catalog_query(raw_name).query
            save_alias(self._garage_store.root / "aliases.json", alias_query, canonical_id)
            dialog.destroy()
            self._refresh_garage_cards()

        ttk.Button(content, text="Guardar corrección", style="Accent.TButton", command=save_review).grid(
            row=7, column=1, sticky="e", pady=(18, 0)
        )



























    def _build_settings_page(self, parent: tk.Frame) -> None:
        self._settings_canvas = tk.Canvas(
            parent,
            bg=COLORS["bg"],
            borderwidth=0,
            highlightthickness=0,
            takefocus=0,
        )
        settings_scrollbar = ttk.Scrollbar(
            parent,
            orient="vertical",
            command=self._settings_canvas.yview,
        )
        self._settings_canvas.configure(yscrollcommand=settings_scrollbar.set)
        self._settings_canvas.pack(side="left", fill="both", expand=True)
        settings_scrollbar.pack(side="right", fill="y")
        wrapper = ttk.Frame(self._settings_canvas, padding=12)
        self._settings_canvas_window = self._settings_canvas.create_window(
            (0, 0),
            anchor="nw",
            window=wrapper,
        )
        wrapper.bind(
            "<Configure>",
            lambda _event: self._settings_canvas.configure(
                scrollregion=self._settings_canvas.bbox("all")
            ),
        )
        self._settings_canvas.bind(
            "<Configure>",
            lambda event: self._settings_canvas.itemconfigure(
                self._settings_canvas_window,
                width=event.width,
            ),
        )
        self.bind_all("<MouseWheel>", self._scroll_settings_on_mousewheel, add="+")
        self._build_section_header(
            wrapper,
            "Ajustes",
            "Configura grabación, privacidad, diagnóstico y actualizaciones desde un único sitio.",
        )

        language_panel = ttk.Frame(wrapper, style="Panel.TFrame", padding=18)
        language_panel.pack(fill="x")
        ttk.Label(language_panel, text=tr("settings.language.title"), style="PanelTitle.TLabel").pack(anchor="w")
        ttk.Label(
            language_panel,
            text=tr("settings.language.description"),
            style="PanelMuted.TLabel",
        ).pack(anchor="w", pady=(4, 10))
        language_row = ttk.Frame(language_panel, style="Panel.TFrame")
        language_row.pack(fill="x")
        for language, label_key in (("en", "settings.language.english"), ("es", "settings.language.spanish")):
            ttk.Radiobutton(
                language_row,
                text=tr(label_key),
                value=language,
                variable=self._language_setting,
            ).pack(side="left", padx=(0, 16))
        ttk.Button(
            language_row,
            text=tr("settings.language.apply"),
            style="Accent.TButton",
            command=self._save_language_setting,
        ).pack(side="left")
        self._language_feedback = LocalizedStringVar(value="")
        ttk.Label(
            language_panel,
            textvariable=self._language_feedback,
            style="PanelMuted.TLabel",
        ).pack(anchor="w", pady=(10, 0))

        telemetry_panel = ttk.Frame(wrapper, style="Panel.TFrame", padding=18)
        telemetry_panel.pack(fill="x", pady=(14, 0))
        ttk.Label(
            telemetry_panel,
            text=tr("telemetry.setup.title"),
            style="PanelTitle.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            telemetry_panel,
            text=tr("telemetry.setup.description"),
            style="PanelMuted.TLabel",
        ).pack(anchor="w", pady=(4, 10))
        tk.Checkbutton(
            telemetry_panel,
            text=tr("telemetry.enabled"),
            variable=self._telemetry_enabled,
            bg=COLORS["panel"],
            fg=COLORS["text"],
            activebackground=COLORS["panel"],
            activeforeground=COLORS["text"],
            selectcolor=COLORS["panel2"],
            highlightthickness=0,
            bd=0,
            font=("Segoe UI Semibold", 10),
        ).pack(anchor="w")
        for setup_key in (
            "telemetry.setup.route",
            "telemetry.setup.data_out",
            "telemetry.setup.ip",
            "telemetry.setup.port",
            "telemetry.setup.listener",
        ):
            ttk.Label(
                telemetry_panel,
                text=tr(setup_key),
                style="PanelField.TLabel" if setup_key == "telemetry.setup.route" else "PanelMuted.TLabel",
            ).pack(anchor="w", pady=(6 if setup_key == "telemetry.setup.route" else 1, 0))
        ttk.Label(
            telemetry_panel,
            text=tr("telemetry.description"),
            style="PanelMuted.TLabel",
        ).pack(anchor="w", pady=(8, 0))
        ttk.Label(
            telemetry_panel,
            text=tr("telemetry.setup.optional"),
            style="PanelMuted.TLabel",
        ).pack(anchor="w", pady=(2, 0))
        self._telemetry_setup_status = LocalizedStringVar(
            value=tr(
                "telemetry.setup.status_enabled"
                if self._settings.telemetry.enabled
                else "telemetry.setup.status_disabled"
            )
        )
        ttk.Label(
            telemetry_panel,
            textvariable=self._telemetry_setup_status,
            style="PanelMuted.TLabel",
        ).pack(anchor="w", pady=(8, 0))


        recording = ttk.Frame(wrapper, style="Panel.TFrame", padding=18)
        recording.pack(fill="x", pady=(14, 0))
        recording.columnconfigure(1, weight=1)
        ttk.Label(recording, text="Grabación", style="PanelTitle.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(recording, text="Modo de grabación", style="PanelField.TLabel").grid(row=1, column=0, sticky="w", pady=(14, 8))
        mode_row = tk.Frame(recording, bg=COLORS["panel"])
        mode_row.grid(row=1, column=1, columnspan=3, sticky="w", padx=(14, 0), pady=(14, 8))
        for text, value in [
            ("No grabar", "off"),
            ("Sesión completa", "session"),
            ("Partidas separadas · Recomendado", "races"),
        ]:
            self._build_segmented_button(
                mode_row,
                text,
                self._recording_mode,
                value,
                command=self._refresh_recording_mode_controls,
            ).pack(side="left", padx=(0, 8))
        tk.Checkbutton(
            recording,
            text="Incluir audio del juego",
            variable=self._recording_audio,
            bg=COLORS["panel"],
            fg=COLORS["text"],
            activebackground=COLORS["panel"],
            activeforeground=COLORS["text"],
            selectcolor=COLORS["panel2"],
            highlightthickness=0,
            bd=0,
            font=("Segoe UI", 10),
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(0, 14))
        ttk.Label(recording, text="Fotogramas por segundo", style="PanelField.TLabel").grid(row=3, column=0, sticky="w")
        for column, text, value in [
            (1, "30 FPS · estable", 30),
            (2, "60 FPS · máxima fluidez", 60),
        ]:
            self._build_segmented_button(
                recording,
                text,
                self._recording_fps,
                value,
            ).grid(row=3, column=column, sticky="w", padx=(14 if column == 1 else 8, 0))
        ttk.Label(recording, text="Espacio máximo (GB)", style="PanelField.TLabel").grid(row=4, column=0, sticky="w", pady=(14, 0))
        ttk.Entry(recording, textvariable=self._recording_storage, width=10).grid(row=4, column=1, sticky="w", padx=14, pady=(14, 0))
        ttk.Label(recording, text="Carpeta de vídeos", style="PanelField.TLabel").grid(row=5, column=0, sticky="w", pady=(14, 0))
        path_row = tk.Frame(recording, bg=COLORS["panel"])
        path_row.grid(row=5, column=1, columnspan=3, sticky="ew", padx=(14, 0), pady=(14, 0))
        tk.Entry(
            path_row,
            textvariable=self._recording_output_dir,
            bg=COLORS["panel2"],
            fg=COLORS["text"],
            insertbackground=COLORS["accent"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=COLORS["line"],
            highlightcolor=COLORS["accent2"],
            font=("Segoe UI", 10),
        ).pack(side="left", fill="x", expand=True, ipady=6)
        ttk.Button(path_row, text="Elegir carpeta", command=self._browse_recording_output_dir).pack(side="left", padx=(8, 0))
        self._settings_feedback = LocalizedStringVar(value="")
        ttk.Label(recording, textvariable=self._settings_feedback, style="PanelMuted.TLabel").grid(row=6, column=0, columnspan=3, sticky="w", pady=(14, 0))
        recording_actions = ttk.Frame(recording, style="Panel.TFrame")
        recording_actions.grid(row=7, column=0, columnspan=3, sticky="w", pady=(16, 0))
        ttk.Button(recording_actions, text="Guardar ajustes", style="Accent.TButton", command=self._save_settings_page).pack(side="left")
        self._session_record_button = ttk.Button(recording_actions, text="Iniciar sesión completa", command=self._start_session_recording)
        self._session_record_button.pack(side="left", padx=8)
        self._manual_stop_button = ttk.Button(recording_actions, text="Parar grabación", style="Danger.TButton", command=self._stop_manual_recording)
        self._manual_stop_button.pack(side="left")
        self._refresh_recording_mode_controls()

        overlay = ttk.Frame(wrapper, style="Panel.TFrame", padding=18)
        overlay.pack(fill="x", pady=(14, 0))
        overlay.columnconfigure(1, weight=1)
        ttk.Label(overlay, text="Overlay ingame", style="PanelTitle.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w"
        )
        tk.Checkbutton(
            overlay,
            text="Activar overlay",
            variable=self._overlay_enabled,
            bg=COLORS["panel"],
            fg=COLORS["text"],
            activebackground=COLORS["panel"],
            activeforeground=COLORS["text"],
            selectcolor=COLORS["panel2"],
            highlightthickness=0,
            bd=0,
            font=("Segoe UI", 10),
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(12, 8))
        ttk.Label(overlay, text="Opacidad", style="PanelField.TLabel").grid(
            row=2, column=0, sticky="w", pady=4
        )
        ttk.Spinbox(
            overlay,
            textvariable=self._overlay_opacity,
            from_=0.45,
            to=0.95,
            increment=0.05,
            width=8,
        ).grid(row=2, column=1, sticky="w", padx=(12, 24), pady=4)
        ttk.Label(overlay, text="Escala", style="PanelField.TLabel").grid(
            row=2, column=2, sticky="w", pady=4
        )
        ttk.Spinbox(
            overlay,
            textvariable=self._overlay_scale,
            from_=0.75,
            to=1.25,
            increment=0.05,
            width=8,
        ).grid(row=2, column=3, sticky="w", padx=(12, 0), pady=4)
        ttk.Label(overlay, text="Duración (segundos)", style="PanelField.TLabel").grid(
            row=3, column=0, sticky="w", pady=4
        )
        ttk.Spinbox(
            overlay,
            textvariable=self._overlay_duration,
            from_=5,
            to=35,
            increment=1,
            width=8,
        ).grid(row=3, column=1, sticky="w", padx=(12, 24), pady=4)
        ttk.Label(
            overlay,
            text="Click-through permanente: no bloquea ratón, teclado ni mando.",
            style="PanelMuted.TLabel",
        ).grid(row=4, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Button(
            overlay,
            text="Guardar overlay",
            style="Accent.TButton",
            command=self._save_overlay_settings,
        ).grid(row=5, column=0, sticky="w", pady=(12, 0))
        ttk.Button(
            overlay,
            text="Mostrar previsualización",
            command=self._preview_ingame_overlay,
        ).grid(row=5, column=1, columnspan=2, sticky="w", padx=(8, 0), pady=(12, 0))

        utilities = ttk.Frame(wrapper, style="Panel.TFrame", padding=18)
        utilities.pack(fill="x", pady=(14, 0))
        ttk.Label(utilities, text="Herramientas", style="PanelTitle.TLabel").pack(anchor="w")
        ttk.Label(
            utilities,
            text="Accesos de mantenimiento. No cambian tus carreras guardadas salvo que lo confirmes en cada diálogo.",
            style="PanelMuted.TLabel",
        ).pack(anchor="w", pady=(4, 0))
        row = ttk.Frame(utilities, style="Panel.TFrame")
        row.pack(fill="x", pady=(14, 0))
        ttk.Button(row, text="Cambiar gamertag", command=self._open_gamertag_settings).pack(side="left")
        ttk.Button(row, text="Privacidad", command=self._open_privacy_settings).pack(side="left", padx=8)
        ttk.Button(row, text="Diagnóstico", command=self._run_capture_diagnostic).pack(side="left")
        ttk.Button(row, text="Buscar actualización", command=self._check_for_updates).pack(side="left", padx=8)

    def _scroll_settings_on_mousewheel(self, event: tk.Event[tk.Misc]) -> str | None:
        if self._active_page != "settings" or not hasattr(self, "_settings_canvas"):
            return None
        self._settings_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def _save_language_setting(self) -> None:
        selected = normalize_language(self._language_setting.get())
        if selected is None:
            return
        if selected == (self._settings.app.language or "en"):
            self._language_feedback.set(tr("settings.language.unchanged"))
            return
        try:
            self._config_path = save_language(
                selected,
                local_path=DATA_ROOT / "config" / "tracker.local.yaml",
                default_path=DEFAULT_CONFIG,
            )
        except (OSError, ValueError) as error:
            messagebox.showerror(
                tr("settings.language.saved_title"),
                tr("settings.language.save_error", message=error),
                parent=self,
            )
            return
        if self._garage_worker_running:
            self._language_feedback.set(tr("settings.language.busy"))
            return
        restart_now = messagebox.askyesno(
            tr("settings.language.saved_title"),
            tr("settings.language.saved"),
            parent=self,
        )
        if restart_now:
            self._restart_for_language()
        else:
            self._language_feedback.set(tr("settings.language.saved_later"))

    def _restart_for_language(self) -> None:
        frozen = bool(getattr(sys, "frozen", False))
        command = build_restart_command(
            executable=sys.executable,
            argv=sys.argv,
            frozen=frozen,
        )
        creationflags = 0
        if os.name == "nt":
            creationflags = (
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
            )
        working_directory = Path(sys.executable).resolve().parent if frozen else PROJECT_ROOT
        try:
            subprocess.Popen(
                command,
                cwd=working_directory,
                close_fds=True,
                creationflags=creationflags,
            )
        except OSError as error:
            messagebox.showerror(
                tr("settings.language.saved_title"),
                tr("settings.language.save_error", message=error),
                parent=self,
            )
            return
        self.after(50, self._exit_app)

    def _preview_ingame_overlay(self) -> None:
        if self._overlay_runtime.show_preview():
            self._settings_feedback.set("Previsualización mostrada sobre Forza.")
        else:
            self._settings_feedback.set("Abre Forza y detecta primero una inscripción.")

    def _save_overlay_settings(self) -> None:
        try:
            opacity = float(self._overlay_opacity.get())
            scale = float(self._overlay_scale.get())
            duration = int(self._overlay_duration.get())
        except (TypeError, ValueError, tk.TclError):
            self._settings_feedback.set("Revisa opacidad, escala y duración del overlay.")
            return
        if not 0.45 <= opacity <= 0.95 or not 0.75 <= scale <= 1.25:
            self._settings_feedback.set("Opacidad o escala del overlay fuera de rango.")
            return
        if not 5 <= duration <= 35:
            self._settings_feedback.set("La duración del overlay debe estar entre 5 y 35 segundos.")
            return
        data = self._load_editable_config()
        overlay = data.setdefault("overlay", {})
        if not isinstance(overlay, dict):
            overlay = {}
            data["overlay"] = overlay
        overlay.update(
            {
                "enabled": bool(self._overlay_enabled.get()),
                "opacity": opacity,
                "scale": scale,
                "duration_seconds": duration,
            }
        )
        self._write_local_config(data)
        self._settings = load_settings(self._config_path)
        self._overlay_enabled.set(self._settings.overlay.enabled)
        self._overlay_opacity.set(self._settings.overlay.opacity)
        self._overlay_scale.set(self._settings.overlay.scale)
        self._overlay_duration.set(self._settings.overlay.duration_seconds)
        self._overlay_runtime.apply_settings(self._settings.overlay)
        self._settings_feedback.set("Ajustes del overlay aplicados.")

    def _browse_recording_output_dir(self) -> None:
        current = self._recording_output_dir_path_from_text(allow_empty=True)
        initial = current if current is not None else self._recordings_directory()
        selected = filedialog.askdirectory(
            parent=self,
            title="Carpeta donde guardar los vídeos",
            initialdir=str(initial),
        )
        if not selected:
            return
        self._recording_output_dir.set(self._recording_output_dir_config_value(Path(selected)))

    def _recording_output_dir_path_from_text(self, allow_empty: bool = False) -> Path | None:
        text = self._recording_output_dir.get().strip().strip('"')
        if not text:
            if allow_empty:
                return None
            raise ValueError("La carpeta de vídeos no puede estar vacía.")
        path = Path(text).expanduser()
        return path if path.is_absolute() else DATA_ROOT / path

    @staticmethod
    def _recording_output_dir_display(path: Path) -> str:
        return str(path) if path.is_absolute() else path.as_posix()

    @staticmethod
    def _recording_output_dir_config_value(path: Path) -> str:
        resolved = path.expanduser().resolve()
        try:
            return resolved.relative_to(DATA_ROOT.resolve()).as_posix()
        except ValueError:
            return str(resolved)

    def _save_settings_page(self) -> None:
        try:
            max_storage = float(self._recording_storage.get().replace(",", "."))
        except ValueError:
            self._settings_feedback.set("El espacio máximo debe ser un número.")
            return
        if max_storage < 1 or max_storage > 1000:
            self._settings_feedback.set("El límite debe estar entre 1 y 1000 GB.")
            return
        try:
            output_path = self._recording_output_dir_path_from_text()
            assert output_path is not None
            output_path.mkdir(parents=True, exist_ok=True)
            output_dir_value = self._recording_output_dir_config_value(output_path)
        except (OSError, ValueError) as error:
            self._settings_feedback.set(f"No se pudo preparar la carpeta de vídeos: {error}")
            return
        data = self._load_editable_config()
        recording = data.setdefault("recording", {})
        if not isinstance(recording, dict):
            recording = {}
            data["recording"] = recording
        recording.update(
            {
                "enabled": self._recording_mode.get() == "races",
                "mode": self._recording_mode.get(),
                "capture_audio": bool(self._recording_audio.get()),
                "framerate": 60 if self._recording_fps.get() == 60 else 30,
                "max_storage_gb": max_storage,
                "output_dir": output_dir_value,
                "width": 1920,
                "height": 1080,
                "quality": 20,
                "keyframe_interval_seconds": 2,
                "ffmpeg_path": "runtime/ffmpeg/bin/ffmpeg.exe",
                "audio_helper_path": "runtime/audio/FHStatsAudioCapture.exe",
            }
        )
        telemetry = data.setdefault("telemetry", {})
        if not isinstance(telemetry, dict):
            telemetry = {}
            data["telemetry"] = telemetry
        telemetry["enabled"] = bool(self._telemetry_enabled.get())
        telemetry.setdefault("game", "fh6")
        telemetry.setdefault("host", self._settings.telemetry.host)
        telemetry.setdefault("port", self._settings.telemetry.port)
        telemetry.setdefault("stale_after_seconds", self._settings.telemetry.stale_after_seconds)
        self._write_local_config(data)
        self._settings = load_settings(self._config_path)
        self._recording_output_dir.set(self._recording_output_dir_display(self._settings.recording.output_dir))
        self._telemetry_enabled.set(self._settings.telemetry.enabled)
        if hasattr(self, "_telemetry_setup_status"):
            self._telemetry_setup_status.set(
                tr(
                    "telemetry.setup.status_enabled"
                    if self._settings.telemetry.enabled
                    else "telemetry.setup.status_disabled"
                )
            )
        feedback_key = "recording.saved_fps_restart" if self._capture_process is not None else "recording.saved_fps"
        self._settings_feedback.set(tr(feedback_key, fps=self._settings.recording.framerate))
        if self._capture_process is not None:
            self._stop_capture()
            self.after(700, self._start_capture)

    def _refresh_recording_mode_controls(self) -> None:
        if not hasattr(self, "_session_record_button"):
            return
        session_mode = self._recording_mode.get() == "session"
        recording = bool(read_recording_status().get("recording"))
        self._session_record_button.configure(state="normal" if session_mode and not recording else "disabled")
        self._manual_stop_button.configure(state="normal" if recording else "disabled")

    def _build_history_tab(self, parent: ttk.Frame) -> None:
        self._build_section_header(
            parent,
            tr("history.title"),
            "Revisa carreras guardadas, filtra por modalidad o clase y abre capturas cuando necesites auditar una lectura.",
            pady=(0, 10),
        )
        notebook = ttk.Notebook(parent)
        notebook.pack(fill="both", expand=True)
        summary_page = ttk.Frame(notebook, padding=(4, 12))
        races_page = ttk.Frame(notebook, padding=(4, 12))
        notebook.add(summary_page, text="Resumen")
        notebook.add(races_page, text="Carreras")
        self._build_stats_tab(summary_page)
        panes = ttk.Panedwindow(races_page, orient="vertical")
        panes.pack(fill="both", expand=True)
        parent = ttk.Frame(panes)
        panes.add(parent, weight=3)

        columns = ("time", "type", "event", "circuit", "discipline", "class", "car", "start", "finish", "championship", "total", "best")
        self._tree = ttk.Treeview(parent, columns=columns, show="headings")
        headings = {
            "time": "Fecha",
            "type": "Tipo",
            "event": "Evento",
            "circuit": "Circuito",
            "discipline": "Modalidad",
            "class": "Clase",
            "car": "Coche",
            "start": "Salida",
            "finish": "Llegada",
            "championship": "Evento",
            "total": "Tiempo",
            "best": "Mejor vuelta",
        }
        widths = {"time": 120, "type": 125, "event": 70, "circuit": 210, "discipline": 105, "class": 75, "car": 155, "start": 65, "finish": 70, "championship": 70, "total": 95, "best": 105}
        for column in columns:
            self._tree.heading(column, text=headings[column])
            self._tree.column(column, width=widths[column], minwidth=widths[column], anchor="w")

        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=scrollbar.set)
        horizontal = ttk.Scrollbar(parent, orient="horizontal", command=self._tree.xview)
        self._tree.configure(xscrollcommand=horizontal.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        self._tree.tag_configure("odd", background=COLORS["row"])
        self._tree.tag_configure("even", background=COLORS["row_alt"])
        self._tree.tag_configure("win", foreground=COLORS["accent"])
        self._tree.tag_configure("podium", foreground=COLORS["accent2"])
        self._tree.tag_configure("empty", background=COLORS["panel"], foreground=COLORS["muted"])
        self._tree.bind("<<TreeviewSelect>>", self._on_select)
        self._tree.bind("<Double-1>", self._open_selected_capture)

        detail = ttk.Frame(panes, style="Panel.TFrame", padding=14)
        panes.add(detail, weight=1)
        self._detail = tk.Text(detail, width=60, height=5, bg=COLORS["panel"], fg=COLORS["text"], insertbackground=COLORS["text"], relief="flat", wrap="word", font=("Consolas", 9))
        self._detail.pack(fill="both", expand=True)
        ttk.Button(detail, text="Abrir captura", command=self._open_selected_capture).pack(fill="x", pady=(12, 0))

    def _build_stats_tab(self, parent: ttk.Frame) -> None:
        self._history_summary_vars = {
            "races": tk.StringVar(value="0"),
            "wins": tk.StringVar(value="0 · 0%"),
            "podiums": tk.StringVar(value="0 · 0%"),
            "avg": tk.StringVar(value="-"),
            "gain": tk.StringVar(value="-"),
            "cars": tk.StringVar(value="0"),
        }
        items = [
            ("Carreras", "races"),
            ("Victorias", "wins"),
            ("Podios", "podiums"),
            ("Posición media", "avg"),
            ("Puestos ganados", "gain"),
            ("Coches usados", "cars"),
        ]
        for index, (label, key) in enumerate(items):
            card = tk.Frame(parent, bg=COLORS["surface"], padx=14, pady=12, highlightthickness=1, highlightbackground=COLORS["line_soft"])
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 8, 0))
            tk.Label(card, text=label, bg=COLORS["surface"], fg=COLORS["muted"], font=("Segoe UI", 8)).pack(anchor="w")
            tk.Label(
                card,
                textvariable=self._history_summary_vars[key],
                bg=COLORS["surface"],
                fg=COLORS["accent"] if index < 4 else COLORS["accent2"],
                font=("Segoe UI Semibold", 16),
            ).pack(anchor="w", pady=(3, 0))
        for column in range(len(items)):
            parent.columnconfigure(column, weight=1)

        filters = ttk.Frame(parent)
        filters.grid(row=1, column=0, columnspan=len(items), sticky="ew", pady=(14, 8))
        self._build_summary_filters(filters)

        insights = tk.Frame(parent, bg=COLORS["bg"])
        insights.grid(row=2, column=0, columnspan=len(items), sticky="ew", pady=(4, 12))
        self._history_insight_vars = {
            "favorite_car": tk.StringVar(value="-"),
            "favorite_class": tk.StringVar(value="-"),
            "best_car": tk.StringVar(value="-"),
            "comeback": tk.StringVar(value="-"),
        }
        for index, (title, key) in enumerate([
            ("MÁS UTILIZADO", "favorite_car"),
            ("CLASE FAVORITA", "favorite_class"),
            ("MEJOR RENDIMIENTO", "best_car"),
            ("MEJOR REMONTADA", "comeback"),
        ]):
            card = tk.Frame(insights, bg=COLORS["panel2"], padx=12, pady=9, highlightthickness=1, highlightbackground=COLORS["line"])
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 8, 0))
            tk.Label(card, text=title, bg=COLORS["panel2"], fg=COLORS["accent2"], font=("Segoe UI Semibold", 8)).pack(anchor="w")
            tk.Label(
                card,
                textvariable=self._history_insight_vars[key],
                bg=COLORS["panel2"],
                fg=COLORS["text"],
                font=("Segoe UI Semibold", 10),
                anchor="w",
            ).pack(fill="x", pady=(3, 0))
            insights.columnconfigure(index, weight=1)

        lists = ttk.Frame(parent)
        lists.grid(row=3, column=0, columnspan=len(items), sticky="nsew")
        parent.rowconfigure(3, weight=1)
        lists.columnconfigure(0, weight=1)
        lists.columnconfigure(1, weight=1)
        lists.rowconfigure(1, weight=1)

        ttk.Label(lists, text="Rendimiento por coche", style="Title.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=(0, 8))
        ttk.Label(lists, text="Rendimiento por circuito", style="Title.TLabel").grid(row=0, column=1, sticky="w", padx=(10, 0), pady=(0, 8))
        self._car_summary_tree = self._build_summary_tree(lists, 0, "car")
        self._circuit_summary_tree = self._build_summary_tree(lists, 1, "circuit")

    def _build_summary_filters(self, parent: ttk.Frame) -> None:
        discipline_row = ttk.Frame(parent)
        discipline_row.pack(fill="x")
        ttk.Label(discipline_row, text="Modalidad", style="Muted.TLabel").pack(side="left", padx=(0, 10))
        for value, label in SUMMARY_DISCIPLINE_FILTERS:
            button = tk.Button(
                discipline_row,
                text=label,
                command=lambda selected=value: self._set_summary_filter("discipline", selected),
                bd=0,
                padx=12,
                pady=7,
                font=("Segoe UI Semibold", 9),
                cursor="hand2",
            )
            button.pack(side="left", padx=(0, 6))
            self._summary_filter_buttons.append((button, "discipline", value))

        class_row = ttk.Frame(parent)
        class_row.pack(fill="x", pady=(7, 0))
        ttk.Label(class_row, text="Clase", style="Muted.TLabel").pack(side="left", padx=(0, 10))
        for value in SUMMARY_CLASS_FILTERS:
            button = tk.Button(
                class_row,
                text=tr("filter.all") if value == "all" else value,
                command=lambda selected=value: self._set_summary_filter("class", selected),
                bd=0,
                padx=10,
                pady=7,
                font=("Segoe UI Semibold", 9),
                cursor="hand2",
            )
            button.pack(side="left", padx=(0, 6))
            self._summary_filter_buttons.append((button, "class", value))
        self._refresh_summary_filter_buttons()

    def _set_summary_filter(self, kind: str, value: str) -> None:
        if kind == "discipline":
            self._summary_discipline_filter.set(value)
        else:
            self._summary_class_filter.set(value)
        self._refresh_summary_filter_buttons()
        self._refresh_summary()

    def _refresh_summary_filter_buttons(self) -> None:
        for button, kind, value in self._summary_filter_buttons:
            active = (
                self._summary_discipline_filter.get() == value
                if kind == "discipline"
                else self._summary_class_filter.get() == value
            )
            button.configure(
                bg=COLORS["accent"] if active and kind == "discipline" else COLORS["accent2"] if active else COLORS["panel2"],
                fg=COLORS["bg"] if active else COLORS["text"],
                activebackground="#d7ff52" if kind == "discipline" else "#62fff0",
                activeforeground=COLORS["bg"],
            )

    def _build_summary_tree(self, parent: ttk.Frame, column_index: int, kind: str) -> ttk.Treeview:
        columns = ("name", "class", "races", "win_rate", "podium_rate", "avg", "gain")
        if kind == "circuit":
            columns += ("favorite",)
        table = ttk.Frame(parent)
        table.grid(row=1, column=column_index, sticky="nsew", padx=(0, 10) if column_index == 0 else (10, 0))
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        tree = ttk.Treeview(table, columns=columns, show="headings", height=10)
        headings = {
            "name": "Coche" if kind == "car" else "Circuito",
            "class": "Clase",
            "races": "Carreras",
            "win_rate": "% vict.",
            "podium_rate": "% podio",
            "avg": "Pos. media",
            "gain": "+/- puestos",
            "favorite": "Coche usado" if kind == "circuit" else "Circuito",
        }
        widths = {
            "name": 145 if kind == "circuit" else 175,
            "class": 58,
            "races": 54,
            "win_rate": 56,
            "podium_rate": 58,
            "avg": 62,
            "gain": 65,
            "favorite": 105,
        }
        for col in columns:
            tree.heading(col, text=headings[col])
            tree.column(col, width=widths[col], minwidth=widths[col], anchor="w")
        if kind == "circuit":
            tree.column("favorite", stretch=True)
        tree.tag_configure("odd", background=COLORS["row"])
        tree.tag_configure("even", background=COLORS["row_alt"])
        tree.tag_configure("empty", background=COLORS["panel"], foreground=COLORS["muted"])
        tree.grid(row=0, column=0, sticky="nsew")
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=tree.xview)
        vertical = ttk.Scrollbar(table, orient="vertical", command=tree.yview)
        tree.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
        horizontal.grid(row=1, column=0, sticky="ew")
        vertical.grid(row=0, column=1, sticky="ns")
        return tree

    def refresh(self, force: bool = False) -> None:
        self._refresh_recording_status()
        signature = self._repository.signature()
        current_recommendation_signature = recommendation_signature()
        previous_recommendation_signature = self._recommendation_signature
        if (
            not force
            and signature == self._history_signature
            and current_recommendation_signature == self._recommendation_signature
        ):
            return
        self._history_signature = signature
        self._recommendation_signature = current_recommendation_signature
        self._entries = self._repository.load()
        self._races = build_race_history(self._entries)
        if self._races:
            self._status.set(f"{len(self._races)} carreras guardadas")
        else:
            self._status.set(tr("history.empty_with_path", path=self._repository.path))
        self._refresh_compact()
        self._refresh_recommendation()
        self._refresh_table()
        self._refresh_summary()
        self._auto_open_recommendation(
            previous_recommendation_signature,
            current_recommendation_signature,
        )

    def _auto_open_recommendation(
        self,
        previous_signature: tuple[int, int] | None,
        current_signature: tuple[int, int] | None,
    ) -> None:
        if self._settings.overlay.enabled:
            self._recommendation_monitor_initialized = True
            return
        if not self._recommendation_monitor_initialized:
            self._recommendation_monitor_initialized = True
            if current_signature is not None:
                payload = read_recommendation()
                requested_events = payload.get("requested_event_plan")
                car_class = str(payload.get("car_class") or "")
                if isinstance(requested_events, list) and len(requested_events) >= 3 and car_class:
                    self._last_auto_opened_event = (
                        tuple(str(event) for event in requested_events[:3]),
                        car_class,
                    )
            return
        if current_signature is None:
            return
        payload = read_recommendation()
        requested_events = payload.get("requested_event_plan")
        car_class = str(payload.get("car_class") or "")
        if not isinstance(requested_events, list) or len(requested_events) < 3 or not car_class:
            return
        event_key = (tuple(str(event) for event in requested_events[:3]), car_class)
        if current_signature == previous_signature or event_key == self._last_auto_opened_event:
            return
        self._last_auto_opened_event = event_key
        self._show_page("home")
        self.lift()
        self.focus_force()

    def _refresh_recommendation(self) -> None:
        if not hasattr(self, "_recommendation_widgets"):
            return
        payload = read_recommendation()
        status = str(payload.get("status") or "")
        car_class = str(payload.get("car_class") or "")
        requested_events = payload.get("requested_event_plan")
        if status == "loading":
            self._recommendation_title.set(tr("recommendation.searching", car_class=car_class))
            self._recommendation_meta.set(tr("recommendation.analyzing"))
        elif status == "waiting":
            self._recommendation_title.set(tr("recommendation.event_detected", car_class=car_class))
            self._recommendation_meta.set(
                str(payload.get("message") or tr("recommendation.waiting_three"))
            )
        elif status == "error":
            self._recommendation_title.set(tr("recommendation.unavailable"))
            self._recommendation_meta.set(str(payload.get("message") or tr("recommendation.retry")))
            self._retry_stale_recommendation(payload)
        elif status == "pending":
            self._recommendation_title.set(tr("recommendation.pending", car_class=car_class))
            self._recommendation_meta.set(tr("recommendation.pending_detail"))
            self._retry_stale_recommendation(payload)
        elif status == "no_data":
            self._recommendation_title.set(tr("recommendation.none", car_class=car_class))
            self._recommendation_meta.set(str(payload.get("message") or tr("recommendation.missing_data")))
        elif status == "ok":
            confidence = str(payload.get("confidence") or "-")
            discipline = str(payload.get("discipline") or "-")
            confidence = {
                "alta": tr("recommendation.confidence_high"),
                "high": tr("recommendation.confidence_high"),
                "media": tr("recommendation.confidence_medium"),
                "medium": tr("recommendation.confidence_medium"),
                "baja": tr("recommendation.confidence_low"),
                "low": tr("recommendation.confidence_low"),
            }.get(confidence.casefold(), confidence)
            discipline = {
                "road": tr("filter.road"),
                "street": tr("filter.street"),
                "dirt": tr("filter.dirt"),
                "cross-country": tr("filter.cross_country"),
            }.get(discipline.casefold(), discipline)
            self._recommendation_title.set(tr("recommendation.result", car_class=car_class))
            events = payload.get("events") if isinstance(payload.get("events"), list) else []
            available_maps = sum(1 for event in events if isinstance(event, dict) and event.get("map_available"))
            self._recommendation_meta.set(
                tr(
                    "recommendation.confidence",
                    discipline=discipline,
                    confidence=confidence,
                    available=available_maps,
                    total=len(events),
                )
            )
        else:
            self._recommendation_title.set(tr("recommendation.title"))
            event_count = len(requested_events) if isinstance(requested_events, list) else 0
            self._recommendation_meta.set(
                tr("recommendation.event_progress", count=event_count)
                if event_count
                else tr("recommendation.waiting")
            )

        recommendations = payload.get("recommendations")
        values = recommendations if isinstance(recommendations, list) else []
        for index, widgets in enumerate(self._recommendation_widgets):
            item = values[index] if index < len(values) and isinstance(values[index], dict) else None
            if item is None:
                widgets["name"].configure(text="Esperando datos")
                widgets["details"].configure(text="Aparecerá cuando FHStats tenga contexto del evento.")
                widgets["reason"].configure(text="")
                continue
            car = item.get("car") if isinstance(item.get("car"), dict) else {}
            tune = item.get("tune") if isinstance(item.get("tune"), dict) else {}
            rivals = item.get("rivals") if isinstance(item.get("rivals"), dict) else {}
            score = item.get("score")
            widgets["name"].configure(text=f"{car.get('name', '-')} · {score} pts")
            tune_label = tune.get("code") or tune.get("name") or tr("recommendation.no_code")
            widgets["details"].configure(
                text=(
                    f"Tier {car.get('tier', '-')} · {tune_label} · {tune.get('tuner') or '-'} · "
                    f"{rivals.get('appearances', 0)} Rivals"
                )
            )
            widgets["reason"].configure(text=str(item.get("reason") or ""))

    def _retry_stale_recommendation(self, payload: dict[str, Any]) -> None:
        if self._recommendation_retry_started:
            return
        event_plan = payload.get("requested_event_plan")
        car_class = payload.get("car_class")
        if not isinstance(event_plan, list) or not event_plan or not car_class:
            return
        self._recommendation_retry_started = True
        threading.Thread(
            target=self._retry_stale_recommendation_worker,
            args=([str(item) for item in event_plan[:3]], str(car_class)),
            daemon=True,
        ).start()

    def _retry_stale_recommendation_worker(self, event_plan: list[str], car_class: str) -> None:
        try:
            payload = recommend_championship(event_plan, car_class)
            payload["requested_event_plan"] = event_plan
            payload["source"] = "local"
            write_recommendation(payload)
        except Exception:
            self._recommendation_retry_started = False

    def _refresh_compact(self) -> None:
        self._refresh_dashboard_command_center(
            build_dashboard_view_model(
                self._races,
                read_status(),
                read_telemetry_status(),
            )
        )
        latest = self._latest_meaningful_entry()
        if latest is None:
            self._refresh_event_progress()
            return
        self._compact_values["event"].set(_event_label(latest.raw.get("event_number"), latest.raw.get("event_total")))
        self._compact_values["circuit"].set(_display_circuit_name(latest.get("circuit_name")))
        self._compact_values["start"].set(_prefixed(latest.raw.get("starting_position"), "P"))
        self._compact_values["finish"].set(_prefixed(latest.raw.get("position"), "P"))
        self._compact_values["car"].set(str(latest.get("car")))
        self._compact_values["time"].set(str(latest.get("total_time")))
        self._refresh_event_progress()

    def _refresh_track_map_panel(self, circuit_name: str | None) -> None:
        if not hasattr(self, "_track_map_title"):
            return
        panel = build_track_map_panel(circuit_name)
        self._track_map_title.set(panel.title)
        self._track_map_meta.set(panel.subtitle)
        if panel.image_path is None:
            self._track_map_photo = None
            self._track_map_image_label.configure(image="", text="")
            return
        try:
            image = Image.open(panel.image_path).convert("RGBA")
            image.thumbnail((220, 138), Image.Resampling.LANCZOS)
            self._track_map_photo = ImageTk.PhotoImage(image)
            self._track_map_image_label.configure(image=self._track_map_photo, text="")
        except OSError:
            self._track_map_photo = None
            self._track_map_image_label.configure(image="", text="")

    def _active_circuit_name_for_map(self, latest: RaceEntry | None) -> str | None:
        if latest is not None:
            circuit = str(latest.raw.get("circuit_name") or "").strip()
            if circuit and circuit != "-":
                return circuit
        for entry in reversed(_current_event_entries(self._entries)):
            circuit = _entry_circuit_name(entry)
            if circuit:
                return circuit
            event_plan = entry.raw.get("event_plan")
            if isinstance(event_plan, list):
                for item in event_plan:
                    value = str(item or "").strip()
                    if value:
                        return value
        return None

    def _refresh_dashboard_command_center(self, view_model: DashboardViewModel) -> None:
        if not hasattr(self, "_dashboard_card_values"):
            return
        self._dashboard_hero_title.set(view_model.hero_title)
        self._dashboard_hero_subtitle.set(view_model.hero_subtitle)
        for key, widgets in self._dashboard_card_values.items():
            card = view_model.cards.get(key)
            if card is None:
                continue
            widgets["label"].set(card.label)
            widgets["value"].set(card.value)
            widgets["detail"].set(card.detail)
            widgets["value_label"].configure(fg=self._dashboard_tone_color(card.tone))

    def _dashboard_tone_color(self, tone: str) -> str:
        return {
            "live": COLORS["accent"],
            "win": COLORS["accent"],
            "podium": COLORS["accent2"],
            "warning": COLORS["warning"],
            "danger": COLORS["danger"],
        }.get(tone, COLORS["text"])

    def _refresh_event_progress(self) -> None:
        if not hasattr(self, "_event_slot_widgets"):
            return

        slots = self._build_event_slots()
        recommendation_payload = read_recommendation()
        recommendation_events = recommendation_payload.get("events")
        if not isinstance(recommendation_events, list):
            recommendation_events = []
        rows = build_event_progress_rows(slots, recommendation_events=recommendation_events)
        rows_by_index = {row.index: row for row in rows}
        for slot in slots:
            widgets = self._event_slot_widgets[slot["index"] - 1]
            color = {
                "done": COLORS["done"],
                "current": COLORS["current"],
                "pending": COLORS["pending"],
            }[slot["state"]]
            accent = COLORS["muted"] if slot["state"] == "done" else COLORS["accent"] if slot["state"] == "current" else COLORS["accent2"]
            for key in ["frame", "number", "status", "circuit_label", "circuit", "start_label", "start", "finish_label", "finish", "car_label", "car", "time_label", "time"]:
                widgets[key].configure(bg=color)
            widgets["frame"].configure(highlightbackground=COLORS["accent"] if slot["state"] == "current" else COLORS["line"])
            widgets["number"].configure(text=f"{slot['index']}/3", fg=accent)
            widgets["status"].configure(text=slot["status"], fg=accent)
            row = rows_by_index.get(slot["index"])
            circuit_label = (
                row.map_panel.title
                if row is not None and row.map_panel.status == "found"
                else slot["circuit"]
            )
            widgets["circuit"].configure(text=circuit_label)
            widgets["start"].configure(text=slot["start"])
            widgets["finish"].configure(text=slot["finish"])
            widgets["car"].configure(text=slot["car"])
            widgets["time"].configure(text=slot["time"])
            if row is not None:
                self._refresh_event_slot_map(slot["index"] - 1, widgets, row.map_panel.image_path)

    def _refresh_event_slot_map(
        self,
        index: int,
        widgets: dict[str, tk.Widget],
        image_path: Path | None,
    ) -> None:
        if self._event_map_paths[index] == image_path:
            return
        self._event_map_paths[index] = image_path
        image_label = widgets["map_image"]
        if image_path is None:
            self._event_map_photos[index] = None
            image_label.configure(image="", text="")
            return
        try:
            image = Image.open(image_path).convert("RGBA")
            image.thumbnail(self._event_map_size, Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image)
            self._event_map_photos[index] = photo
            image_label.configure(image=photo, text="")
        except OSError:
            self._event_map_paths[index] = None
            self._event_map_photos[index] = None
            image_label.configure(image="", text="")

    def _build_event_slots(self) -> list[dict[str, Any]]:
        entries = _current_event_entries(self._entries)
        races = _current_event_races(self._races, entries)
        event_names: dict[int, str] = {}
        event_details: dict[int, dict[str, str]] = {}
        event_discipline = ""
        completed: set[int] = set()
        latest_event_number: int | None = None
        latest_entry_was_registration = False

        for entry in entries:
            _merge_event_plan(event_names, entry.raw.get("event_plan"))
            event_discipline = event_discipline or str(entry.raw.get("discipline") or "").strip()
            event_number = _as_int(entry.raw.get("event_number"))
            if event_number is not None:
                latest_event_number = event_number
            entry_circuit = _entry_circuit_name(entry)
            if event_number is not None and entry_circuit:
                event_names[event_number] = entry_circuit
            if event_number is not None:
                detail = event_details.setdefault(event_number, {})
                _set_if_present(detail, "circuit", entry_circuit)
                _set_if_present(detail, "start", _prefixed(entry.raw.get("starting_position"), "P"))
                _set_if_present(detail, "finish", _prefixed(entry.raw.get("position"), "P"))
                _set_if_present(detail, "car", entry.raw.get("car"))
                _set_if_present(detail, "time", entry.raw.get("total_time"))
                _set_if_present(detail, "best_lap", entry.raw.get("best_lap"))
                _set_if_present(detail, "discipline", entry.raw.get("discipline"))
            if entry.screen_type == "event_registration" and event_number is not None:
                latest_entry_was_registration = True
            elif entry.screen_type in {"starting_grid", "race_result", "championship_result"}:
                latest_entry_was_registration = False

        for race in races:
            _merge_event_plan(event_names, race.raw.get("event_plan"))
            event_discipline = event_discipline or str(race.raw.get("discipline") or "").strip()
            event_number = _as_int(race.raw.get("event_number"))
            if event_number is None:
                continue
            if race.raw.get("circuit_name"):
                event_names[event_number] = str(race.raw["circuit_name"])
            detail = event_details.setdefault(event_number, {})
            _set_if_present(detail, "circuit", race.raw.get("circuit_name"))
            _set_if_present(detail, "start", _prefixed(race.raw.get("starting_position"), "P"))
            _set_if_present(detail, "finish", _prefixed(race.raw.get("position"), "P"))
            _set_if_present(detail, "car", race.raw.get("car"))
            _set_if_present(detail, "time", race.raw.get("total_time"))
            _set_if_present(detail, "best_lap", race.raw.get("best_lap"))
            _set_if_present(detail, "discipline", race.raw.get("discipline"))
            if _as_int(race.raw.get("position")) is not None:
                completed.add(event_number)
                latest_event_number = event_number

        current = 1
        if latest_event_number is not None:
            if latest_entry_was_registration:
                current = latest_event_number
            elif latest_event_number in completed:
                current = min(latest_event_number + 1, 3)
            else:
                current = latest_event_number

        if latest_event_number is not None:
            completed.update(range(1, min(current, latest_event_number) + (0 if latest_entry_was_registration else 1)))

        slots: list[dict[str, Any]] = []
        for index in range(1, 4):
            state = "done" if index in completed and index != current else "current" if index == current else "pending"
            detail = event_details.get(index, {})
            status = {
                "done": "Completado",
                "current": "Actual",
                "pending": "Pendiente",
            }[state]
            slots.append({
                "index": index,
                "state": state,
                "status": status,
                "circuit": detail.get("circuit") or event_names.get(index) or ("Evento ya corrido" if state == "done" else "Esperando circuito"),
                "start": detail.get("start") or "-",
                "finish": detail.get("finish") or "-",
                "car": detail.get("car") or "-",
                "time": format_event_times(detail.get("time"), detail.get("best_lap")),
                "discipline": detail.get("discipline") or event_discipline,
            })
        return slots

    def _refresh_table(self) -> None:
        selected_path = self._selected_path()
        self._tree.delete(*self._tree.get_children())
        selected_item: str | None = None
        if not self._races:
            self._insert_tree_empty_state(
                self._tree,
                "Aún no hay carreras guardadas. Abre Forza y deja la captura activa para empezar.",
                len(self._tree["columns"]),
            )
            return
        for index, entry in enumerate(reversed(self._races)):
            item_id = f"race_{index}"
            self._tree.insert(
                "",
                "end",
                iid=item_id,
                tags=_history_row_tags(entry, index),
                values=(
                    entry.display_time,
                    _event_type_label(entry.get("event_type")),
                    _event_label(entry.raw.get("event_number"), entry.raw.get("event_total")),
                    _display_circuit_name(entry.get("circuit_name")),
                    entry.get("discipline"),
                    entry.get("car_class"),
                    entry.get("car"),
                    _prefixed(entry.raw.get("starting_position"), "P"),
                    _prefixed(entry.raw.get("position"), "P"),
                    _prefixed(entry.raw.get("championship_position"), "P"),
                    entry.get("total_time"),
                    entry.get("best_lap"),
                ),
            )
            if selected_path and str(entry.get("screenshot_path", "")) == str(selected_path):
                selected_item = item_id
        if selected_item:
            self._tree.selection_set(selected_item)

    def _refresh_summary(self) -> None:
        summary = summarize(self._filtered_summary_races())
        self._summary_vars["races"].set(str(summary.total_races))
        self._summary_vars["wins"].set(str(summary.wins))
        self._summary_vars["podiums"].set(str(summary.podiums))
        self._summary_vars["avg"].set("-" if summary.avg_position is None else f"{summary.avg_position:.2f}")
        self._history_summary_vars["races"].set(str(summary.total_races))
        self._history_summary_vars["wins"].set(f"{summary.wins} · {summary.win_rate:.0f}%")
        self._history_summary_vars["podiums"].set(f"{summary.podiums} · {summary.podium_rate:.0f}%")
        self._history_summary_vars["avg"].set("-" if summary.avg_position is None else f"P{summary.avg_position:.2f}")
        self._history_summary_vars["gain"].set(_format_position_gain(summary.avg_positions_gained))
        self._history_summary_vars["cars"].set(str(summary.unique_cars))

        best_car = summary.by_car[0] if summary.by_car else None
        self._history_insight_vars["favorite_car"].set(summary.favorite_car or "-")
        self._history_insight_vars["favorite_class"].set(summary.favorite_class or "-")
        self._history_insight_vars["best_car"].set(
            f"{best_car.name} · {best_car.car_class} · P{best_car.avg_position:.1f}"
            if best_car and best_car.avg_position is not None
            else "-"
        )
        self._history_insight_vars["comeback"].set(
            f"+{summary.best_comeback} puestos"
            if summary.best_comeback is not None and summary.best_comeback > 0
            else "Sin remontadas registradas"
        )
        if hasattr(self, "_car_summary_tree"):
            self._refresh_summary_tree(self._car_summary_tree, summary.by_car, show_favorite=False)
        if hasattr(self, "_circuit_summary_tree"):
            self._refresh_summary_tree(self._circuit_summary_tree, summary.by_circuit, show_favorite=True)

    def _filtered_summary_races(self) -> list[RaceEntry]:
        discipline_filter = self._summary_discipline_filter.get()
        class_filter = self._summary_class_filter.get()
        races = self._races
        if discipline_filter != "all":
            races = [race for race in races if _discipline_matches(race.raw.get("discipline"), discipline_filter)]
        if class_filter != "all":
            races = [race for race in races if str(race.get("car_class")) == class_filter]
        return races

    def _refresh_summary_tree(self, tree: ttk.Treeview, groups: list[Any], *, show_favorite: bool) -> None:
        tree.delete(*tree.get_children())
        if not groups:
            self._insert_tree_empty_state(tree, "No hay carreras para estos filtros.", len(tree["columns"]))
            return
        for index, group in enumerate(groups):
            values = [
                _display_circuit_name(group.name) if show_favorite else group.name,
                group.car_class,
                group.total_races,
                f"{group.win_rate:.0f}%",
                f"{group.podium_rate:.0f}%",
                "-" if group.avg_position is None else f"{group.avg_position:.2f}",
                _format_position_gain(group.avg_positions_gained),
            ]
            if show_favorite:
                values.append(group.favorite_car if group.favorite_car else "-")
            tree.insert(
                "",
                "end",
                iid=f"{id(tree)}-{index}",
                tags=("even" if index % 2 == 0 else "odd",),
                values=values,
            )

    def _on_select(self, _event: Any = None) -> None:
        entry = self._selected_entry()
        self._detail.delete("1.0", "end")
        if entry is None:
            return
        lines = [
            tr("history.detail.date", value=entry.display_time),
            tr("history.detail.type", value=_event_type_label(entry.get("event_type"))),
            tr(
                "history.detail.event",
                value=_event_label(entry.raw.get("event_number"), entry.raw.get("event_total")),
            ),
            tr("history.detail.circuit", value=_display_circuit_name(entry.get("circuit_name"))),
            tr("history.detail.discipline", value=entry.get("discipline")),
            tr("history.detail.start", value=_prefixed(entry.raw.get("starting_position"), "P")),
            tr("history.detail.finish", value=_prefixed(entry.raw.get("position"), "P")),
            tr(
                "history.detail.event_position",
                value=_prefixed(entry.raw.get("championship_position"), "P"),
            ),
            tr("history.detail.car", value=entry.get("car")),
            tr("history.detail.class", value=entry.get("car_class")),
            tr("history.detail.time", value=entry.get("total_time")),
            tr("history.detail.best_lap", value=entry.get("best_lap")),
            tr("history.detail.row_confidence", value=entry.get("row_detection_confidence")),
            "",
            tr("history.detail.grid", value=_entry_path(entry.starting_grid)),
            tr("history.detail.result", value=_entry_path(entry.race_result)),
            tr("history.detail.championship", value=_entry_path(entry.championship_result)),
            tr("history.detail.class_crop", value=entry.get("class_crop_path")),
            "",
            tr("history.detail.raw_ocr"),
            str(entry.get("raw_row_text")),
        ]
        self._detail.insert("1.0", "\n".join(lines))

    def _open_selected_capture(self, _event: Any = None) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        relative_path = Path(str(entry.get("screenshot_path", "")))
        path = relative_path if relative_path.is_absolute() else DATA_ROOT / relative_path
        if not path.exists() and not relative_path.is_absolute():
            path = PROJECT_ROOT / relative_path
        if path.exists():
            os.startfile(path)

    def _selected_entry(self) -> RaceEntry | None:
        selected = self._tree.selection()
        if not selected:
            return None
        values = self._tree.item(selected[0], "values")
        if not values:
            return None
        display_time = values[0]
        for entry in self._races:
            if entry.display_time == display_time:
                return entry
        return None

    def _selected_path(self) -> str | None:
        entry = self._selected_entry()
        if entry is None:
            return None
        return str(entry.get("screenshot_path", ""))

    def _latest_meaningful_entry(self) -> RaceEntry | None:
        return self._races[-1] if self._races else None

    def _auto_refresh(self) -> None:
        self.refresh()
        self.after(AUTO_REFRESH_MS, self._auto_refresh)

    def _start_session_recording(self) -> None:
        if self._recording_mode.get() != "session":
            self._settings_feedback.set("Selecciona Sesión completa y guarda los ajustes.")
            return
        if not _is_game_process_running():
            self._recording_state.set("Abre Forza antes de grabar")
            self._show_page("videos")
            return
        if self._capture_process is None:
            self._start_capture()
        send_recording_command("start_session")
        self._recording_state.set("Iniciando sesión completa…")

    def _stop_manual_recording(self) -> None:
        send_recording_command("stop")
        self._recording_state.set("Finalizando vídeo…")

    def _refresh_recording_status(self) -> None:
        status = read_recording_status()
        recording = bool(status.get("recording"))
        mode = str(status.get("mode") or "idle")
        if recording:
            label = "Grabando sesión completa" if mode == "session" else "Grabando carrera automática"
            self._recording_state.set(f"● {label} · {self._settings.recording.framerate} FPS")
            if hasattr(self, "_manual_stop_button"):
                self._manual_stop_button.configure(state="normal")
            if hasattr(self, "_session_record_button"):
                self._session_record_button.configure(state="disabled")
        else:
            message = str(status.get("message") or "Grabación detenida")
            self._recording_state.set(message)
            if hasattr(self, "_manual_stop_button"):
                self._manual_stop_button.configure(state="disabled")
            if hasattr(self, "_session_record_button"):
                self._session_record_button.configure(
                    state="normal" if self._recording_mode.get() == "session" else "disabled"
                )

    def _toggle_capture(self) -> None:
        if self._capture_process is None:
            self._auto_capture_suppressed = False
            self._start_capture()
        else:
            self._stop_capture(manual_stop=True)

    def _start_capture(self) -> None:
        if not self._has_valid_player_name():
            self._capture_status.set("Configura jugador antes de capturar")
            return
        self._save_player_profile(show_status=False)
        if self._capture_process is not None and self._capture_process.poll() is None:
            return
        creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        self._capture_process = subprocess.Popen(
            _capture_command(self._config_path),
            cwd=PROJECT_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        self._capture_started_monotonic = time.monotonic()
        self._capture_active.set(True)
        self._capture_status.set("Iniciando captura...")
        self._capture_button.configure(text="Detener captura", style="Danger.TButton")

    def _save_player_profile(self, show_status: bool = True) -> None:
        player_name = self._player_name.get().strip()
        if not player_name:
            self._capture_status.set("Falta jugador")
            return
        if player_name.strip().lower() in PLAYER_PLACEHOLDERS:
            self._capture_status.set("Gamertag no valido")
            return
        data = self._load_editable_config()
        app = data.setdefault("app", {})
        if not isinstance(app, dict):
            app = {}
            data["app"] = app
        app["player_name"] = player_name
        self._write_local_config(data)
        self.refresh(force=True)
        if show_status:
            self._capture_status.set(
                tr("profile.saved_restart") if self._capture_process is not None else tr("profile.saved")
            )

    def _load_editable_config(self) -> dict[str, Any]:
        local_config = DATA_ROOT / "config" / "tracker.local.yaml"
        return load_user_config(local_path=local_config, default_path=DEFAULT_CONFIG)

    def _write_local_config(self, data: dict[str, Any]) -> None:
        local_config = DATA_ROOT / "config" / "tracker.local.yaml"
        self._config_path = write_user_config(data, local_path=local_config)

    def _has_valid_player_name(self) -> bool:
        return self._player_name.get().strip().lower() not in PLAYER_PLACEHOLDERS

    def _player_display_text(self) -> str:
        player_name = self._player_name.get().strip()
        return f"Jugador: {player_name}" if player_name.lower() not in PLAYER_PLACEHOLDERS else "Jugador sin configurar"

    def _read_player_name_from_game(self) -> str | None:
        try:
            monitor_index = 0 if self._settings.capture.auto_detect_monitors else self._settings.capture.monitor_index
            capture = create_capture_backend(self._settings.capture.backend, monitor_index)
            ocr = TesseractOcr(self._settings.ocr.enabled, self._settings.ocr.tesseract_cmd)
            detected = None
            for frame in capture.grab_all():
                image = frame.image
                height, width = image.shape[:2]
                crop = image[int(height * 0.05) : int(height * 0.20), int(width * 0.70) : int(width * 0.90)]
                detected = _extract_player_name_from_menu_text(ocr.read_line(crop))
                if detected:
                    break
        except Exception:
            return None
        return detected

    def _monitor_game_process(self) -> None:
        if self._is_exiting:
            return
        game_running = _is_game_process_running()
        if not game_running:
            self._auto_capture_suppressed = False
        self._game_was_running = game_running
        if game_running:
            self._game_status.set(
                "Forza detectado: captura pausada"
                if self._auto_capture_suppressed and self._capture_process is None
                else "Forza detectado: captura automatica activa"
            )
            capture_running = self._capture_process is not None
            if should_auto_start_capture(
                game_running=game_running,
                capture_running=capture_running,
                has_valid_player_name=self._has_valid_player_name(),
                auto_capture_suppressed=self._auto_capture_suppressed,
            ):
                self._start_capture()
            elif self._capture_process is None and self._auto_capture_suppressed:
                self._capture_status.set("Captura pausada manualmente")
            elif self._capture_process is None:
                self._capture_status.set("Configura jugador para activar captura")
        else:
            self._game_status.set("Esperando Forza Horizon")
            if self._capture_process is not None:
                self._stop_capture()
        self.after(GAME_POLL_MS, self._monitor_game_process)

    def _stop_capture(self, manual_stop: bool = False) -> None:
        if manual_stop and _is_game_process_running():
            self._auto_capture_suppressed = True
        process = self._capture_process
        if process is not None and process.poll() is None:
            try:
                send_recording_command("shutdown")
            except OSError:
                pass
            try:
                process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                _terminate_process_tree(process)
                CONTROL_PATH.unlink(missing_ok=True)
        self._capture_process = None
        self._capture_started_monotonic = None
        self._capture_active.set(False)
        self._capture_status.set("Captura detenida")
        self._capture_button.configure(text="Iniciar captura", style="Accent.TButton")

    def _poll_capture_process(self) -> None:
        process = self._capture_process
        if process is not None and process.poll() is not None:
            exit_code = process.returncode
            self._capture_process = None
            self._capture_started_monotonic = None
            self._capture_active.set(False)
            self._capture_status.set(f"Captura interrumpida (código {exit_code})")
            self._capture_button.configure(text="Iniciar captura", style="Accent.TButton")
            self._report_dashboard_error(
                "CaptureProcessExited",
                f"El proceso de captura terminó con código {exit_code}.",
                {"exit_code": exit_code},
            )
            if self._game_was_running:
                self.after(1500, self._restart_capture_after_failure)
        elif process is not None:
            self._check_capture_heartbeat()
        self.after(1000, self._poll_capture_process)

    def _check_capture_heartbeat(self) -> None:
        started = self._capture_started_monotonic
        if started is None or time.monotonic() - started < CAPTURE_STARTUP_GRACE_SECONDS:
            return
        status = read_status(STATUS_PATH)
        updated_at = _parse_iso_datetime(status.get("updated_at"))
        if updated_at is None:
            self._capture_status.set("Sin señal del capturador")
            self._restart_capture_after_failure()
            return
        age = (datetime.now(timezone.utc) - updated_at).total_seconds()
        if age > CAPTURE_HEARTBEAT_STALE_SECONDS:
            self._capture_status.set(f"Captura bloqueada ({int(age)} s sin señal)")
            self._report_dashboard_error(
                "CaptureHeartbeatStale",
                f"El capturador lleva {int(age)} segundos sin actualizar su estado.",
                {"heartbeat_age_seconds": int(age)},
            )
            self._restart_capture_after_failure()
            return

        frames = status.get("frames_captured", 0)
        self._capture_status.set(f"Capturando · {frames} fotogramas comprobados")

    def _restart_capture_after_failure(self) -> None:
        if self._is_exiting or not self._game_was_running:
            return
        now = time.monotonic()
        while self._watchdog_restarts and now - self._watchdog_restarts[0] > WATCHDOG_RESTART_WINDOW_SECONDS:
            self._watchdog_restarts.popleft()
        if len(self._watchdog_restarts) >= WATCHDOG_MAX_RESTARTS:
            self._capture_status.set("Captura detenida: revisa Diagnóstico")
            return
        self._watchdog_restarts.append(now)
        if self._capture_process is not None:
            self._stop_capture()
        self._capture_status.set("Reiniciando captura automáticamente...")
        self.after(1000, self._start_capture)

    def _run_capture_diagnostic(self) -> None:
        try:
            monitor_index = 0 if self._settings.capture.auto_detect_monitors else self._settings.capture.monitor_index
            capture = create_capture_backend(self._settings.capture.backend, monitor_index)
            summaries = []
            for frame in capture.grab_all():
                height, width = frame.image.shape[:2]
                brightness = float(frame.image.mean())
                if width >= 640 and height >= 360 and brightness >= 1.0:
                    summaries.append(f"M{frame.monitor_index}: {width}x{height}")
            if not summaries:
                raise RuntimeError("No se pudo obtener una captura válida de ningún monitor.")
            self._capture_status.set(f"Diagnóstico correcto · {' · '.join(summaries)}")
        except Exception as error:
            message = sanitize_error_message(str(error))
            self._capture_status.set(f"Error de diagnóstico: {message[:80]}")
            self._report_dashboard_error(
                type(error).__name__,
                message,
                {"phase": "manual_diagnostic"},
            )

    def _render_release_notes(self) -> None:
        snapshot = self._release_snapshot or {}
        version = snapshot.get("version", "")
        available = bool(version and is_newer_version(version, APP_VERSION))
        self._news_page.show_release(
            installed_version=APP_VERSION,
            installed_notes=installed_release_notes(current_language()),
            available_version=version if available else "",
            available_notes=snapshot.get("notes", "") if available else "",
            checked_at=snapshot.get("checked_at", ""),
            cached=self._release_snapshot_cached,
        )

    def _set_update_status(self, text: str, *, progress: float | None = None) -> None:
        self._news_page.set_status(text, busy=self._update_busy,
            can_install=self._available_update is not None, progress=progress)
        self._update_button.configure(state="disabled" if self._update_busy else "normal")

    def _check_for_updates(self) -> None:
        self._show_page("news")
        self._begin_update_check(silent=False)

    def _check_for_updates_silently(self) -> None:
        self._begin_update_check(silent=True)

    def _begin_update_check(self, silent: bool = False) -> None:
        if self._is_exiting or self._update_busy:
            return
        self._update_busy = True
        self._set_update_status(tr("desktop.update.checking"))
        try:
            settings = load_settings(self._config_path)
            threading.Thread(target=self._check_for_updates_worker,
                args=(settings.api.base_url, settings.api.timeout_seconds, silent), daemon=True).start()
        except Exception as error:
            self._on_update_check_error(error)

    def _check_for_updates_worker(self, base_url: str, timeout_seconds: int, silent: bool) -> None:
        try:
            update = check_for_update(base_url, max(10, timeout_seconds))
        except Exception as error:
            self._update_events.put(("check_error", error))
            return
        self._update_events.put(("checked", (update, silent)))

    def _drain_update_events(self) -> None:
        if self._is_exiting:
            return
        for _ in range(32):
            try:
                kind, payload = self._update_events.get_nowait()
            except queue.Empty:
                break
            if kind == "checked":
                self._on_update_check_complete(*payload)
            elif kind == "check_error":
                self._on_update_check_error(payload)
            elif kind == "progress":
                text, percent = payload
                self._set_update_status(text, progress=percent)
            elif kind == "download_error":
                self._on_update_download_error(payload)
            elif kind == "install":
                self._install_downloaded_update(*payload)
        self.after(100, self._drain_update_events)

    def _on_update_check_complete(self, update: UpdateInfo | None, silent: bool = False) -> None:
        if self._is_exiting:
            return
        self._update_busy = False
        self._available_update = update
        notes = update.release_notes if update else installed_release_notes(current_language())
        self._release_snapshot = {
            "version": update.version if update else APP_VERSION, "notes": notes,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        self._release_snapshot_cached = False
        cache_failed = False
        try:
            cache_release(self._news_cache_path, version=self._release_snapshot["version"], notes=notes)
        except (OSError, ValueError):
            cache_failed = True
        self._render_release_notes()
        if update is None:
            self._update_button.pack_forget()
            status = tr("desktop.update.current", version=APP_VERSION)
        else:
            self._update_button.configure(text=tr("desktop.update.available", version=update.version), style="Quiet.TButton")
            if not self._update_button.winfo_manager():
                self._update_button.pack(side="right", padx=(0, 10))
            status = tr("desktop.update.available", version=update.version)
        if cache_failed:
            status += " · " + tr("desktop.update.cache_failed")
        self._set_update_status(status)

    def _accept_available_update(self) -> None:
        update = self._available_update
        if update is None or self._update_busy or self._is_exiting:
            return
        # A modal confirmation runs a nested event loop: guard against repeated actions.
        self._update_busy = True
        self._set_update_status(tr("desktop.update.confirm"))
        try:
            accepted = messagebox.askyesno("Actualización disponible",
                tr("update.available_prompt", available=update.version, current=APP_VERSION,
                   notes=f"\n\n{update.release_notes}" if update.release_notes else ""), parent=self)
        finally:
            self._update_busy = False
        if self._is_exiting:
            return
        if accepted:
            self._start_update_download(update)
        else:
            self._set_update_status(tr("desktop.update.available", version=update.version))

    def _on_update_check_error(self, error: Exception) -> None:
        if self._is_exiting:
            return
        self._update_busy = False
        self._available_update = None
        self._release_snapshot_cached = True
        self._update_button.pack_forget()
        self._render_release_notes()
        self._set_update_status(tr("desktop.update.check_failed"))

    def _start_update_download(self, update: UpdateInfo) -> None:
        if self._update_busy or self._is_exiting:
            return
        self._update_busy = True
        self._show_page("news")
        self._set_update_status(tr("desktop.update.downloading", version=update.version))
        try:
            threading.Thread(target=self._download_update_worker, args=(update,), daemon=True).start()
        except Exception as error:
            self._on_update_download_error(error)

    def _download_update_worker(self, update: UpdateInfo) -> None:
        last_percent = -1
        last_megabytes = -1
        def progress(downloaded: int, total: int | None) -> None:
            nonlocal last_percent, last_megabytes
            percent = min(100, int(downloaded * 100 / total)) if total else None
            megabytes = downloaded // 1_048_576
            if percent == last_percent and megabytes == last_megabytes:
                return
            last_percent, last_megabytes = percent, megabytes
            text = tr("desktop.update.downloading", version=update.version)
            text += f" · {percent}%" if percent is not None else f" · {megabytes} MB"
            self._update_events.put(("progress", (text, percent)))
        try:
            zip_path = download_update(update, progress)
        except Exception as error:
            self._update_events.put(("download_error", error))
            return
        self._update_events.put(("install", (update, zip_path)))

    def _on_update_download_error(self, error: Exception) -> None:
        if self._is_exiting:
            return
        self._update_busy = False
        self._set_update_status(tr("desktop.update.download_failed"))

    def _install_downloaded_update(self, update: UpdateInfo, zip_path: Path) -> None:
        if self._is_exiting:
            return
        try:
            launch_installer(zip_path)
        except Exception as error:
            self._on_update_download_error(error)
            return
        self._set_update_status(tr("desktop.update.installing", version=update.version), progress=100)
        self.after(250, self._exit_app)

    def _report_dashboard_error(
        self,
        error_type: str,
        message: str,
        context: dict[str, object] | None = None,
    ) -> None:
        signature = f"{error_type}:{message}"
        if signature == self._last_reported_error:
            return
        self._last_reported_error = signature
        settings = load_settings(self._config_path)
        if not settings.api.diagnostics_enabled:
            return
        client = ApiClient(settings.api.base_url, settings.api.timeout_seconds, settings.api.api_key)
        payload = {
            "player": self._player_name.get().strip() or "unknown",
            "app_version": APP_VERSION,
            "component": "dashboard_watchdog",
            "error_type": error_type,
            "message": sanitize_error_message(message),
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "context": context or {},
        }
        threading.Thread(target=_send_error_report, args=(client, payload), daemon=True).start()

    def _start_tray_icon(self) -> None:
        if self._tray_icon is not None:
            return
        icon = pystray.Icon(
            "fhstats-tracker",
            _create_tray_image(),
            "FHStats Tracker",
            pystray.Menu(
                pystray.MenuItem(tr("action.open_tracker"), lambda _icon, _item: self.after(0, self._show_from_tray)),
                pystray.MenuItem(tr("action.start_capture"), lambda _icon, _item: self.after(0, self._start_capture)),
                pystray.MenuItem(tr("action.stop_capture"), lambda _icon, _item: self.after(0, self._stop_capture)),
                pystray.MenuItem(tr("action.exit"), lambda _icon, _item: self.after(0, self._exit_app)),
            ),
        )
        self._tray_icon = icon
        threading.Thread(target=icon.run, daemon=True).start()

    def _hide_to_tray(self) -> None:
        self.withdraw()
        self._capture_status.set("Minimizado en bandeja")

    def _show_from_tray(self) -> None:
        self._ensure_window_visible()

    def _ensure_window_visible(self) -> None:
        if self._is_exiting:
            return
        self.state("normal")
        self.attributes("-alpha", 1.0)
        self.deiconify()
        self.update_idletasks()
        _apply_windows_titlebar(self)
        self.lift()
        self.attributes("-topmost", True)
        self.after(100, lambda: self.attributes("-topmost", False))
        self.focus_force()

    def _exit_app(self) -> None:
        if self._is_exiting:
            return
        self._is_exiting = True
        self._rivals_runtime.stop()
        self._release_video_player()
        self._stop_capture()
        if self._tray_icon is not None:
            self._tray_icon.stop()
            self._tray_icon = None
        self._overlay_runtime.stop()
        super().destroy()

    def destroy(self) -> None:
        self._exit_app()


def _event_label(event_number: Any, event_total: Any) -> str:
    if event_number is None or event_number == "":
        return "-"
    if event_total is None or event_total == "":
        return str(event_number)
    return f"{event_number}/{event_total}"


def _history_row_tags(entry: RaceEntry, index: int) -> tuple[str, ...]:
    tags = ["even" if index % 2 == 0 else "odd"]
    position = _as_int(entry.raw.get("position"))
    if position == 1:
        tags.append("win")
    elif position is not None and position <= 3:
        tags.append("podium")
    return tuple(tags)


def _discipline_matches(value: Any, selected: str) -> bool:
    normalized = str(value or "").lower()
    aliases = {
        "road": ("carretera", "road"),
        "dirt": ("tierra", "dirt"),
        "cross_country": ("campo", "cross"),
        "street": ("callej", "street"),
    }
    return any(alias in normalized for alias in aliases.get(selected, (selected,)))


def _event_type_label(value: Any) -> str:
    labels = {
        "online_matchmaking": tr("history.event_type.online"),
        "matchmaking_search": tr("history.event_type.searching"),
        "matchmaking_found": tr("history.event_type.found"),
    }
    if value is None or value == "" or value == "-":
        return "-"
    return labels.get(str(value), str(value))


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _merge_event_plan(target: dict[int, str], value: Any) -> None:
    if not isinstance(value, list):
        return
    for index, name in enumerate(value[:3], start=1):
        cleaned = _clean_circuit_name(name)
        if _is_real_circuit_name(cleaned):
            target.setdefault(index, cleaned)


def _entry_circuit_name(entry: HistoryEntry) -> str | None:
    if entry.screen_type in {"event_registration", "matchmaking_search", "matchmaking_found"}:
        return None
    value = entry.raw.get("circuit_name")
    return _clean_circuit_name(value) if _is_real_circuit_name(value) else None


def _is_real_circuit_name(value: Any) -> bool:
    if value is None or value == "" or value == "-":
        return False
    normalized = _clean_circuit_name(value).lower()
    blocked = ["inscripcion al evento", "inscripción al evento", "accediendo", "horizon play"]
    return not any(text in normalized for text in blocked)


def _clean_circuit_name(value: Any) -> str:
    return str(value).strip().lstrip(" ,.;:!-")


def _display_circuit_name(value: Any) -> str:
    cleaned = _clean_circuit_name(value)
    if not cleaned or cleaned == "-":
        return "-"
    panel = build_track_map_panel(cleaned)
    return panel.title if panel.status == "found" else cleaned


def _current_event_entries(entries: list[HistoryEntry]) -> list[HistoryEntry]:
    start_index = 0
    for index, entry in enumerate(entries):
        if entry.screen_type == "event_registration" and isinstance(entry.raw.get("event_plan"), list):
            start_index = index
    return entries[start_index:]


def _current_event_races(races: list[RaceEntry], entries: list[HistoryEntry]) -> list[RaceEntry]:
    if not entries:
        return races
    first_timestamp = entries[0].timestamp
    if first_timestamp is None:
        return races
    return [race for race in races if race.timestamp is not None and race.timestamp >= first_timestamp]


def _set_if_present(target: dict[str, str], key: str, value: Any) -> None:
    if value is None or value == "" or value == "-":
        return
    target[key] = str(value)


def _prefixed(value: Any, prefix: str) -> str:
    if value is None or value == "":
        return "-"
    return f"{prefix}{value}"


def _format_position_gain(value: float | None) -> str:
    if value is None:
        return "-"
    if abs(value) < 0.05:
        return "0.0"
    return f"{value:+.1f}"


def _progress(value: Any) -> str:
    if value is None or value == "":
        return "-"
    return f"{value}%"


def _entry_path(entry: HistoryEntry | RaceEntry | None) -> str:
    if entry is None:
        return "-"
    return str(entry.get("screenshot_path"))


def _capture_command(config_path: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve()), "--capture", "--config", str(config_path)]
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    python_exe = pythonw if pythonw.exists() else Path(sys.executable)
    return [str(python_exe), "-m", "forza_tracker.main", "--config", str(config_path)]


def _terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=6,
                creationflags=creationflags,
            )
            process.wait(timeout=2)
            return
        except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
            pass
    try:
        process.kill()
        process.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _send_error_report(client: ApiClient, payload: dict[str, Any]) -> None:
    try:
        client.report_error(payload)
    except Exception:
        return


def _is_game_process_running() -> bool:
    creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    try:
        result = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            timeout=2,
            creationflags=creationflags,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    output = result.stdout.lower()
    return any(name in output for name in GAME_PROCESS_NAMES)


def _create_tray_image() -> Image.Image:
    if APP_ICON.exists():
        return Image.open(APP_ICON).resize((64, 64))
    image = Image.new("RGBA", (64, 64), COLORS["accent"])
    draw = ImageDraw.Draw(image)
    draw.text((16, 24), "FHT", fill="#05080a")
    return image


def _extract_player_name_from_menu_text(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = " ".join(text.replace("|", " ").replace("\n", " ").split())
    cleaned = re.sub(r"\bGPU\b\s*\d{1,3}\s*:?\s*\d{1,3}\s*%?", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bGPU\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bCR\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:D|C|B|A|S1|S2|R|X)\s*\d{3}\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b\d{1,3}(?:[ .]\d{3})+\b", " ", cleaned)
    tokens = [token.strip(" ,.;:![](){}<>|") for token in cleaned.split()]
    ignored = {"cr", "gpu", "fps", "level", "nivel", "menu", "horizon"}
    candidates: list[str] = []
    for token in tokens:
        normalized = re.sub(r"[^A-Za-z0-9_#-]", "", token)
        if len(normalized) < 3 or len(normalized) > 16:
            continue
        if normalized.lower() in ignored:
            continue
        if normalized.isdigit():
            continue
        if not re.search(r"[A-Za-z]", normalized):
            continue
        candidates.append(normalized)
    if not candidates:
        return None
    return max(candidates, key=lambda value: (any(char.isdigit() for char in value), len(value)))


def _resolve_config_path() -> Path:
    local_config = DATA_ROOT / "config" / "tracker.local.yaml"
    return local_config if local_config.exists() else DEFAULT_CONFIG


def _acquire_single_instance_lock() -> bool:
    global _INSTANCE_LOCK_FILE, _INSTANCE_MUTEX_HANDLE
    if not _acquire_file_instance_lock():
        return False

    if os.name != "nt":
        return True

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, False, "Local\\FHStatsTrackerDashboard")
    if not handle:
        return True

    if ctypes.get_last_error() == 183:
        kernel32.CloseHandle(handle)
        return False

    _INSTANCE_MUTEX_HANDLE = handle
    return True


def main() -> None:
    if "--restart-wait" in sys.argv:
        sys.argv.remove("--restart-wait")
        time.sleep(1.5)
    if not _acquire_single_instance_lock():
        return
    enable_per_monitor_dpi_awareness()
    ensure_user_data_layout()
    settings = load_settings(_resolve_config_path())
    history_path = settings.history.path if settings.history.path.is_absolute() else DATA_ROOT / settings.history.path
    app = TrackerDashboard(history_path)
    app.mainloop()


if __name__ == "__main__":
    if "--capture" in sys.argv:
        sys.argv.remove("--capture")
        from forza_tracker.main import main as capture_main

        capture_main()
    else:
        main()
