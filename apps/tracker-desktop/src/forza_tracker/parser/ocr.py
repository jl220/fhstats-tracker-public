from __future__ import annotations

import logging
import os
from pathlib import Path

import cv2
import numpy as np
import pytesseract

from forza_tracker.paths import application_root

logger = logging.getLogger(__name__)


class TesseractOcr:
    def __init__(self, enabled: bool, tesseract_cmd: Path | None) -> None:
        self._enabled = enabled
        resolved_cmd = _resolve_tesseract_cmd(tesseract_cmd)
        self._languages = _available_languages(resolved_cmd)
        if resolved_cmd is not None:
            pytesseract.pytesseract.tesseract_cmd = str(resolved_cmd)
            tessdata_dir = resolved_cmd.parent / "tessdata"
            if tessdata_dir.exists():
                os.environ["TESSDATA_PREFIX"] = str(tessdata_dir)
            logger.info(
                "Using Tesseract from %s with languages=%s",
                resolved_cmd,
                self._languages,
            )
        elif enabled:
            logger.warning("Tesseract executable not found at %s", tesseract_cmd)

    def read_line(self, image: np.ndarray, *, whitelist: str | None = None) -> str | None:
        if not self._enabled:
            return None

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        config = "--psm 7"
        if whitelist:
            config += f" -c tessedit_char_whitelist={whitelist}"

        try:
            return pytesseract.image_to_string(
                gray,
                config=config,
                lang=self._languages,
            ).strip() or None
        except pytesseract.TesseractNotFoundError:
            logger.warning("Tesseract executable is not available")
            return None


def _resolve_tesseract_cmd(configured: Path | None) -> Path | None:
    bundled = application_root() / "ocr" / "tesseract" / "tesseract.exe"
    if bundled.exists():
        return bundled
    if configured is not None and configured.exists():
        return configured
    return None


def _available_languages(tesseract_cmd: Path | None) -> str:
    if tesseract_cmd is None:
        return "eng"
    tessdata_dir = tesseract_cmd.parent / "tessdata"
    languages = [
        language
        for language in ("eng", "spa")
        if (tessdata_dir / f"{language}.traineddata").exists()
    ]
    return "+".join(languages) or "eng"
