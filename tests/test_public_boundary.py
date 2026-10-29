from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "apps/tracker-desktop/src/forza_tracker"


def test_desktop_source_has_no_private_automation() -> None:
    forbidden = ("pre" + "mium", "wheel" + "spin", "afk_" + "farm", "owner_" + "features")
    exposed = []
    for path in SOURCE.rglob("*.py"):
        text = path.read_text(encoding="utf-8-sig").casefold()
        if any(value in text or value in path.name.casefold() for value in forbidden):
            exposed.append(str(path.relative_to(ROOT)))
    assert not exposed, exposed


def test_private_modules_cannot_be_imported() -> None:
    for suffix in ("scripts", "afk"):
        assert importlib.util.find_spec("forza_tracker." + "pre" + "mium_" + suffix) is None






def test_public_navigation_contains_all_desktop_pages() -> None:
    from forza_tracker.ui.app import PAGE_SPECS, visible_page_specs

    assert visible_page_specs() == PAGE_SPECS
    assert {spec[0] for spec in PAGE_SPECS} == {
        "home", "history", "videos", "tierlist", "rivals", "news", "settings"
    }


def test_retired_scanner_is_not_shipped() -> None:
    assert importlib.util.find_spec("forza_tracker.garage.scanner") is None
    assert importlib.util.find_spec("forza_tracker.garage.runner") is None
    from forza_tracker.ui.app import TrackerDashboard
    assert not hasattr(TrackerDashboard, "_start_garage_scan")
