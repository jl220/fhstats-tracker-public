from __future__ import annotations

from forza_tracker.restart import build_restart_command


def test_frozen_restart_reuses_the_packaged_executable() -> None:
    assert build_restart_command(
        executable=r"C:\\FHStats\\FHStatsTracker.exe",
        argv=[r"C:\\FHStats\\FHStatsTracker.exe"],
        frozen=True,
    ) == [r"C:\\FHStats\\FHStatsTracker.exe", "--restart-wait"]


def test_source_restart_uses_the_dashboard_module() -> None:
    assert build_restart_command(
        executable=r"C:\\Python313\\python.exe",
        argv=[r"C:\\repo\\app.py"],
        frozen=False,
    ) == [
        r"C:\\Python313\\python.exe",
        "-m",
        "forza_tracker.ui.app",
        "--restart-wait",
    ]
