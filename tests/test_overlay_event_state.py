from datetime import datetime, timedelta, timezone
import inspect
from pathlib import Path
from types import SimpleNamespace

from forza_tracker.main import _run_tracker, publish_overlay_result, safe_publish_overlay_result
from forza_tracker.models.screen_type import ScreenType
from forza_tracker.overlay.event_state import (
    OverlayEventPublisher,
    read_overlay_event,
    write_overlay_event,
)


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def test_event_snapshot_round_trip_and_corruption_is_safe(tmp_path: Path) -> None:
    path = tmp_path / "overlay-event.json"
    publisher = OverlayEventPublisher(path)
    snapshot = publisher.registration(
        slots=("A", None, "C"),
        car_class="S1 900",
        source_screen="event_registration",
        observed_at=NOW,
    )

    assert read_overlay_event(path) == snapshot
    path.write_text("{broken", encoding="utf-8")
    assert read_overlay_event(path) is None


def test_repeated_registration_keeps_event_identity_and_first_detection(tmp_path: Path) -> None:
    publisher = OverlayEventPublisher(tmp_path / "overlay-event.json")
    first = publisher.registration(
        slots=("A", None, "C"), car_class=None, source_screen="event_registration", observed_at=NOW
    )
    second = publisher.registration(
        slots=("A", "B", "C"),
        car_class="A 800",
        source_screen="event_registration",
        observed_at=NOW + timedelta(seconds=5),
    )

    assert second.event_id == first.event_id
    assert second.detected_at == NOW
    assert second.updated_at == NOW + timedelta(seconds=5)
    assert second.circuits == ("A", "B", "C")


def test_conflict_expiry_or_closed_phase_starts_a_new_event(tmp_path: Path) -> None:
    publisher = OverlayEventPublisher(tmp_path / "overlay-event.json")
    first = publisher.registration(
        slots=("A", None, None), car_class=None, source_screen="event_registration", observed_at=NOW
    )
    conflict = publisher.registration(
        slots=("Different", None, None),
        car_class=None,
        source_screen="event_registration",
        observed_at=NOW + timedelta(seconds=1),
    )
    expired = publisher.registration(
        slots=("Different", None, None),
        car_class=None,
        source_screen="event_registration",
        observed_at=NOW + timedelta(seconds=37),
    )
    publisher.close(source_screen="championship_result", observed_at=NOW + timedelta(seconds=38))
    after_close = publisher.registration(
        slots=("Different", None, None),
        car_class=None,
        source_screen="event_registration",
        observed_at=NOW + timedelta(seconds=39),
    )

    assert conflict.event_id != first.event_id
    assert expired.event_id != conflict.event_id
    assert after_close.event_id != expired.event_id


def test_mark_phase_preserves_snapshot_identity(tmp_path: Path) -> None:
    path = tmp_path / "overlay-event.json"
    publisher = OverlayEventPublisher(path)
    first = publisher.registration(
        slots=("A", "B", "C"), car_class="S1 900", source_screen="registration", observed_at=NOW
    )
    grid = publisher.mark_phase(
        "starting_grid", source_screen="starting_grid", observed_at=NOW + timedelta(seconds=10)
    )

    assert grid is not None
    assert grid.event_id == first.event_id
    assert grid.phase == "starting_grid"
    assert read_overlay_event(path) == grid


def test_publish_overlay_result_dispatches_registration_and_hard_phases() -> None:
    class FakePublisher:
        def __init__(self) -> None:
            self.calls = []

        def registration(self, **kwargs):
            self.calls.append(("registration", kwargs))

        def mark_phase(self, phase, **kwargs):
            self.calls.append((phase, kwargs))

        def close(self, **kwargs):
            self.calls.append(("closed", kwargs))

    publisher = FakePublisher()
    publish_overlay_result(
        publisher,
        SimpleNamespace(
            screen_type=ScreenType.EVENT_REGISTRATION,
            event_plan_slots=("A", None, "C"),
            car_class="A 800",
        ),
        observed_at=NOW,
    )
    publish_overlay_result(
        publisher,
        SimpleNamespace(screen_type=ScreenType.STARTING_GRID),
        observed_at=NOW,
    )
    publish_overlay_result(
        publisher,
        SimpleNamespace(screen_type=ScreenType.CHAMPIONSHIP_RESULT),
        observed_at=NOW,
    )

    assert [call[0] for call in publisher.calls] == ["registration", "starting_grid", "closed"]


def test_overlay_publication_failure_never_stops_capture_worker() -> None:
    class BrokenPublisher:
        def registration(self, **_kwargs):
            raise OSError("disk full")

    parsed = SimpleNamespace(
        screen_type=ScreenType.EVENT_REGISTRATION,
        event_plan_slots=("A", None, None),
        car_class="A 800",
    )

    assert safe_publish_overlay_result(BrokenPublisher(), parsed, observed_at=NOW) is None


def test_capture_worker_keeps_publishing_when_overlay_is_enabled_live() -> None:
    source = inspect.getsource(_run_tracker)
    assert "overlay_publisher = OverlayEventPublisher()" in source
    assert "if overlay_publisher is not None" not in source
