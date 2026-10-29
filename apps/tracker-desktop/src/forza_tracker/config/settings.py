from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from forza_tracker.localization import normalize_language


@dataclass(frozen=True)
class AppSettings:
    player_name: str = "Jolu4327"
    log_level: str = "INFO"
    language: str | None = None


@dataclass(frozen=True)
class CaptureSettings:
    backend: str = "mss"
    monitor_index: int = 0
    auto_detect_monitors: bool = True
    interval_seconds: float = 1.0
    output_dir: Path = Path("captures")
    max_storage_gb: float = 1.0


@dataclass(frozen=True)
class DetectorSettings:
    templates_dir: Path = Path("templates")
    threshold: float = 0.86
    thresholds_by_type: dict[str, float] = field(default_factory=dict)
    cooldown_seconds: int = 45
    cooldown_seconds_by_type: dict[str, float] = field(default_factory=dict)
    resize_scales: list[float] = field(default_factory=lambda: [1.0])
    log_best_match_interval_seconds: int = 10


@dataclass(frozen=True)
class HistorySettings:
    enabled: bool = True
    path: Path = Path("data/history/results.jsonl")


@dataclass(frozen=True)
class ApiSettings:
    enabled: bool = False
    diagnostics_enabled: bool = False
    base_url: str = "http://127.0.0.1:8000"
    api_key: str | None = None
    timeout_seconds: int = 5


@dataclass(frozen=True)
class OcrSettings:
    enabled: bool = True
    tesseract_cmd: Path | None = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")


@dataclass(frozen=True)
class RecordingSettings:
    enabled: bool = False
    mode: str = "off"
    output_dir: Path = Path("data/recordings")
    max_storage_gb: float = 20.0
    framerate: int = 60
    width: int = 1920
    height: int = 1080
    quality: int = 20
    keyframe_interval_seconds: int = 2
    capture_audio: bool = True
    ffmpeg_path: Path = Path("runtime/ffmpeg/bin/ffmpeg.exe")
    audio_helper_path: Path = Path("runtime/audio/FHStatsAudioCapture.exe")
    video_sync_offset_seconds: float = 0.08


@dataclass(frozen=True)
class TelemetrySettings:
    enabled: bool = False
    game: str = "fh6"
    host: str = "0.0.0.0"
    port: int = 9999
    auto_configure: bool = False
    stale_after_seconds: float = 3.0
    event_forwarding_enabled: bool = False
    event_forwarding_host: str = ""
    event_forwarding_port: int = 0


@dataclass(frozen=True)
class OverlaySettings:
    enabled: bool = True
    opacity: float = 0.82
    scale: float = 1.0
    duration_seconds: int = 35


@dataclass(frozen=True)
class Settings:
    app: AppSettings = field(default_factory=AppSettings)
    capture: CaptureSettings = field(default_factory=CaptureSettings)
    detector: DetectorSettings = field(default_factory=DetectorSettings)
    history: HistorySettings = field(default_factory=HistorySettings)
    api: ApiSettings = field(default_factory=ApiSettings)
    ocr: OcrSettings = field(default_factory=OcrSettings)
    recording: RecordingSettings = field(default_factory=RecordingSettings)
    telemetry: TelemetrySettings = field(default_factory=TelemetrySettings)
    overlay: OverlaySettings = field(default_factory=OverlaySettings)


def load_settings(path: Path) -> Settings:
    raw = _load_yaml(path)
    app = dict(raw.get("app", {}))
    app["language"] = normalize_language(app.get("language"))
    api = dict(raw.get("api", {}))
    if "diagnostics_enabled" not in api:
        api["diagnostics_enabled"] = bool(api.get("enabled", False))
    detector = dict(raw.get("detector", {}))
    thresholds_by_type = dict(detector.get("thresholds_by_type", {}))
    thresholds_by_type["event_registration"] = min(
        float(thresholds_by_type.get("event_registration", 0.77)),
        0.77,
    )
    thresholds_by_type["race_result"] = min(float(thresholds_by_type.get("race_result", 0.78)), 0.78)
    thresholds_by_type["championship_result"] = min(
        float(thresholds_by_type.get("championship_result", 0.92)),
        0.92,
    )
    detector["thresholds_by_type"] = thresholds_by_type
    recording = dict(raw.get("recording", {}))
    if bool(recording.get("enabled", False)):
        recording["mode"] = "races"
    else:
        mode = str(recording.get("mode") or "off")
        recording["mode"] = mode if mode in {"off", "session", "races"} else "off"
    overlay = dict(raw.get("overlay", {}))
    return Settings(
        app=AppSettings(**app),
        capture=CaptureSettings(**_with_path(raw.get("capture", {}), "output_dir")),
        detector=DetectorSettings(**_with_path(detector, "templates_dir")),
        history=HistorySettings(**_with_path(raw.get("history", {}), "path")),
        api=ApiSettings(**api),
        ocr=OcrSettings(**_with_path(raw.get("ocr", {}), "tesseract_cmd")),
        recording=RecordingSettings(
            **_with_paths(recording, "output_dir", "ffmpeg_path", "audio_helper_path")
        ),
        telemetry=TelemetrySettings(**raw.get("telemetry", {})),
        overlay=OverlaySettings(
            enabled=bool(overlay.get("enabled", True)),
            opacity=_clamp_float(overlay.get("opacity"), 0.82, 0.45, 0.95),
            scale=_clamp_float(overlay.get("scale"), 1.0, 0.75, 1.25),
            duration_seconds=_clamp_int(overlay.get("duration_seconds"), 35, 5, 35),
        ),
    )


def _clamp_float(value: object, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _clamp_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}

    if not isinstance(data, dict):
        raise ValueError(f"Config root must be a mapping: {path}")

    return data


def _with_path(section: dict[str, Any], key: str) -> dict[str, Any]:
    copied = dict(section)
    if key in copied and copied[key] is not None:
        copied[key] = Path(copied[key])
    return copied


def _with_paths(section: dict[str, Any], *keys: str) -> dict[str, Any]:
    copied = dict(section)
    for key in keys:
        if key in copied and copied[key] is not None:
            copied[key] = Path(copied[key])
    return copied
