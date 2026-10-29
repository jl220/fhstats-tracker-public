from types import SimpleNamespace
from unittest.mock import Mock

from forza_tracker.ui.app import TrackerDashboard


def test_leaving_videos_for_garage_always_pauses_and_hides_player():
    dashboard = SimpleNamespace(
        _active_page="videos",
        _pages={"home": Mock(), "videos": Mock(), "garage": Mock()},
        _nav_buttons={"home": Mock(), "garage": Mock()}, _pause_video=Mock(),
        _hide_video_overlay_for_inactive_page=Mock(), _refresh_garage_cards=Mock(),
    )
    TrackerDashboard._show_page(dashboard, "garage")
    dashboard._pause_video.assert_called_once()
    dashboard._hide_video_overlay_for_inactive_page.assert_called_once()
    dashboard._refresh_garage_cards.assert_not_called()
    assert dashboard._active_page == "home"


def test_unavailable_garage_routes_to_home():
    dashboard = SimpleNamespace(
        _active_page="home",
        _pages={"home": Mock(), "garage": Mock()}, _nav_buttons={"home": Mock()},
        _refresh_garage_cards=Mock(),
    )
    TrackerDashboard._show_page(dashboard, "garage")
    assert dashboard._active_page == "home"
    dashboard._refresh_garage_cards.assert_not_called()


def test_empty_garage_explains_how_to_add_cars():
    screen = SimpleNamespace(
        _garage_grid=Mock(), _garage_card_photos=[], _garage_slot=Mock(),
        _garage_store=Mock(), _garage_status=Mock(), _garage_summary=Mock(),
        _garage_video_button=Mock(), _garage_export_button=Mock(),
        _build_empty_state=Mock(),
    )
    screen._garage_grid.winfo_children.return_value = []
    screen._garage_store.load_slot.return_value = None
    TrackerDashboard._refresh_garage_cards(screen)
    screen._build_empty_state.assert_called_once()
