from dataclasses import dataclass


@dataclass(frozen=True)
class DashboardLayoutProfile:
    name: str
    scroll_enabled: bool
    panel_padding: int
    section_gap: int
    event_map_size: tuple[int, int]
    event_row_padding: tuple[int, int]
    event_row_gap: int
    event_number_font: int
    event_status_font: int
    field_title_font: int
    field_value_font: int
    dashboard_hero_title_font: int
    dashboard_hero_subtitle_font: int
    dashboard_card_title_font: int
    dashboard_card_value_font: int
    dashboard_card_detail_font: int
    home_stat_title_font: int
    home_stat_value_font: int
    column_mins: tuple[int, ...]
    recommendation_header_wraplength: int
    recommendation_card_wraplength: int


_COMFORTABLE_PROFILE = DashboardLayoutProfile(
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

_COMPACT_PROFILE = DashboardLayoutProfile(
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

_SCROLL_PROFILE = DashboardLayoutProfile(
    name="scroll",
    scroll_enabled=True,
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


def select_dashboard_layout(width: int, height: int) -> DashboardLayoutProfile:
    if height < 720:
        return _SCROLL_PROFILE
    if width >= 1600 and height >= 900:
        return _COMFORTABLE_PROFILE
    return _COMPACT_PROFILE
