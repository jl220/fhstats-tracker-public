from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path

import pytest

from forza_tracker.localization import configure_language, current_language, tr


from forza_tracker.release_notes import (
    RELEASE_NOTES_VERSION,
    cache_release,
    installed_release_notes,
    load_cached_release,
)
from forza_tracker.ui.news import NewsPage


@pytest.fixture(autouse=True)
def restore_language_after_test():
    previous = current_language()
    try:
        yield
    finally:
        configure_language(previous)

class _Widget:
    def __init__(self) -> None:
        self.options: dict[str, object] = {}
        self.running = False

    def configure(self, **options: object) -> None:
        self.options.update(options)

    def start(self, _interval: int = 50) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False


class _Variable:
    def __init__(self) -> None:
        self.value: object = None

    def set(self, value: object) -> None:
        self.value = value


class _Notes:
    def __init__(self) -> None:
        self.state = "disabled"
        self.text = ""

    def configure(self, **options: object) -> None:
        if "state" in options:
            self.state = str(options["state"])

    def delete(self, _start: str, _end: str) -> None:
        self.text = ""

    def insert(self, _at: str, text: str) -> None:
        self.text = text


def _page_double() -> NewsPage:
    page = object.__new__(NewsPage)
    page._installed_version = _Variable()
    page._available_heading = _Variable()
    page._available_meta = _Variable()
    page._status = _Variable()
    page._progress_value = _Variable()
    page._installed_notes = _Notes()
    page._available_notes = _Notes()
    page._check_button = _Widget()
    page._install_button = _Widget()
    page._progress = _Widget()
    page._can_install = False
    page._release_installable = True
    page._busy = False
    page._on_install = lambda: None
    return page


def test_installed_release_notes_are_bilingual_and_describe_current_rivals_policy() -> None:
    english = installed_release_notes("en")
    spanish = installed_release_notes("es")

    assert "race" in english.lower()
    assert "telemetry" in english.lower()
    assert "Rivals" in english
    assert "Meta Cars" in english
    assert "garage" in english.lower()
    assert RELEASE_NOTES_VERSION == "0.4.12"
    assert "Automatic Rivals event recognition" in english
    assert "Reconocimiento automático del evento de Rivals" in spanish
    assert "Clean when no invalidation is detected" in english
    assert "Limpia mientras no se detecte invalidación" in spanish
    assert "interrupted readings remain unconfirmed" not in english
    assert "lecturas interrumpidas quedan pendientes" not in spanish
    assert RELEASE_NOTES_VERSION in english
    assert "What's new" in english
    assert "renovated" in english.lower()
    assert "carreras" in spanish.lower()
    assert "telemetría" in spanish.lower()
    assert "garaje" in spanish.lower()
    assert RELEASE_NOTES_VERSION in spanish
    assert "Novedades" in spanish
    assert "renovada" in spanish.lower()
    assert english != spanish


def test_installed_release_notes_describe_045_repairs_without_future_features() -> None:
    english = installed_release_notes("en")

    assert "Videos" in english
    assert "Garage" in english
    assert "navigation" in english.lower()
    assert "offline" in english.lower()
    assert "capture" in english.lower()
    assert "update status" in english.lower()


def test_cache_release_round_trips_and_records_a_utc_timestamp(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "release.json"

    saved = cache_release(path, version="0.4.5", notes="Corrections and polish")

    assert saved == load_cached_release(path)
    assert saved["version"] == "0.4.5"
    assert saved["notes"] == "Corrections and polish"
    assert saved["checked_at"].endswith("Z")


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        '{"version": 4, "notes": "ok", "checked_at": "today"}',
        '{"version": "1", "notes": "ok"}',
        '{"version": "", "notes": "ok", "checked_at": "2026-09-07T10:00:00Z"}',
    ],
)
def test_load_cached_release_rejects_corrupt_or_invalid_shapes(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / "release.json"
    path.write_text(content, encoding="utf-8")

    assert load_cached_release(path) is None


def test_load_cached_release_rejects_oversized_files(tmp_path: Path) -> None:
    path = tmp_path / "release.json"
    path.write_bytes(b"x" * (1024 * 1024 + 1))

    assert load_cached_release(path) is None


@pytest.mark.parametrize(
    "checked_at",
    ["2026-09-07T12:00:00+02:00", "2026-09-07T10:00:00-03:00"],
)
def test_load_cached_release_rejects_non_utc_timestamps(
    tmp_path: Path, checked_at: str
) -> None:
    path = tmp_path / "release.json"
    path.write_text(
        json.dumps({"version": "0.4.5", "notes": "safe", "checked_at": checked_at}),
        encoding="utf-8",
    )

    assert load_cached_release(path) is None


def test_cache_release_refuses_oversized_notes_without_replacing_previous_cache(
    tmp_path: Path,
) -> None:
    path = tmp_path / "release.json"
    previous = cache_release(path, version="0.4.5", notes="safe")

    with pytest.raises(ValueError, match="notes"):
        cache_release(path, version="0.4.6", notes="x" * (128 * 1024 + 1))

    assert load_cached_release(path) == previous


def test_cache_release_surfaces_replace_errors_and_preserves_previous_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import forza_tracker.release_notes as release_notes

    path = tmp_path / "release.json"
    previous = cache_release(path, version="0.4.5", notes="safe")

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(release_notes.os, "replace", fail_replace)
    with pytest.raises(OSError, match="disk unavailable"):
        cache_release(path, version="0.4.6", notes="new")

    assert json.loads(path.read_text(encoding="utf-8")) == previous
    assert list(tmp_path.glob(".*.tmp")) == []


def test_show_release_marks_cached_information_and_never_enables_install() -> None:
    configure_language("en")
    page = _page_double()

    page.show_release(
        installed_version="0.4.4",
        installed_notes="Installed notes",
        available_version="0.4.5",
        available_notes="Available notes",
        checked_at="2026-09-07T10:00:00Z",
        cached=True,
    )

    assert "0.4.4" in str(page._installed_version.value)
    assert page._installed_notes.text == "Installed notes"
    assert "0.4.5" in str(page._available_heading.value)
    assert tr("news.cached_at", checked_at="2026-09-07T10:00:00Z") == page._available_meta.value
    assert page._available_notes.text == "Available notes"
    assert page._install_button.options["state"] == "disabled"


def test_show_release_clears_stale_available_content() -> None:
    configure_language("en")
    page = _page_double()
    page._available_notes.text = "stale"

    page.show_release(installed_version="0.4.4", installed_notes="Installed notes")

    assert page._available_heading.value == tr("news.no_update_title")
    assert page._available_meta.value == ""
    assert page._available_notes.text == tr("news.no_update_notes")


@pytest.mark.parametrize(
    ("progress", "expected"),
    [(-10.0, 0.0), (42.5, 42.5), (150.0, 100.0)],
)
def test_set_status_clamps_progress_and_controls_actions(
    progress: float, expected: float
) -> None:
    page = _page_double()

    page.set_status("Downloading", busy=False, can_install=True, progress=progress)

    assert page._progress_value.value == expected
    assert page._check_button.options["state"] == "normal"
    assert page._install_button.options["state"] == "normal"
    assert page._progress.options["mode"] == "determinate"


def test_busy_status_disables_check_and_install() -> None:
    page = _page_double()

    page.set_status("Checking", busy=True, can_install=True)

    assert page._check_button.options["state"] == "disabled"
    assert page._install_button.options["state"] == "disabled"
    assert page._progress.options["mode"] == "indeterminate"
    assert page._progress.running is True


def test_install_callback_runs_only_after_explicit_handler_call() -> None:
    calls: list[str] = []
    page = _page_double()
    page._on_install = lambda: calls.append("install")
    page._can_install = True

    assert calls == []
    page._handle_install()
    assert calls == ["install"]


def test_cached_release_cannot_be_reenabled_or_installed_by_status_update() -> None:
    configure_language("en")
    calls: list[str] = []
    page = _page_double()
    page._on_install = lambda: calls.append("install")
    page.show_release(
        installed_version="0.4.5",
        installed_notes="Installed",
        available_version="0.4.6",
        available_notes="Cached",
        checked_at="2026-09-07T10:00:00Z",
        cached=True,
    )

    page.set_status("Ready", can_install=True)
    page._handle_install()

    assert page._install_button.options["state"] == "disabled"
    assert calls == []


def test_subtitle_wrap_tracks_available_page_width() -> None:
    page = _page_double()
    page._subtitle = _Widget()
    page._status_label = _Widget()
    event = type("Resize", (), {"width": 650})()

    page._resize_subtitle(event)

    assert page._subtitle.options["wraplength"] == 626


def test_news_panels_wrap_spanish_headings_at_650px(capfd) -> None:
    configure_language("es")
    # Tcl standard channels must not inherit pytest's temporary file descriptors.
    with capfd.disabled():
        root = tk.Tk()
    root.attributes("-alpha", 0.0)
    try:
        root.geometry("650x400")
        colors = {
            "bg": "#101214",
            "panel": "#1b1f22",
            "text": "#f4f5f5",
            "muted": "#9ba3a7",
            "accent": "#c7f43d",
        }
        page = NewsPage(
            root, colors=colors, on_check=lambda: None, on_install=lambda: None
        )
        page.pack(fill="both", expand=True)
        page.show_release(
            installed_version="0.4.5",
            installed_notes="Notas instaladas",
            available_version="0.4.6",
            available_notes="Notas disponibles",
            checked_at="2026-09-07T10:00:00Z",
            cached=True,
        )
        root.update()

        assert page.winfo_width() == 650
        assert page._content.grid_columnconfigure(0)["weight"] == 1
        assert page._content.grid_columnconfigure(1)["weight"] == 1
        assert page._content.grid_columnconfigure(0)["uniform"] == "news"
        assert page._content.grid_columnconfigure(1)["uniform"] == "news"
        for panel, labels in page._panel_wrap_groups:
            assert panel.winfo_width() > 200
            for label in labels:
                assert 200 <= int(label.cget("wraplength")) <= panel.winfo_width() - 20
        assert int(page._installed_notes.cget("font").split()[-1]) == 10
    finally:
        root.destroy()


def test_news_translation_catalogs_stay_in_sync() -> None:
    from forza_tracker.localization import validate_catalogs

    assert validate_catalogs() == []
