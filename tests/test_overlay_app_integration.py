import inspect

from forza_tracker.ui.app import TrackerDashboard


def test_dashboard_skips_focus_stealing_when_overlay_is_enabled() -> None:
    source = inspect.getsource(TrackerDashboard._auto_open_recommendation)
    assert "self._settings.overlay.enabled" in source
    assert source.index("self._settings.overlay.enabled") < source.index('self._show_page("home")')


def test_dashboard_owns_runtime_settings_preview_and_shutdown() -> None:
    source = inspect.getsource(TrackerDashboard)
    assert "OverlayRuntime(self, self._settings.overlay)" in source
    assert 'text="Overlay ingame"' in source
    assert 'text="Mostrar previsualización"' in source
    assert "self._overlay_runtime.apply_settings(self._settings.overlay)" in source
    assert "self._overlay_runtime.stop()" in source


def test_preview_reports_real_overlay_availability() -> None:
    source = inspect.getsource(TrackerDashboard._preview_ingame_overlay)

    assert "self._overlay_runtime.show_preview()" in source
    assert "Previsualización mostrada sobre Forza." in source
    assert "Abre Forza y detecta primero una inscripción." in source


def test_overlay_settings_save_live_without_restarting_capture() -> None:
    overlay_save = inspect.getsource(TrackerDashboard._save_overlay_settings)
    recording_save = inspect.getsource(TrackerDashboard._save_settings_page)

    assert "self._overlay_runtime.apply_settings(self._settings.overlay)" in overlay_save
    assert "_stop_capture" not in overlay_save
    assert 'data.setdefault("overlay", {})' in overlay_save
    assert 'data.setdefault("overlay", {})' not in recording_save
