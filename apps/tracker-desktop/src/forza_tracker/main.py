from __future__ import annotations

import argparse
import logging
import platform
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from forza_tracker.api.client import ApiClient
from forza_tracker.capture.screen_capture import create_capture_backend
from forza_tracker.config.settings import load_settings
from forza_tracker.detector.result_detector import ResultDetector
from forza_tracker.events.bus import EventBus
from forza_tracker.models.events import ResultDetected, ResultParsed, ResultUploaded
from forza_tracker.models.result import ParsedResult
from forza_tracker.models.screen_type import ScreenType
from forza_tracker.overlay.event_state import (
    OverlayEventPublisher,
    OverlayEventSnapshot,
)
from forza_tracker.paths import ensure_user_data_layout, resolve_data_path, resolve_resource_path
from forza_tracker.parser.ocr import TesseractOcr
from forza_tracker.parser.result_parser import ResultParser
from forza_tracker.runtime_status import sanitize_error_message, write_status
from forza_tracker.recording import MatchRecorder, apply_recording_boundary
from forza_tracker.recording.control import consume_recording_command, write_recording_status
from forza_tracker.telemetry import apply_latest_telemetry_context, start_telemetry_listener_if_enabled
from forza_tracker.local_recommender import recommend_championship
from forza_tracker.localization import configure_language, tr
from forza_tracker.recommendation import write_recommendation
from forza_tracker.storage.cleanup import enforce_image_storage_limit
from forza_tracker.storage.history_store import HistoryStore
from forza_tracker.utils.logging import configure_logging
from forza_tracker.version import APP_VERSION

logger = logging.getLogger(__name__)
_last_overlay_publish_error_log = 0.0


@dataclass
class _RaceUploadState:
    enabled: bool
    api_client: ApiClient
    event_bus: EventBus
    starting_grid: ParsedResult | None = None
    race_result: ParsedResult | None = None
    uploaded: bool = False

    def record_race_result(self, race_result: ParsedResult) -> None:
        self.race_result = race_result
        self.uploaded = self._submit(None)

    def advance_to_grid(self, starting_grid: ParsedResult) -> None:
        if self.race_result is not None and not self.uploaded:
            self.uploaded = self._submit(None)
        self.starting_grid = starting_grid

    def enrich_championship(self, championship_result: ParsedResult) -> None:
        if self.race_result is None:
            return
        self.uploaded = self._submit(championship_result) or self.uploaded

    def clear(self) -> None:
        self.starting_grid = None
        self.race_result = None
        self.uploaded = False

    def _submit(self, championship_result: ParsedResult | None) -> bool:
        if self.race_result is None:
            return False
        return _submit_race_snapshot(
            enabled=self.enabled,
            api_client=self.api_client,
            event_bus=self.event_bus,
            starting_grid=self.starting_grid,
            race_result=self.race_result,
            championship_result=championship_result,
        )


def main() -> None:
    args = _parse_args()
    ensure_user_data_layout()
    settings = load_settings(args.config)
    configure_language(settings.app.language or "en")
    configure_logging(settings.app.log_level)
    api_client = ApiClient(settings.api.base_url, settings.api.timeout_seconds, settings.api.api_key)
    try:
        _run_tracker(settings, api_client)
    except Exception as error:
        message = sanitize_error_message(str(error))
        logger.exception("Capture process stopped unexpectedly")
        write_status(
            "error",
            error_type=type(error).__name__,
            error_message=message,
            stopped_at=datetime.now(timezone.utc).isoformat(),
        )
        if settings.api.diagnostics_enabled:
            _report_client_error(
                api_client,
                player=settings.app.player_name,
                component="capture",
                error=error,
                context={"phase": "capture_loop"},
            )
        raise


def _run_tracker(settings, api_client: ApiClient) -> None:

    monitor_index = 0 if settings.capture.auto_detect_monitors else settings.capture.monitor_index
    capture = create_capture_backend(settings.capture.backend, monitor_index)
    detector = ResultDetector(
        templates_dir=resolve_resource_path(settings.detector.templates_dir),
        threshold=settings.detector.threshold,
        cooldown_seconds=settings.detector.cooldown_seconds,
        resize_scales=settings.detector.resize_scales,
        thresholds_by_type=settings.detector.thresholds_by_type,
        cooldown_seconds_by_type=settings.detector.cooldown_seconds_by_type,
        log_best_match_interval_seconds=settings.detector.log_best_match_interval_seconds,
    )
    parser = ResultParser(
        default_player=settings.app.player_name,
        ocr=TesseractOcr(settings.ocr.enabled, settings.ocr.tesseract_cmd),
    )
    history_store = HistoryStore(resolve_data_path(settings.history.path))
    event_bus = _create_event_bus()
    race_upload = _RaceUploadState(settings.api.enabled, api_client, event_bus)
    recorder = MatchRecorder(settings.recording)
    overlay_publisher = OverlayEventPublisher()
    telemetry_thread = start_telemetry_listener_if_enabled(settings.telemetry, reuse_existing=True)
    if telemetry_thread is not None:
        logger.info("FH6 telemetry listener started on %s:%s", settings.telemetry.host, settings.telemetry.port)
    last_circuit_name: str | None = None
    last_grid_car: str | None = None
    last_grid_car_class: str | None = None
    last_grid_class_crop_path: Path | None = None
    last_grid_event_number: int | None = None
    last_grid_event_total: int | None = None
    last_grid_discipline: str | None = None
    last_event_type: str | None = None
    last_event_plan: list[str] | None = None
    last_race_car: str | None = None
    last_race_car_class: str | None = None
    last_recommendation_signature: tuple[tuple[str, ...], str] | None = None
    recording_mode = "automatic"
    recording_output_path: Path | None = None

    started_at = datetime.now(timezone.utc).isoformat()
    write_status(
        "running",
        started_at=started_at,
        last_frame_at=started_at,
        last_detection_at=None,
        frames_captured=0,
        detections=0,
        error_type=None,
        error_message=None,
    )
    frames_captured = 0
    detections = 0
    logger.info("Forza tracker started. Press Ctrl+C to stop.")
    write_recording_status(recording=False, mode="idle")

    try:
        while True:
            loop_started_at = time.monotonic()
            command = consume_recording_command()
            if command:
                action = str(command.get("command") or "")
                if action == "start_session":
                    if recorder.is_recording:
                        recording_mode = "session"
                    else:
                        recording_output_path = recorder.start(
                            monitor_index=max(0, monitor_index - 1),
                            car_name="Sesion completa",
                            car_class="Manual",
                            force=True,
                        )
                        recording_mode = "session" if recording_output_path else "automatic"
                elif action == "stop":
                    recorder.stop()
                    recording_mode = "automatic"
                    recording_output_path = None
                elif action == "shutdown":
                    recorder.stop(wait=True)
                    recording_mode = "automatic"
                    recording_output_path = None
                    write_recording_status(recording=False, mode="idle", message=tr("capture.worker_stopped"))
                    write_status("stopped", stopped_at=datetime.now(timezone.utc).isoformat())
                    break
                write_recording_status(
                    recording=recorder.is_recording,
                    mode=recording_mode if recorder.is_recording else "idle",
                    output_path=str(recording_output_path) if recording_output_path else None,
                    message=tr("recording.manual_active") if recorder.is_recording else tr("recording.stopped"),
                )
            frames = capture.grab_all()
            frames_captured += len(frames)
            now = datetime.now(timezone.utc)
            write_status(
                "running",
                last_frame_at=now.isoformat(),
                frames_captured=frames_captured,
                detections=detections,
            )
            detected_frame_index, detection = detector.detect_many([frame.image for frame in frames])
            frame = frames[detected_frame_index]

            if detection.detected:
                detections += 1
                timestamp = datetime.now(timezone.utc)
                write_status(
                    "running",
                    last_detection_at=timestamp.isoformat(),
                    frames_captured=frames_captured,
                    detections=detections,
                    active_monitor=frame.monitor_index,
                )
                screenshot_path = _save_screenshot(
                    frame.image,
                    resolve_data_path(settings.capture.output_dir),
                    timestamp,
                    detection.screen_type.value,
                    settings.capture.max_storage_gb,
                )
                logger.info(
                    "Using monitor %d for detected screen type=%s",
                    frame.monitor_index,
                    detection.screen_type.value,
                )

                event_bus.publish(
                    ResultDetected(
                        screenshot_path=screenshot_path,
                        confidence=detection.confidence,
                        template_name=detection.template_name,
                        screen_type=detection.screen_type,
                        timestamp=timestamp,
                    )
                )

                parsed = parser.parse(frame.image, screenshot_path, timestamp, detection.screen_type)
                safe_publish_overlay_result(
                    overlay_publisher, parsed, observed_at=timestamp
                )
                registration_complete = (
                    parsed.screen_type != ScreenType.EVENT_REGISTRATION
                    or bool(parsed.event_plan and len(parsed.event_plan) == 3 and parsed.car_class)
                )
                if not registration_complete:
                    detector.reset_cooldown(ScreenType.EVENT_REGISTRATION)
                    logger.info("Incomplete event registration ignored; retrying on the next frame")
                if parsed.screen_type not in {ScreenType.EVENT_REGISTRATION, ScreenType.MATCHMAKING_SEARCH, ScreenType.MATCHMAKING_FOUND} and parsed.circuit_name:
                    last_circuit_name = parsed.circuit_name
                elif parsed.screen_type not in {ScreenType.EVENT_REGISTRATION, ScreenType.MATCHMAKING_SEARCH, ScreenType.MATCHMAKING_FOUND} and last_circuit_name:
                    parsed = replace(parsed, circuit_name=last_circuit_name)
                if parsed.screen_type in {ScreenType.EVENT_REGISTRATION, ScreenType.MATCHMAKING_SEARCH, ScreenType.MATCHMAKING_FOUND}:
                    if parsed.screen_type == ScreenType.EVENT_REGISTRATION and parsed.event_type:
                        last_event_type = parsed.event_type
                    if parsed.event_plan:
                        last_event_plan = parsed.event_plan
                    if parsed.event_number:
                        last_grid_event_number = parsed.event_number
                    if parsed.event_total:
                        last_grid_event_total = parsed.event_total
                    if (
                        parsed.screen_type == ScreenType.EVENT_REGISTRATION
                        and parsed.event_plan
                        and parsed.car_class
                    ):
                        recommendation_signature = (tuple(parsed.event_plan), parsed.car_class)
                        if recommendation_signature != last_recommendation_signature:
                            last_recommendation_signature = recommendation_signature
                            _request_recommendation(
                                parsed.event_plan,
                                parsed.car_class,
                            )
                elif parsed.screen_type == ScreenType.STARTING_GRID:
                    recommendation_class = (
                        last_recommendation_signature[1]
                        if last_recommendation_signature is not None
                        else None
                    )
                    if (
                        parsed.car_class
                        and recommendation_class
                        and parsed.car_class != recommendation_class
                    ):
                        logger.warning(
                            "Starting grid class %s differs from active recommendation %s; "
                            "discarding stale championship context",
                            parsed.car_class,
                            recommendation_class,
                        )
                        last_event_plan = None
                        last_grid_event_number = None
                        last_grid_event_total = None
                        last_grid_discipline = None
                        last_grid_car = None
                        last_grid_car_class = None
                        last_grid_class_crop_path = None
                        last_race_car = None
                        last_race_car_class = None
                        last_recommendation_signature = (tuple(), parsed.car_class)
                        _write_waiting_recommendation(parsed.car_class)
                    elif parsed.car_class and last_recommendation_signature is None:
                        last_recommendation_signature = (tuple(), parsed.car_class)
                        _write_waiting_recommendation(parsed.car_class)
                    # MSS numbers physical monitors from 1; DXGI/ddagrab starts at 0.
                    if recording_mode != "session":
                        apply_recording_boundary(
                            recorder,
                            parsed.screen_type,
                            max(0, frame.monitor_index - 1),
                            parsed.car,
                            parsed.car_class,
                        )
                    next_grid = replace(
                        parsed,
                        event_type=parsed.event_type or last_event_type,
                        event_number=parsed.event_number or last_grid_event_number,
                        event_total=parsed.event_total or last_grid_event_total,
                        event_plan=parsed.event_plan or last_event_plan,
                    )
                    race_upload.advance_to_grid(next_grid)
                    parsed = next_grid
                    last_grid_event_number = parsed.event_number or last_grid_event_number
                    last_grid_event_total = parsed.event_total or last_grid_event_total
                    last_grid_discipline = parsed.discipline or last_grid_discipline
                    last_grid_class_crop_path = parsed.class_crop_path or last_grid_class_crop_path
                    if parsed.car:
                        last_grid_car = parsed.car
                        last_grid_car_class = parsed.car_class
                    elif parsed.car_class:
                        last_grid_car_class = parsed.car_class
                elif parsed.screen_type == ScreenType.RACE_RESULT:
                    if not parsed.car and last_grid_car:
                        parsed = replace(parsed, car=last_grid_car)
                    if not parsed.car_class and last_grid_car_class and (not parsed.car or parsed.car == last_grid_car):
                        parsed = replace(parsed, car_class=last_grid_car_class)
                    parsed = replace(
                        parsed,
                        event_number=parsed.event_number or last_grid_event_number,
                        event_total=parsed.event_total or last_grid_event_total,
                        event_type=parsed.event_type or last_event_type,
                        event_plan=parsed.event_plan or last_event_plan,
                        discipline=parsed.discipline or last_grid_discipline,
                        class_crop_path=parsed.class_crop_path or last_grid_class_crop_path,
                    )
                    parsed = apply_latest_telemetry_context(
                        parsed,
                        settings.telemetry,
                        now=timestamp,
                    )
                    if parsed.car:
                        last_race_car = parsed.car
                        last_race_car_class = parsed.car_class
                    elif parsed.car_class:
                        last_race_car_class = parsed.car_class
                    race_upload.record_race_result(parsed)
                    if recording_mode != "session":
                        apply_recording_boundary(
                            recorder,
                            parsed.screen_type,
                            max(0, frame.monitor_index - 1),
                            parsed.car,
                            parsed.car_class,
                            parsed.position,
                        )
                elif parsed.screen_type == ScreenType.CHAMPIONSHIP_RESULT:
                    parsed = replace(
                        parsed,
                        car=parsed.car or last_race_car,
                        car_class=parsed.car_class or last_race_car_class,
                        event_number=parsed.event_number or last_grid_event_number,
                        event_total=parsed.event_total or last_grid_event_total,
                        event_type=parsed.event_type or last_event_type,
                        event_plan=parsed.event_plan or last_event_plan,
                        discipline=parsed.discipline or last_grid_discipline,
                        class_crop_path=parsed.class_crop_path or last_grid_class_crop_path,
                    )
                    race_upload.enrich_championship(parsed)
                    if recording_mode != "session":
                        apply_recording_boundary(
                            recorder,
                            parsed.screen_type,
                            max(0, frame.monitor_index - 1),
                            parsed.car,
                            parsed.car_class,
                            parsed.championship_position,
                        )

                event_bus.publish(
                    ResultParsed(
                        screenshot_path=parsed.screenshot_path,
                        screen_type=parsed.screen_type,
                        player=parsed.player,
                        circuit_name=parsed.circuit_name,
                        position=parsed.position,
                        starting_position=parsed.starting_position,
                        event_type=parsed.event_type,
                        event_number=parsed.event_number,
                        event_total=parsed.event_total,
                        discipline=parsed.discipline,
                        car=parsed.car,
                        car_class=parsed.car_class,
                        class_crop_path=parsed.class_crop_path,
                        championship_position=parsed.championship_position,
                        progress_percent=parsed.progress_percent,
                        best_lap=parsed.best_lap,
                        total_time=parsed.total_time,
                        row_detection_confidence=parsed.row_detection_confidence,
                        raw_row_text=parsed.raw_row_text,
                        timestamp=parsed.timestamp,
                        event_plan=parsed.event_plan,
                    )
                )

                if settings.history.enabled and registration_complete:
                    history_store.append(parsed)

                if (
                    parsed.screen_type == ScreenType.CHAMPIONSHIP_RESULT
                    and _is_final_championship_result(parsed)
                ):
                    logger.info(
                        "Final championship result detected (%s/%s); clearing event context",
                        parsed.event_number,
                        parsed.event_total,
                    )
                    race_upload.clear()
                    last_circuit_name = None
                    last_grid_car = None
                    last_grid_car_class = None
                    last_grid_class_crop_path = None
                    last_grid_event_number = None
                    last_grid_event_total = None
                    last_grid_discipline = None
                    last_event_type = None
                    last_event_plan = None
                    last_race_car = None
                    last_race_car_class = None
                    last_recommendation_signature = None
                    _clear_recommendation()

            elapsed_seconds = time.monotonic() - loop_started_at
            write_recording_status(
                recording=recorder.is_recording,
                mode=recording_mode if recorder.is_recording else "idle",
                output_path=str(recording_output_path) if recording_output_path else None,
            )
            time.sleep(max(0.0, settings.capture.interval_seconds - elapsed_seconds))
    except KeyboardInterrupt:
        logger.info("Forza tracker stopped by user.")
        write_status("stopped", stopped_at=datetime.now(timezone.utc).isoformat())
    finally:
        recorder.stop(wait=True)
        write_recording_status(recording=False, mode="idle", message=tr("capture.worker_stopped"))


def publish_overlay_result(
    publisher: OverlayEventPublisher,
    parsed: ParsedResult,
    *,
    observed_at: datetime,
) -> OverlayEventSnapshot | None:
    source_screen = parsed.screen_type.value
    if parsed.screen_type == ScreenType.EVENT_REGISTRATION:
        return publisher.registration(
            slots=parsed.event_plan_slots or (None, None, None),
            car_class=parsed.car_class,
            source_screen=source_screen,
            observed_at=observed_at,
        )
    if parsed.screen_type == ScreenType.STARTING_GRID:
        return publisher.mark_phase(
            "starting_grid", source_screen=source_screen, observed_at=observed_at
        )
    if parsed.screen_type == ScreenType.CHAMPIONSHIP_RESULT:
        return publisher.close(source_screen=source_screen, observed_at=observed_at)
    return None


def safe_publish_overlay_result(
    publisher: OverlayEventPublisher,
    parsed: ParsedResult,
    *,
    observed_at: datetime,
) -> OverlayEventSnapshot | None:
    global _last_overlay_publish_error_log
    try:
        return publish_overlay_result(publisher, parsed, observed_at=observed_at)
    except Exception as error:
        now = time.monotonic()
        if now - _last_overlay_publish_error_log >= 10:
            _last_overlay_publish_error_log = now
            logger.warning(
                "Overlay event publication skipped: %s",
                sanitize_error_message(str(error)),
            )
        return None


def _create_event_bus() -> EventBus:
    event_bus = EventBus()
    event_bus.subscribe(ResultDetected, lambda event: logger.info("Event ResultDetected: %s", event))
    event_bus.subscribe(ResultParsed, lambda event: logger.info("Event ResultParsed: %s", event))
    event_bus.subscribe(ResultUploaded, lambda event: logger.info("Event ResultUploaded: %s", event))
    return event_bus


def _submit_race_snapshot(
    *,
    enabled: bool,
    api_client: ApiClient,
    event_bus: EventBus,
    starting_grid: ParsedResult | None,
    race_result: ParsedResult,
    championship_result: ParsedResult | None,
) -> bool:
    if not enabled:
        return False
    return _upload_race(
        api_client,
        event_bus,
        starting_grid,
        race_result,
        championship_result,
    )


def _upload_race(
    api_client: ApiClient,
    event_bus: EventBus,
    starting_grid: ParsedResult | None,
    race_result: ParsedResult,
    championship_result: ParsedResult | None,
) -> bool:
    payload = _build_race_payload(starting_grid, race_result, championship_result)
    try:
        response = api_client.upload_race(payload)
    except Exception:
        logger.exception("Could not upload race to API")
        return False

    event_bus.publish(
        ResultUploaded(
            backend_id=str(response.get("id", "")),
            timestamp=datetime.now(timezone.utc),
        )
    )
    return True


def _report_client_error(
    api_client: ApiClient,
    player: str,
    component: str,
    error: Exception,
    context: dict[str, object] | None = None,
) -> None:
    try:
        api_client.report_error(
            {
                "player": player or "unknown",
                "app_version": APP_VERSION,
                "component": component,
                "error_type": type(error).__name__,
                "message": sanitize_error_message(str(error)),
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "context": {
                    "os": platform.system(),
                    "os_release": platform.release(),
                    **(context or {}),
                },
            }
        )
    except Exception:
        logger.exception("Could not report client error to API")


def _request_recommendation(
    event_plan: list[str],
    car_class: str,
) -> None:
    write_recommendation(
        {
            "status": "loading",
            "car_class": car_class,
            "requested_event_plan": event_plan,
            "recommendations": [],
        }
    )
    try:
        started_at = time.perf_counter()
        payload = recommend_championship(event_plan, car_class)
        payload["requested_event_plan"] = event_plan
        payload["source"] = "local"
        write_recommendation(payload)
        logger.info(
            "Stored local championship recommendation class=%s events=%s elapsed_ms=%.1f",
            car_class,
            event_plan,
            (time.perf_counter() - started_at) * 1000,
        )
    except Exception as error:
        logger.exception("Could not calculate local championship recommendation")
        write_recommendation(
            {
                "status": "error",
                "car_class": car_class,
                "requested_event_plan": event_plan,
                "message": tr("recommendation.local_data_error"),
                "recommendations": [],
            }
        )


def _write_waiting_recommendation(car_class: str) -> None:
    write_recommendation(
        {
            "status": "waiting",
            "car_class": car_class,
            "requested_event_plan": [],
            "message": tr("recommendation.class_updated"),
            "recommendations": [],
        }
    )


def _clear_recommendation() -> None:
    write_recommendation(
        {
            "status": "idle",
            "car_class": "",
            "requested_event_plan": [],
            "recommendations": [],
        }
    )


def _is_final_championship_result(parsed: ParsedResult) -> bool:
    return bool(
        parsed.event_number is not None
        and parsed.event_total is not None
        and parsed.event_total > 0
        and parsed.event_number >= parsed.event_total
    )


def _build_race_payload(
    starting_grid: ParsedResult | None,
    race_result: ParsedResult,
    championship_result: ParsedResult | None,
) -> dict[str, object]:
    return {
        "player": race_result.player or (starting_grid.player if starting_grid else None) or "unknown",
        "game": "forza_horizon_5",
        "played_at": race_result.timestamp.isoformat(),
        "event_number": _first_not_none(
            starting_grid.event_number if starting_grid else None,
            race_result.event_number,
            championship_result.event_number if championship_result else None,
        ),
        "event_total": _first_not_none(
            starting_grid.event_total if starting_grid else None,
            race_result.event_total,
            championship_result.event_total if championship_result else None,
        ),
        "event_type": _first_not_none(
            race_result.event_type,
            starting_grid.event_type if starting_grid else None,
            championship_result.event_type if championship_result else None,
        ),
        "event_plan": _first_not_none(
            race_result.event_plan,
            starting_grid.event_plan if starting_grid else None,
            championship_result.event_plan if championship_result else None,
        ),
        "circuit": _first_not_none(race_result.circuit_name, starting_grid.circuit_name if starting_grid else None),
        "discipline": _first_not_none(
            starting_grid.discipline if starting_grid else None,
            race_result.discipline,
            championship_result.discipline if championship_result else None,
        ),
        "car": _first_not_none(race_result.car, starting_grid.car if starting_grid else None),
        "car_class": _first_not_none(
            race_result.car_class,
            starting_grid.car_class if starting_grid else None,
            championship_result.car_class if championship_result else None,
        ),
        "start_position": starting_grid.starting_position if starting_grid else None,
        "finish_position": race_result.position,
        "championship_position": championship_result.championship_position if championship_result else None,
        "total_time": race_result.total_time,
        "best_lap": race_result.best_lap,
        "parser_version": f"forza-horizon-tracker/{APP_VERSION}",
    }


def _first_not_none(*values: object) -> object:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _save_screenshot(
    image: np.ndarray,
    output_dir: Path,
    timestamp: datetime,
    screen_type: str,
    max_storage_gb: float,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{screen_type}_{timestamp.strftime('%Y%m%d_%H%M%S')}.png"
    path = output_dir / filename
    cv2.imwrite(str(path), image)
    enforce_image_storage_limit(output_dir, max_storage_gb)
    logger.info("Saved screenshot: %s", path)
    return path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Forza Horizon result tracker")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/tracker.yaml"),
        help="Path to tracker YAML config.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
