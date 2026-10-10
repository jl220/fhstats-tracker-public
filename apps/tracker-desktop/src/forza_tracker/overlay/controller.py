from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from forza_tracker.overlay.event_state import OverlayEventSnapshot


class OverlayAction(Enum):
    BACK = "back"
    UP = "up"
    CONFIRM = "confirm"


class OverlayVisibility(Enum):
    HIDDEN = "hidden"
    VISIBLE = "visible"


@dataclass(frozen=True)
class OverlayContext:
    enabled: bool
    now: datetime
    duration_seconds: int
    event: OverlayEventSnapshot | None
    forza_found: bool
    forza_minimized: bool
    forza_foreground: bool
    race_on: bool


@dataclass(frozen=True)
class OverlayDecision:
    visibility: OverlayVisibility
    permanently_dismissed: bool
    reason: str


class OverlayController:
    def __init__(self) -> None:
        self._event_id: str | None = None
        self._temporarily_hidden = False
        self._dismissed_event_id: str | None = None
        self._sequence: tuple[OverlayAction, ...] = ()
        self._sequence_at: datetime | None = None

    def update(
        self, context: OverlayContext, actions: tuple[OverlayAction, ...] = ()
    ) -> OverlayDecision:
        event = context.event
        event_id = event.event_id if event else None
        if event_id != self._event_id:
            self._event_id = event_id
            self._temporarily_hidden = False
            self._dismissed_event_id = None
            self._reset_sequence()

        if not context.enabled:
            return self._hidden("disabled")
        if event is None:
            return self._hidden("no_event")
        if not any(event.circuits):
            return self._hidden("no_circuits")
        if self._dismissed_event_id == event.event_id:
            return self._hidden("dismissed", permanent=True)
        if event.phase in {"starting_grid", "closed"}:
            self._dismissed_event_id = event.event_id
            return self._hidden(event.phase, permanent=True)
        deadline = event.detected_at + timedelta(seconds=min(35, context.duration_seconds))
        if context.now >= deadline:
            self._dismissed_event_id = event.event_id
            return self._hidden("expired", permanent=True)
        if context.race_on:
            self._dismissed_event_id = event.event_id
            return self._hidden("race_on", permanent=True)

        self._process_actions(event.event_id, context.now, actions)
        if self._dismissed_event_id == event.event_id:
            return self._hidden("dismissed", permanent=True)
        if not context.forza_found:
            return self._hidden("forza_not_found")
        if context.forza_minimized:
            return self._hidden("forza_minimized")
        if not context.forza_foreground:
            return self._hidden("forza_not_foreground")
        if self._temporarily_hidden:
            return self._hidden("temporarily_hidden")
        return OverlayDecision(OverlayVisibility.VISIBLE, False, "visible")

    def _process_actions(
        self, event_id: str, now: datetime, actions: tuple[OverlayAction, ...]
    ) -> None:
        if self._sequence_at and now - self._sequence_at > timedelta(seconds=3):
            self._reset_sequence()
        for action in actions:
            if action == OverlayAction.BACK:
                self._temporarily_hidden = False
                self._sequence = (OverlayAction.BACK,)
                self._sequence_at = now
            elif action == OverlayAction.UP:
                if self._sequence == (OverlayAction.BACK,):
                    self._sequence = (OverlayAction.BACK, OverlayAction.UP)
                    self._sequence_at = now
                else:
                    self._reset_sequence()
            elif action == OverlayAction.CONFIRM:
                if self._sequence == (OverlayAction.BACK, OverlayAction.UP):
                    self._dismissed_event_id = event_id
                    self._temporarily_hidden = False
                else:
                    self._temporarily_hidden = True
                self._reset_sequence()

    def _reset_sequence(self) -> None:
        self._sequence = ()
        self._sequence_at = None

    @staticmethod
    def _hidden(reason: str, permanent: bool = False) -> OverlayDecision:
        return OverlayDecision(OverlayVisibility.HIDDEN, permanent, reason)


def is_fresh_race_on(
    payload: dict[str, object] | None,
    now: datetime,
    stale_after_seconds: float = 2.0,
) -> bool:
    if not payload or payload.get("is_race_on") is not True:
        return False
    timestamp = payload.get("last_packet_at") or payload.get("updated_at")
    if not timestamp:
        return False
    try:
        observed_at = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        age = (now - observed_at).total_seconds()
    except (TypeError, ValueError):
        return False
    return 0 <= age <= stale_after_seconds
