from __future__ import annotations

from typing import Protocol

from forza_tracker.models.screen_type import ScreenType


class Recorder(Protocol):
    @property
    def is_recording(self) -> bool:
        ...

    def start(
        self,
        monitor_index: int = 0,
        car_name: str | None = None,
        car_class: str | None = None,
    ) -> object:
        ...

    def stop(
        self,
        final_car_name: str | None = None,
        final_car_class: str | None = None,
        finish_position: int | None = None,
    ) -> object:
        ...


def apply_recording_boundary(
    recorder: Recorder,
    screen_type: ScreenType,
    monitor_index: int,
    car_name: str | None = None,
    car_class: str | None = None,
    finish_position: int | None = None,
) -> None:
    """Keep one independent video per race: starting grid through race result."""
    if screen_type == ScreenType.STARTING_GRID:
        # A new grid is authoritative. Close a stale recording if the previous
        # result screen was missed, then always start a fresh race video.
        if recorder.is_recording:
            recorder.stop()
        recorder.start(monitor_index, car_name, car_class)
    elif screen_type in {ScreenType.RACE_RESULT, ScreenType.CHAMPIONSHIP_RESULT}:
        # RACE_RESULT is the normal boundary. CHAMPIONSHIP_RESULT is a fallback
        # for games/languages where the race result template was not recognized.
        if recorder.is_recording:
            recorder.stop(car_name, car_class, finish_position)
