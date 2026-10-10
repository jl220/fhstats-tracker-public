from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from forza_tracker.garage import runner as garage_runner
from forza_tracker.garage.runner import ScreenFrame


def test_garage_probe_captures_before_and_after_one_right_press(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = np.zeros((100, 150, 3), dtype=np.uint8)
    after = before.copy()
    after[20:80, 10:110] = 80
    frames = iter(
        [
            [ScreenFrame(before, 1, 0, 0, 150, 100)],
            [ScreenFrame(after, 1, 0, 0, 150, 100)],
        ]
    )
    pressed: list[str] = []
    waited: list[float] = []

    monkeypatch.setattr(garage_runner.os, "name", "nt")
    monkeypatch.setattr(garage_runner, "_activate_forza_window", lambda: (10, 20, 110, 80))
    monkeypatch.setattr(garage_runner, "_monitor_index_for_window", lambda _settings, _rect: 1)
    monkeypatch.setattr(
        garage_runner,
        "_capture_frames",
        lambda _settings, preferred_monitor_index=None: next(frames),
    )
    monkeypatch.setattr(garage_runner, "_press_key", pressed.append)
    monkeypatch.setattr(
        garage_runner,
        "_sleep_or_stop",
        lambda seconds, _stop_event: waited.append(seconds),
    )

    payload = garage_runner.capture_garage_probe(SimpleNamespace(), tmp_path)

    assert pressed == ["right"]
    assert waited == [0.35]
    assert payload["window_rect"] == [10, 20, 110, 80]
    assert payload["monitor_index"] == 1
    assert payload["width"] == 100
    assert payload["height"] == 60
    assert payload["mean_difference"] > 0
    assert {path.name for path in tmp_path.iterdir()} == {
        "after.png",
        "before.png",
        "difference.png",
        "probe.json",
    }
    assert json.loads((tmp_path / "probe.json").read_text(encoding="utf-8")) == payload


def test_probe_cli_help_imports_tracker_without_pythonpath() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)

    result = subprocess.run(
        [sys.executable, "scripts/probe_garage_capture.py", "--help"],
        cwd=repository_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert "--config" in result.stdout
