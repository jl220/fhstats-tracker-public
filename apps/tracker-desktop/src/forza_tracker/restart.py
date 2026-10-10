from __future__ import annotations

from collections.abc import Sequence


def build_restart_command(
    *,
    executable: str,
    argv: Sequence[str],
    frozen: bool,
) -> list[str]:
    del argv  # Restart intentionally drops transient command-line arguments.
    if frozen:
        return [executable, "--restart-wait"]
    return [executable, "-m", "forza_tracker.ui.app", "--restart-wait"]
