from datetime import datetime, timezone
from types import SimpleNamespace

from forza_tracker.config.settings import OverlaySettings
from forza_tracker.overlay.controller import OverlayAction
from forza_tracker.overlay.event_state import OverlayEventSnapshot
from forza_tracker.overlay.runtime import OverlayRuntime
from forza_tracker.overlay.window_win32 import ForzaWindowState, OverlayGeometry, Rect


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
EVENT = OverlayEventSnapshot(
    1, "event-1", NOW, NOW, "registration", "registration", "S1 900", ("A", "B", "C")
)
WINDOW = ForzaWindowState(10, Rect(0, 0, 2048, 1152), 96, False, True)
GEOMETRY = OverlayGeometry(
    Rect(0, 0, 200, 500),
    (Rect(0, 0, 200, 100), Rect(0, 200, 200, 300), Rect(0, 400, 200, 500)),
    Rect(250, 500, 900, 600),
)


class FakeRoot:
    def __init__(self):
        self.jobs = {}
        self.cancelled = []
        self.index = 0

    def after(self, delay, callback):
        self.index += 1
        job = f"job-{self.index}"
        self.jobs[job] = (delay, callback)
        return job

    def after_cancel(self, job):
        self.cancelled.append(job)


class FakeRenderer:
    def __init__(self):
        self.rendered = 0
        self.shown = 0
        self.hidden = 0
        self.closed = 0

    def render(self, *_args):
        self.rendered += 1
        return True

    def show_no_activate(self, *_args):
        self.shown += 1

    def hide(self):
        self.hidden += 1

    def close(self):
        self.closed += 1

def _runtime(root, renderer, actions=(), telemetry=None, input_poller=None):
    poller = input_poller or SimpleNamespace(poll=lambda: actions, reset=lambda: None)
    return OverlayRuntime(
        root,
        OverlaySettings(),
        event_reader=lambda: EVENT,
        recommendation_reader=lambda: {},
        telemetry_reader=lambda: telemetry or {},
        window_adapter=SimpleNamespace(find_forza_window=lambda: WINDOW),
        renderer=renderer,
        input_poller=poller,
        model_builder=lambda *_args, **_kwargs: SimpleNamespace(content_signature="signature"),
        geometry_builder=lambda *_args: GEOMETRY,
        clock=lambda: NOW,
    )


def test_runtime_renders_only_changed_content_and_repositions_each_poll() -> None:
    root = FakeRoot()
    renderer = FakeRenderer()
    runtime = _runtime(root, renderer)

    runtime._poll_content()
    runtime._poll_content()

    assert renderer.rendered == 1
    assert renderer.shown == 2
    assert all(delay == 500 for delay, _callback in root.jobs.values())


def test_runtime_input_hides_immediately_and_stop_cleans_jobs() -> None:
    root = FakeRoot()
    renderer = FakeRenderer()
    runtime = _runtime(root, renderer, actions=(OverlayAction.CONFIRM,))
    runtime.start()
    runtime._poll_content()
    runtime._poll_input()
    runtime.stop()

    assert renderer.hidden >= 1
    assert renderer.closed == 1
    assert root.cancelled


def test_disabling_runtime_hides_overlay() -> None:
    class CountingPoller:
        def __init__(self):
            self.resets = 0

        def poll(self):
            return ()

        def reset(self):
            self.resets += 1

    renderer = FakeRenderer()
    poller = CountingPoller()
    runtime = _runtime(FakeRoot(), renderer, input_poller=poller)
    runtime._input_active = True
    runtime._input_was_active = True
    runtime._preview_until = NOW
    runtime.apply_settings(OverlaySettings(enabled=False))

    assert renderer.hidden >= 1
    assert runtime._input_active is False
    assert runtime._input_was_active is False
    assert runtime._preview_until is None
    assert poller.resets == 1


def test_runtime_fails_closed_and_reschedules_after_render_error() -> None:
    class BrokenRenderer(FakeRenderer):
        def render(self, *_args):
            raise RuntimeError("renderer failed")

    root = FakeRoot()
    renderer = BrokenRenderer()
    runtime = _runtime(root, renderer)

    runtime._poll_content()

    assert renderer.hidden >= 1
    assert any(delay == 500 for delay, _callback in root.jobs.values())


def test_permanent_race_dismissal_stops_input_polling_immediately() -> None:
    class CountingPoller:
        def __init__(self):
            self.polls = 0

        def poll(self):
            self.polls += 1
            return ()

        def reset(self):
            pass

    poller = CountingPoller()
    runtime = _runtime(
        FakeRoot(),
        FakeRenderer(),
        telemetry={"is_race_on": True, "last_packet_at": NOW.isoformat()},
        input_poller=poller,
    )

    runtime._poll_content()
    runtime._poll_input()

    assert poller.polls == 0


def test_preview_uses_latest_event_with_live_geometry_and_renderer() -> None:
    renderer = FakeRenderer()
    runtime = _runtime(FakeRoot(), renderer)
    captured = []

    def build(event, payload):
        captured.append((event, payload))
        return SimpleNamespace(content_signature="preview")

    runtime._model_builder = build
    assert runtime.show_preview() is True

    assert captured[0][0].event_id == EVENT.event_id
    assert renderer.rendered == 1
    assert renderer.shown == 1


def test_preview_reports_unavailable_without_forza() -> None:
    renderer = FakeRenderer()
    runtime = _runtime(FakeRoot(), renderer)
    runtime._window_adapter = SimpleNamespace(find_forza_window=lambda: None)

    assert runtime.show_preview() is False
    assert renderer.rendered == 0
    assert renderer.shown == 0
