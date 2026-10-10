"""Exercise desktop construction and navigation with isolated, empty data."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/tracker-desktop/src"))


class IdleRivalsRuntime:
    """Supply an idle view without opening a game process or collecting data."""

    def start(self):
        pass

    def stop(self):
        pass

    def set_profile(self, _profile):
        pass

    def snapshot(self, **_kwargs):
        return {
            "active": None, "status": "waiting", "current": {},
            "attempts": [], "bests": [], "events": [], "revision": 0,
            "telemetry_fresh": False, "refresh": {}, "error": "",
        }


def check_language(language: str) -> None:
    import yaml

    data_root = Path(tempfile.mkdtemp(prefix=f"fhstats-public-{language}-"))
    os.environ["FHSTATS_TRACKER_DATA_DIR"] = str(data_root)
    os.environ["FHSTATS_START_PAGE"] = "home"
    config = yaml.safe_load((ROOT / "config/tracker.yaml").read_text(encoding="utf-8"))
    config["app"].update(language=language, player_name="PublicSourceCheck")
    config["overlay"]["enabled"] = False
    config["api"].update(enabled=False, diagnostics_enabled=False)
    config["telemetry"] = {"enabled": False}
    (data_root / "config").mkdir(parents=True)
    (data_root / "config/tracker.local.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

    from forza_tracker.ui import app as tracker_app
    from forza_tracker.localization import current_language

    failures = []
    # Construct the real widget tree, keeping the diagnostic window hidden.
    with patch.object(tracker_app.tk.Tk, "deiconify", lambda self: self.withdraw()):
        dashboard = tracker_app.TrackerDashboard(
            data_root / "data/history/results.jsonl", rivals_runtime=IdleRivalsRuntime()
        )
    dashboard.withdraw()
    dashboard.report_callback_exception = lambda kind, value, trace: failures.append(f"{kind.__name__}: {value}")
    try:
        assert current_language() == language
        assert Path(tracker_app.__file__).resolve().is_relative_to(ROOT)
        pages = {page_id for page_id, _, _ in tracker_app.PAGE_SPECS}
        assert set(dashboard._nav_buttons) == pages
        assert set(dashboard._pages) == pages
        for page_id in sorted(pages):
            dashboard._show_page(page_id)
            dashboard.update_idletasks()
            assert dashboard._active_page == page_id
            assert dashboard._pages[page_id].winfo_children(), page_id
        dashboard._poll_rivals()
        dashboard.update_idletasks()
        assert not failures, failures
        print(f"{language}: desktop construction, eight pages and idle Rivals polling passed")
    finally:
        dashboard.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=("en", "es"))
    args = parser.parse_args()
    if args.language:
        check_language(args.language)
        return
    for language in ("en", "es"):
        subprocess.run([sys.executable, __file__, "--language", language], check=True, timeout=45)


if __name__ == "__main__":
    main()
