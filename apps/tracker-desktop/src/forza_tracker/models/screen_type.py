from __future__ import annotations

from enum import Enum


class ScreenType(str, Enum):
    EVENT_REGISTRATION = "event_registration"
    MATCHMAKING_SEARCH = "matchmaking_search"
    MATCHMAKING_FOUND = "matchmaking_found"
    STARTING_GRID = "starting_grid"
    RACE_RESULT = "race_result"
    CHAMPIONSHIP_RESULT = "championship_result"
    UNKNOWN = "unknown"

    @classmethod
    def from_template_name(cls, template_name: str) -> "ScreenType":
        stem = template_name.lower().rsplit(".", 1)[0]
        if stem == "event_registration" or stem.startswith("event_registration_"):
            return cls.EVENT_REGISTRATION
        if stem == "matchmaking_search" or stem.startswith("matchmaking_search_"):
            return cls.MATCHMAKING_SEARCH
        if stem == "matchmaking_found" or stem.startswith("matchmaking_found_"):
            return cls.MATCHMAKING_FOUND
        if stem == "starting_grid" or stem.startswith("starting_grid_"):
            return cls.STARTING_GRID
        if stem == "race_result" or stem.startswith("race_result_"):
            return cls.RACE_RESULT
        if stem == "championship_result" or stem.startswith("championship_result_"):
            return cls.CHAMPIONSHIP_RESULT
        return cls.UNKNOWN
