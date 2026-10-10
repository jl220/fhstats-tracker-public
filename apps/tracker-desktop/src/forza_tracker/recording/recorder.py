from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from forza_tracker.config.settings import RecordingSettings
from forza_tracker.paths import resolve_data_path, resolve_resource_path

logger = logging.getLogger(__name__)


def safe_filename_component(value: str, max_length: int = 80) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", value)
    cleaned = re.sub(r"\s+", "-", cleaned).strip(" .-_")
    cleaned = re.sub(r"-+", "-", cleaned)
    return cleaned[:max_length].rstrip(" .-_") or "Coche-desconocido"


def recording_stem(
    car_name: str | None,
    car_class: str | None,
    timestamp: str,
    *,
    finish_position: int | None = None,
) -> str:
    _ = finish_position
    car_slug = safe_filename_component(car_name or "Coche desconocido")
    class_slug = safe_filename_component(car_class or "Clase desconocida")
    return f"{timestamp}_{car_slug}_{class_slug}"


def _final_recording_path(
    current_path: Path,
    timestamp: str | None,
    final_car_name: str | None,
    final_car_class: str | None,
    finish_position: int | None,
) -> Path:
    has_final_metadata = any(
        value not in {None, ""}
        for value in (final_car_name, final_car_class, finish_position)
    )
    if not timestamp or not has_final_metadata:
        return current_path

    stem = recording_stem(
        final_car_name,
        final_car_class,
        timestamp,
        finish_position=finish_position,
    )
    candidate = current_path.with_name(f"{stem}{current_path.suffix}")
    if candidate == current_path or not candidate.exists():
        return candidate

    for index in range(2, 1000):
        numbered = current_path.with_name(f"{stem}-{index}{current_path.suffix}")
        if not numbered.exists():
            return numbered
    return current_path.with_name(f"{stem}-{uuid.uuid4().hex[:8]}{current_path.suffix}")


@dataclass(frozen=True)
class EncoderChoice:
    name: str
    ffmpeg_encoder: str


@dataclass(frozen=True)
class RecordingSession:
    process: subprocess.Popen[str]
    output_path: Path
    video_path: Path
    audio_process: subprocess.Popen[str] | None
    audio_stop_event: int | None
    audio_path: Path | None
    audio_started_at: float | None
    video_started_at: float | None
    timestamp: str | None = None


class MatchRecorder:
    """Records one online event without making recording failures fatal to OCR."""

    def __init__(self, settings: RecordingSettings) -> None:
        self._settings = settings
        self._process: subprocess.Popen[str] | None = None
        self._audio_process: subprocess.Popen[str] | None = None
        self._audio_stop_event: int | None = None
        self._audio_path: Path | None = None
        self._audio_started_at: float | None = None
        self._video_started_at: float | None = None
        self._timestamp: str | None = None
        self._output_path: Path | None = None
        self._video_path: Path | None = None
        self._finalizer_threads: list[threading.Thread] = []
        self._lock = threading.Lock()

    @property
    def is_recording(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(
        self,
        monitor_index: int = 0,
        car_name: str | None = None,
        car_class: str | None = None,
        force: bool = False,
    ) -> Path | None:
        if not self._settings.enabled and not force:
            return None
        with self._lock:
            if self.is_recording:
                return self._output_path

            ffmpeg = _resolve_runtime_path(self._settings.ffmpeg_path)
            if not ffmpeg.exists():
                logger.error("Recording disabled for this event: FFmpeg not found at %s", ffmpeg)
                return None

            output_dir = resolve_data_path(self._settings.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            enforce_storage_limit(output_dir, self._settings.max_storage_gb)
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
            stem = recording_stem(car_name, car_class, timestamp)
            output_path = output_dir / f"{stem}.mkv"
            video_path = output_dir / f".{stem}.video.mkv"
            audio_path = output_dir / f".{stem}.audio.wav"
            self._start_game_audio(audio_path)
            encoder = detect_encoder(ffmpeg)
            command = build_recording_command(
                ffmpeg=ffmpeg,
                output_path=video_path,
                settings=self._settings,
                encoder=encoder,
                monitor_index=monitor_index,
            )
            creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
            try:
                self._process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=creationflags,
                )
                self._video_started_at = time.monotonic()
                time.sleep(0.35)
                if self._process.poll() is not None:
                    error = self._process.stderr.read()[-1200:] if self._process.stderr else ""
                    logger.error("Could not start match recording with %s: %s", encoder.name, error.strip())
                    self._stop_game_audio()
                    self._process = None
                    return None
            except OSError:
                logger.exception("Could not launch FFmpeg recorder")
                self._process = None
                return None

            self._output_path = output_path
            self._video_path = video_path
            self._timestamp = timestamp
            logger.info("Match recording started encoder=%s path=%s", encoder.name, output_path)
            return output_path

    def stop(
        self,
        final_car_name: str | None = None,
        final_car_class: str | None = None,
        finish_position: int | None = None,
        wait: bool = False,
    ) -> Path | None:
        with self._lock:
            process = self._process
            output_path = self._output_path
            video_path = self._video_path
            if process is None or output_path is None or video_path is None:
                threads = list(self._finalizer_threads) if wait else []
            else:
                final_output_path = _final_recording_path(
                    output_path,
                    self._timestamp,
                    final_car_name,
                    final_car_class,
                    finish_position,
                )
                output_path = final_output_path
                session = RecordingSession(
                    process=process,
                    output_path=final_output_path,
                    video_path=video_path,
                    audio_process=self._audio_process,
                    audio_stop_event=self._audio_stop_event,
                    audio_path=self._audio_path,
                    audio_started_at=self._audio_started_at,
                    video_started_at=self._video_started_at,
                    timestamp=self._timestamp,
                )
                self._process = None
                self._output_path = None
                self._video_path = None
                self._audio_process = None
                self._audio_stop_event = None
                self._audio_path = None
                self._audio_started_at = None
                self._video_started_at = None
                self._timestamp = None
                thread = threading.Thread(
                    target=self._finish_session,
                    args=(session,),
                    name="fhstats-recording-finalizer",
                    daemon=True,
                )
                self._finalizer_threads.append(thread)
                thread.start()
                threads = list(self._finalizer_threads) if wait else []

        for thread in threads:
            thread.join(timeout=70)
        return output_path

    def _finish_session(self, session: RecordingSession) -> None:
        process = session.process
        output_path: Path | None = session.output_path
        try:
            try:
                if process.poll() is None and process.stdin is not None:
                    process.stdin.write("q\n")
                    process.stdin.flush()
                    process.wait(timeout=12)
            except (OSError, subprocess.TimeoutExpired):
                logger.warning("Recorder did not stop cleanly; terminating it")
                process.terminate()
                try:
                    process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    process.kill()
            self._stop_game_audio_session(session.audio_process, session.audio_stop_event)

            output_dir = resolve_data_path(self._settings.output_dir)
            if session.video_path.exists() and session.video_path.stat().st_size > 0:
                output_path = self._finalize_recording(session)
            if output_path and output_path.exists() and output_path.stat().st_size > 0:
                logger.info("Match recording finished: %s", output_path)
            else:
                logger.error("Recorder stopped without producing a usable video")
            enforce_storage_limit(output_dir, self._settings.max_storage_gb)
        except Exception:
            logger.exception("Could not finalize match recording")
        finally:
            current = threading.current_thread()
            with self._lock:
                self._finalizer_threads = [
                    thread for thread in self._finalizer_threads if thread is not current
                ]

    def _start_game_audio(self, audio_path: Path) -> None:
        if not self._settings.capture_audio or os.name != "nt":
            return
        helper = _resolve_runtime_path(self._settings.audio_helper_path)
        game_pid = _find_game_pid()
        if not helper.exists() or game_pid is None:
            logger.warning(
                "Game-only audio is unavailable; recording video without audio (helper=%s pid=%s)",
                helper.exists(),
                game_pid,
            )
            return
        event_name = f"Local\\FHStatsAudioStop-{uuid.uuid4()}"
        handle = _create_named_event(event_name)
        if handle is None:
            return
        creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        try:
            self._audio_process = subprocess.Popen(
                [str(helper), str(game_pid), "includetree", str(audio_path), event_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            self._audio_started_at = time.monotonic()
            self._audio_stop_event = handle
            self._audio_path = audio_path
        except OSError:
            _close_handle(handle)
            logger.exception("Could not launch game-audio capture helper")

    def _stop_game_audio(self) -> None:
        self._stop_game_audio_session(self._audio_process, self._audio_stop_event)
        self._audio_process = None
        self._audio_stop_event = None

    @staticmethod
    def _stop_game_audio_session(
        process: subprocess.Popen[str] | None,
        handle: int | None,
    ) -> None:
        if handle is not None:
            _signal_event(handle)
        if process is not None:
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.terminate()
        if handle is not None:
            _close_handle(handle)

    def _finalize_recording(self, session: RecordingSession) -> Path:
        output_path = session.output_path
        video_path = session.video_path
        audio_path = session.audio_path
        ffmpeg = _resolve_runtime_path(self._settings.ffmpeg_path)
        if not audio_path or not audio_path.exists() or audio_path.stat().st_size <= 44:
            video_path.replace(output_path)
            self._cleanup_audio(audio_path)
            return output_path

        video_delay = _positive_delta(session.video_started_at, session.audio_started_at)
        video_delay += max(0.0, self._settings.video_sync_offset_seconds)
        audio_delay = _positive_delta(session.audio_started_at, session.video_started_at)

        command = [str(ffmpeg), "-hide_banner", "-loglevel", "warning", "-y"]
        if video_delay:
            command += ["-itsoffset", f"{video_delay:.3f}"]
        command += ["-i", str(video_path)]
        if audio_delay:
            command += ["-itsoffset", f"{audio_delay:.3f}"]
        command += [
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-shortest",
            str(output_path),
        ]
        creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        result = subprocess.run(command, capture_output=True, text=True, timeout=45, creationflags=creationflags)
        if result.returncode != 0 or not output_path.exists():
            logger.error("Could not mux game audio; keeping video-only recording: %s", result.stderr[-800:])
            video_path.replace(output_path)
        else:
            video_path.unlink(missing_ok=True)
        self._cleanup_audio(audio_path)
        return output_path

    @staticmethod
    def _cleanup_audio(audio_path: Path | None) -> None:
        if audio_path:
            audio_path.unlink(missing_ok=True)


def detect_encoder(ffmpeg: Path) -> EncoderChoice:
    available = _ffmpeg_encoders(ffmpeg)
    gpu_names = _video_controller_names()
    if any("nvidia" in name for name in gpu_names) and "h264_nvenc" in available:
        return EncoderChoice("NVIDIA NVENC", "h264_nvenc")
    if any(("amd" in name or "radeon" in name) for name in gpu_names) and "h264_amf" in available:
        return EncoderChoice("AMD AMF", "h264_amf")
    if "h264_nvenc" in available:
        return EncoderChoice("NVIDIA NVENC", "h264_nvenc")
    if "h264_amf" in available:
        return EncoderChoice("AMD AMF", "h264_amf")
    return EncoderChoice("CPU x264", "libx264")


def build_recording_command(
    ffmpeg: Path,
    output_path: Path,
    settings: RecordingSettings,
    encoder: EncoderChoice,
    monitor_index: int,
) -> list[str]:
    fps = max(15, min(settings.framerate, 60))
    width = _even(max(640, settings.width))
    height = _even(max(360, settings.height))
    keyframes = fps * max(1, settings.keyframe_interval_seconds)
    quality = max(14, min(settings.quality, 35))
    capture = (
        "gfxcapture="
        "window_exe='(?i)(forzahorizon6|forzahorizon5)\\.exe':"
        "capture_cursor=0:capture_border=0:display_border=0:"
        f"max_framerate={fps}:width={width}:height={height}:"
        "resize_mode=scale_aspect:scale_mode=bicubic"
    )
    command = [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-f",
        "lavfi",
        "-i",
        capture,
        "-an",
        "-g",
        str(keyframes),
    ]
    if encoder.ffmpeg_encoder == "h264_nvenc":
        command += [
            "-c:v",
            "h264_nvenc",
            "-preset",
            "p5",
            "-tune",
            "hq",
            "-rc",
            "vbr",
            "-cq",
            str(quality),
            "-b:v",
            "0",
            "-multipass",
            "qres",
            "-profile:v",
            "high",
            "-spatial-aq",
            "1",
            "-temporal-aq",
            "1",
        ]
    elif encoder.ffmpeg_encoder == "h264_amf":
        command += [
            "-c:v",
            "h264_amf",
            "-usage",
            "transcoding",
            "-quality",
            "quality",
            "-rc",
            "cqp",
            "-qp_i",
            str(quality),
            "-qp_p",
            str(quality),
            "-qp_b",
            str(quality + 2),
            "-profile:v",
            "high",
            "-vbaq",
            "true",
            "-preencode",
            "false",
        ]
    else:
        command += [
            "-vf",
            "hwdownload,format=bgra,format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            str(quality),
            "-profile:v",
            "high",
        ]
    command += ["-f", "matroska", str(output_path)]
    return command


def enforce_storage_limit(directory: Path, max_storage_gb: float) -> list[Path]:
    if max_storage_gb <= 0 or not directory.exists():
        return []
    limit = int(max_storage_gb * 1024**3)
    videos = sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file()
            and not path.name.startswith(".")
            and path.suffix.lower() in {".mkv", ".mp4"}
        ),
        key=lambda path: (path.stat().st_mtime, path.name),
    )
    total = sum(path.stat().st_size for path in videos)
    deleted: list[Path] = []
    # Keep the newest completed recording even if that single file is larger
    # than the configured allowance; the limit applies to previous videos.
    while len(videos) > 1 and total > limit:
        oldest = videos.pop(0)
        size = oldest.stat().st_size
        try:
            oldest.unlink()
        except OSError:
            logger.exception("Could not delete old recording: %s", oldest)
            continue
        total -= size
        deleted.append(oldest)
        logger.info("Deleted old recording to respect storage limit: %s", oldest)
    return deleted


def _resolve_runtime_path(path: Path) -> Path:
    return path if path.is_absolute() else resolve_resource_path(path)


def _ffmpeg_encoders(ffmpeg: Path) -> set[str]:
    creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    try:
        result = subprocess.run(
            [str(ffmpeg), "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=creationflags,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    return set(re.findall(r"\b(?:h264_nvenc|h264_amf|libx264)\b", result.stdout + result.stderr))


def _video_controller_names() -> list[str]:
    if os.name != "nt":
        return []
    creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    try:
        result = subprocess.run(
            ["wmic", "path", "win32_VideoController", "get", "name"],
            capture_output=True,
            text=True,
            timeout=4,
            creationflags=creationflags,
        )
        if result.returncode == 0:
            return [line.strip().lower() for line in result.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_VideoController).Name",
            ],
            capture_output=True,
            text=True,
            timeout=6,
            creationflags=creationflags,
        )
        return [line.strip().lower() for line in result.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        return []


def _even(value: int) -> int:
    return value if value % 2 == 0 else value - 1


def _find_game_pid() -> int | None:
    if os.name != "nt":
        return None
    creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    try:
        result = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=creationflags,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in result.stdout.splitlines():
        match = re.match(r'"([^"]+)","(\d+)"', line)
        if match and match.group(1).lower() in {"forzahorizon5.exe", "forzahorizon6.exe"}:
            return int(match.group(2))
    return None


def _create_named_event(name: str) -> int | None:
    import ctypes

    handle = ctypes.windll.kernel32.CreateEventW(None, True, False, name)
    return int(handle) if handle else None


def _signal_event(handle: int) -> None:
    import ctypes

    ctypes.windll.kernel32.SetEvent(handle)


def _positive_delta(later: float | None, earlier: float | None) -> float:
    if later is None or earlier is None:
        return 0.0
    return max(0.0, later - earlier)


def _close_handle(handle: int) -> None:
    import ctypes

    ctypes.windll.kernel32.CloseHandle(handle)
