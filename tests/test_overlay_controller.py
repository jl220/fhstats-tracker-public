from dataclasses import replace
from datetime import datetime, timedelta, timezone

from forza_tracker.overlay.controller import (
    OverlayAction,
    OverlayContext,
    OverlayController,
    OverlayVisibility,
    is_fresh_race_on,
)
from forza_tracker.overlay.event_state import OverlayEventSnapshot


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
EVENT = OverlayEventSnapshot(
    1, "event-1", NOW, NOW, "registration", "registration", "S1 900", ("A", "B", "C")
)


def _context(**changes) -> OverlayContext:
    values = dict(
        enabled=True,
        now=NOW + timedelta(seconds=1),
        duration_seconds=35,
        event=EVENT,
        forza_found=True,
        forza_minimized=False,
        forza_foreground=True,
        race_on=False,
    )
    values.update(changes)
    return OverlayContext(**values)


def test_confirm_temporarily_hides_and_back_restores() -> None:
    controller = OverlayController()
    assert controller.update(_context()).visibility == OverlayVisibility.VISIBLE
    assert controller.update(_context(), (OverlayAction.CONFIRM,)).reason == "temporarily_hidden"
    assert controller.update(_context(), (OverlayAction.BACK,)).visibility == OverlayVisibility.VISIBLE


def test_mixed_back_up_confirm_sequence_dismisses_current_event() -> None:
    controller = OverlayController()
    controller.update(_context(), (OverlayAction.BACK,))
    controller.update(_context(now=NOW + timedelta(seconds=2)), (OverlayAction.UP,))
    decision = controller.update(
        _context(now=NOW + timedelta(seconds=3)), (OverlayAction.CONFIRM,)
    )

    assert decision.visibility == OverlayVisibility.HIDDEN
    assert decision.permanently_dismissed is True
    assert controller.update(_context(now=NOW + timedelta(seconds=4))).reason == "dismissed"


def test_new_event_clears_local_dismissal() -> None:
    controller = OverlayController()
    controller.update(_context(), (OverlayAction.BACK, OverlayAction.UP, OverlayAction.CONFIRM))
    new_event = replace(EVENT, event_id="event-2", detected_at=NOW + timedelta(seconds=5))

    assert controller.update(_context(event=new_event, now=NOW + timedelta(seconds=6))).visibility == OverlayVisibility.VISIBLE


def test_hard_and_soft_hide_rules() -> None:
    controller = OverlayController()
    assert controller.update(_context(forza_foreground=False)).reason == "forza_not_foreground"
    assert controller.update(_context()).visibility == OverlayVisibility.VISIBLE
    assert controller.update(_context(now=NOW + timedelta(seconds=35))).reason == "expired"

    controller = OverlayController()
    assert controller.update(_context(race_on=True)).reason == "race_on"

    controller = OverlayController()
    grid = replace(EVENT, phase="starting_grid")
    assert controller.update(_context(event=grid)).reason == "starting_grid"

    controller = OverlayController()
    empty = replace(EVENT, circuits=(None, None, None))
    assert controller.update(_context(event=empty)).reason == "no_circuits"


def test_fresh_race_on_rejects_stale_or_invalid_status() -> None:
    assert is_fresh_race_on(
        {"is_race_on": True, "last_packet_at": NOW.isoformat()}, NOW + timedelta(seconds=1)
    )
    assert not is_fresh_race_on(
        {"is_race_on": True, "last_packet_at": NOW.isoformat()}, NOW + timedelta(seconds=5)
    )
    assert not is_fresh_race_on({"is_race_on": True}, NOW)
