from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from forza_tracker.config.settings import OverlaySettings
from forza_tracker.overlay.controller import (
    OverlayContext,
    OverlayController,
    OverlayVisibility,
    is_fresh_race_on,
)
from forza_tracker.overlay.event_state import read_overlay_event
from forza_tracker.overlay.input_win32 import WindowsInputPoller
from forza_tracker.overlay.model import build_overlay_view_model
from forza_tracker.overlay.renderer_tk import OverlayRenderer
from forza_tracker.overlay.window_win32 import (
    WindowsOverlayAdapter,
    compute_overlay_geometry,
)
from forza_tracker.recommendation import read_recommendation
from forza_tracker.runtime_status import sanitize_error_message
from forza_tracker.telemetry.status import read_telemetry_status


logger = logging.getLogger(__name__)


class OverlayRuntime:
    CONTENT_POLL_MS = 500
    INPUT_POLL_MS = 40

    def __init__(
        self,
        root,
        settings: OverlaySettings,
        *,
        event_reader=read_overlay_event,
        recommendation_reader=read_recommendation,
        telemetry_reader=read_telemetry_status,
        window_adapter=None,
        renderer=None,
        input_poller=None,
        model_builder=build_overlay_view_model,
        geometry_builder=compute_overlay_geometry,
        clock=None,
    ) -> None:
        self._root = root
        self._settings = settings
        self._event_reader = event_reader
        self._recommendation_reader = recommendation_reader
        self._telemetry_reader = telemetry_reader
        self._window_adapter = window_adapter or WindowsOverlayAdapter()
        self._renderer = renderer or OverlayRenderer(root, self._window_adapter)
        self._input_poller = input_poller or WindowsInputPoller()
        self._model_builder = model_builder
        self._geometry_builder = geometry_builder
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._controller = OverlayController()
        self._content_job = None
        self._input_job = None
        self._running = False
        self._closed = False
        self._input_active = False
        self._input_was_active = False
        self._last_context: OverlayContext | None = None
        self._last_geometry = None
        self._last_render_key: tuple[object, ...] | None = None
        self._preview_until: datetime | None = None
        self._preview_geometry = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._closed = False
        self._content_job = self._root.after(0, self._poll_content)
        self._input_job = self._root.after(self.INPUT_POLL_MS, self._poll_input)

    def stop(self) -> None:
        if self._closed:
            return
        self._running = False
        self._closed = True
        for job in (self._content_job, self._input_job):
            if job is not None:
                try:
                    self._root.after_cancel(job)
                except Exception:
                    pass
        self._content_job = None
        self._input_job = None
        self._renderer.close()

    def apply_settings(self, settings: OverlaySettings) -> None:
        self._settings = settings
        self._last_render_key = None
        if not settings.enabled:
            self._preview_until = None
            self._preview_geometry = None
            self._input_active = False
            self._input_was_active = False
            self._last_context = None
            try:
                self._input_poller.reset()
            except Exception:
                pass
            self._renderer.hide()

    def show_preview(self) -> bool:
        now = self._clock()
        event = self._event_reader()
        window = self._window_adapter.find_forza_window()
        if event is None or not any(event.circuits) or window is None or window.minimized:
            return False
        geometry = self._geometry_builder(window.client_rect, self._settings.scale)
        if geometry is None:
            return False
        view = self._model_builder(event, self._recommendation_reader())
        self._input_active = False
        self._input_was_active = False
        self._preview_until = now + timedelta(seconds=8)
        self._preview_geometry = geometry
        self._renderer.render(view, geometry, self._settings)
        self._renderer.show_no_activate(geometry, self._settings.opacity)
        return True

    def _poll_content(self) -> None:
        if self._closed:
            return
        try:
            self._poll_content_once()
        except Exception as error:
            self._fail_closed(error)
        finally:
            if not self._closed:
                self._content_job = self._root.after(
                    self.CONTENT_POLL_MS, self._poll_content
                )

    def _poll_content_once(self) -> None:
        now = self._clock()
        event = self._event_reader()
        window = self._window_adapter.find_forza_window()
        telemetry = self._telemetry_reader()
        if self._preview_until is not None and now < self._preview_until:
            if window is None or window.minimized:
                self._renderer.hide()
                return
            geometry = self._geometry_builder(window.client_rect, self._settings.scale)
            if geometry is None:
                self._renderer.hide()
                return
            self._preview_geometry = geometry
            self._renderer.show_no_activate(geometry, self._settings.opacity)
            return
        if self._preview_until is not None:
            self._preview_until = None
            self._preview_geometry = None
            self._renderer.hide()
        context = OverlayContext(
            enabled=self._settings.enabled,
            now=now,
            duration_seconds=self._settings.duration_seconds,
            event=event,
            forza_found=window is not None,
            forza_minimized=window.minimized if window else False,
            forza_foreground=window.foreground if window else False,
            race_on=is_fresh_race_on(telemetry, now),
        )
        self._last_context = context
        decision = self._controller.update(context)
        self._input_active = bool(
            self._settings.enabled
            and event is not None
            and any(event.circuits)
            and event.phase == "registration"
            and not decision.permanently_dismissed
            and now
            < event.detected_at
            + timedelta(seconds=min(35, self._settings.duration_seconds))
            and window is not None
            and window.foreground
            and not window.minimized
        )
        if decision.visibility == OverlayVisibility.HIDDEN or window is None:
            self._renderer.hide()
            self._last_geometry = None
        else:
            geometry = self._geometry_builder(window.client_rect, self._settings.scale)
            if geometry is None or event is None:
                self._renderer.hide()
                self._last_geometry = None
            else:
                view = self._model_builder(event, self._recommendation_reader())
                render_key = (
                    view.content_signature,
                    geometry.map_window.width,
                    geometry.map_window.height,
                    geometry.recommendation_window.width,
                    geometry.recommendation_window.height,
                    self._settings.scale,
                )
                if render_key != self._last_render_key:
                    self._renderer.render(view, geometry, self._settings)
                    self._last_render_key = render_key
                self._renderer.show_no_activate(geometry, self._settings.opacity)
                self._last_geometry = geometry

    def _poll_input(self) -> None:
        if self._closed:
            return
        try:
            self._poll_input_once()
        except Exception as error:
            self._fail_closed(error)
        finally:
            if not self._closed:
                self._input_job = self._root.after(
                    self.INPUT_POLL_MS, self._poll_input
                )

    def _poll_input_once(self) -> None:
        if self._input_active and self._last_context is not None:
            if not self._input_was_active:
                self._input_poller.reset()
            actions = self._input_poller.poll()
            if actions:
                decision = self._controller.update(self._last_context, actions)
                if decision.permanently_dismissed:
                    self._input_active = False
                if decision.visibility == OverlayVisibility.HIDDEN:
                    self._renderer.hide()
                elif self._last_geometry is not None:
                    self._renderer.show_no_activate(
                        self._last_geometry, self._settings.opacity
                    )
        elif self._input_was_active:
            self._input_poller.reset()
        self._input_was_active = self._input_active

    def _fail_closed(self, error: Exception) -> None:
        self._input_active = False
        self._input_was_active = False
        self._last_context = None
        self._last_geometry = None
        self._last_render_key = None
        self._preview_until = None
        self._preview_geometry = None
        try:
            self._input_poller.reset()
        except Exception:
            pass
        try:
            self._renderer.hide()
        except Exception:
            pass
        logger.error("Ingame overlay hidden after error: %s", sanitize_error_message(str(error)))
