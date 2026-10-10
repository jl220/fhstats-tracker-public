from dataclasses import FrozenInstanceError, replace

import pytest

from forza_tracker.ui.dashboard_responsive import (
    DashboardLayoutProfile,
    select_dashboard_layout,
)


def test_comfortable_profile_has_the_exact_dashboard_dimensions() -> None:
    profile = select_dashboard_layout(1920, 1080)

    assert profile == DashboardLayoutProfile(
        name="comfortable",
        scroll_enabled=False,
        panel_padding=16,
        section_gap=12,
        event_map_size=(168, 100),
        event_row_padding=(12, 8),
        event_row_gap=6,
        event_number_font=14,
        event_status_font=8,
        field_title_font=9,
        field_value_font=11,
        dashboard_hero_title_font=19,
        dashboard_hero_subtitle_font=9,
        dashboard_card_title_font=8,
        dashboard_card_value_font=14,
        dashboard_card_detail_font=8,
        home_stat_title_font=8,
        home_stat_value_font=17,
        column_mins=(58, 172, 150, 72, 72, 110, 132),
        recommendation_header_wraplength=540,
        recommendation_card_wraplength=330,
    )


def test_compact_profile_has_the_exact_dashboard_dimensions() -> None:
    profile = select_dashboard_layout(1280, 720)

    assert profile == DashboardLayoutProfile(
        name="compact",
        scroll_enabled=False,
        panel_padding=10,
        section_gap=6,
        event_map_size=(116, 70),
        event_row_padding=(8, 4),
        event_row_gap=4,
        event_number_font=12,
        event_status_font=8,
        field_title_font=8,
        field_value_font=9,
        dashboard_hero_title_font=17,
        dashboard_hero_subtitle_font=8,
        dashboard_card_title_font=8,
        dashboard_card_value_font=12,
        dashboard_card_detail_font=8,
        home_stat_title_font=8,
        home_stat_value_font=15,
        column_mins=(48, 120, 130, 58, 58, 96, 118),
        recommendation_header_wraplength=360,
        recommendation_card_wraplength=190,
    )


@pytest.mark.parametrize("width,height", [(1280, 719), (1600, 719), (1920, 600)])
def test_any_height_below_720_enables_scroll(width: int, height: int) -> None:
    profile = select_dashboard_layout(width, height)
    compact = select_dashboard_layout(1280, 720)

    assert profile.name == "scroll"
    assert profile.scroll_enabled is True
    assert replace(profile, name=compact.name, scroll_enabled=compact.scroll_enabled) == compact


def test_comfortable_requires_both_width_and_height_thresholds() -> None:
    assert select_dashboard_layout(1599, 1080).name == "compact"
    assert select_dashboard_layout(1600, 899).name == "compact"
    assert select_dashboard_layout(1600, 900).name == "comfortable"


def test_dashboard_layout_profile_is_immutable() -> None:
    profile = select_dashboard_layout(1280, 720)

    with pytest.raises(FrozenInstanceError):
        profile.name = "changed"


@pytest.mark.parametrize("width,height", [(1920, 1080), (1280, 720), (880, 540)])
def test_dashboard_profile_never_reduces_readable_text_below_eight_points(
    width: int, height: int
) -> None:
    profile = select_dashboard_layout(width, height)

    assert min(
        profile.dashboard_hero_title_font,
        profile.dashboard_hero_subtitle_font,
        profile.dashboard_card_title_font,
        profile.dashboard_card_value_font,
        profile.dashboard_card_detail_font,
        profile.home_stat_title_font,
        profile.home_stat_value_font,
    ) >= 8
