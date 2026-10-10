from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TRACKER_SOURCE = REPOSITORY_ROOT / "apps" / "tracker-desktop" / "src"
sys.path.insert(0, str(TRACKER_SOURCE))

from forza_tracker.config.settings import load_settings
from forza_tracker.garage.runner import capture_garage_probe


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Captura dos estados de Mis coches y avanza exactamente un coche.",
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()

    settings = load_settings(arguments.config.resolve())
    output_dir = arguments.output.resolve()
    payload = capture_garage_probe(settings, output_dir)
    print(output_dir)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
