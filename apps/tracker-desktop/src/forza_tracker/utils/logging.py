from __future__ import annotations

import logging
from pathlib import Path

from forza_tracker.paths import user_data_root


def configure_logging(level: str, log_file: Path | None = None) -> None:
    log_file = log_file or user_data_root() / "logs" / "forza_tracker.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )
