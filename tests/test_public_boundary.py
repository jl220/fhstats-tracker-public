from __future__ import annotations

import importlib.util
import ast
import inspect
import textwrap
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


def test_garage_adapter_is_standalone() -> None:
    from forza_tracker.garage.runner import GARAGE_PROFILE_PATH, run_garage_scan_script
    from forza_tracker.garage.scanner import GarageScreenProfile

    assert callable(run_garage_scan_script)
    profile = GarageScreenProfile.load(ROOT / GARAGE_PROFILE_PATH)
    assert {"screen_marker", "selected_card", "navigation_strip"} <= profile.regions.keys()


def test_dashboard_mounts_the_standalone_garage() -> None:
    from forza_tracker.ui.app import TrackerDashboard

    tree = ast.parse(textwrap.dedent(inspect.getsource(TrackerDashboard._build_layout)))
    assert any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_build_garage_page"
        for node in ast.walk(tree)
    )


def test_public_navigation_contains_all_desktop_pages() -> None:
    from forza_tracker.ui.app import PAGE_SPECS, visible_page_specs

    assert visible_page_specs() == PAGE_SPECS
    assert {spec[0] for spec in PAGE_SPECS} == {
        "home", "history", "videos", "tierlist", "rivals", "garage", "news", "settings"
    }
