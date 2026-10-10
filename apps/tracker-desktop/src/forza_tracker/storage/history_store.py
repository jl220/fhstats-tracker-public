from __future__ import annotations

import json
import logging
from pathlib import Path

from forza_tracker.models.result import ParsedResult

logger = logging.getLogger(__name__)


class HistoryStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, result: ParsedResult) -> None:
        with self._path.open("a", encoding="utf-8") as file:
            json.dump(result.to_json_dict(), file, ensure_ascii=False)
            file.write("\n")
        logger.info("Stored match history entry at %s", self._path)
