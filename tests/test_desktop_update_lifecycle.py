import queue
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from forza_tracker.ui.app import TrackerDashboard
from forza_tracker.updater import UpdateInfo


def dashboard():
    return SimpleNamespace(
        _is_exiting=False, _update_busy=False, _available_update=None,
        _update_button=Mock(), _news_page=Mock(), _capture_status=Mock(),
        _update_events=queue.Queue(), _set_update_status=Mock(),
        _render_release_notes=Mock(), _news_cache_path=Path("unused-cache.json"),
        _begin_update_check=Mock(), _show_page=Mock(),
    )


def test_update_available_does_not_replace_capture_status_or_prompt_install(monkeypatch):
    from forza_tracker.ui import app
    screen = dashboard()
    screen._accept_available_update = Mock()
    monkeypatch.setattr(app, "cache_release", lambda *a, **k: {})
    update = UpdateInfo("0.4.5", "https://fhstats.com/update.zip", "a" * 64, 100, "Notes", False)
    TrackerDashboard._on_update_check_complete(screen, update, silent=False)
    screen._capture_status.set.assert_not_called()
    screen._accept_available_update.assert_not_called()
    assert screen._available_update == update
    screen._render_release_notes.assert_called_once()


def test_update_check_error_leaves_capture_status_alone():
    screen = dashboard()
    screen._update_busy = True
    TrackerDashboard._on_update_check_error(screen, RuntimeError("offline"))
    screen._capture_status.set.assert_not_called()
    assert not screen._update_busy


def test_duplicate_update_check_is_ignored(monkeypatch):
    from forza_tracker.ui import app
    screen = dashboard()
    screen._update_busy = True
    thread = Mock()
    monkeypatch.setattr(app.threading, "Thread", thread)
    TrackerDashboard._begin_update_check(screen, silent=False)
    thread.assert_not_called()


def test_worker_queues_result_without_calling_tk(monkeypatch):
    from forza_tracker.ui import app
    screen = dashboard()
    screen.after = Mock(side_effect=AssertionError("Tk called from worker"))
    monkeypatch.setattr(app, "check_for_update", lambda *a, **k: None)
    TrackerDashboard._check_for_updates_worker(screen, "https://fhstats.com", 10, False)
    assert screen._update_events.get_nowait() == ("checked", (None, False))


def test_shutdown_ignores_queued_update_callbacks():
    screen = dashboard()
    screen._is_exiting = True
    screen.after = Mock()
    screen._on_update_check_complete = Mock()
    screen._update_events.put(("checked", (None, False)))
    TrackerDashboard._drain_update_events(screen)
    screen._on_update_check_complete.assert_not_called()
    screen.after.assert_not_called()
