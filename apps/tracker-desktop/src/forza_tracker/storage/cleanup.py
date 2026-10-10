from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def enforce_image_storage_limit(directory: Path, max_storage_gb: float) -> list[Path]:
    if max_storage_gb <= 0 or not directory.exists():
        return []

    limit = int(max_storage_gb * 1024**3)
    images = sorted(
        _iter_image_files(directory),
        key=lambda item: (item[2], item[0].as_posix().lower()),
    )
    total = sum(size for _, size, _ in images)
    deleted: list[Path] = []

    # Keep the newest capture even if it alone exceeds the limit. The limit
    # applies to previous screenshots, not the detection we just saved.
    while len(images) > 1 and total > limit:
        path, size, _ = images.pop(0)
        try:
            path.unlink()
        except OSError:
            logger.exception("Could not delete old capture: %s", path)
            continue
        total -= size
        deleted.append(path)
        logger.info("Deleted old capture to respect storage limit: %s", path)

    return deleted


def _iter_image_files(directory: Path) -> list[tuple[Path, int, float]]:
    images: list[tuple[Path, int, float]] = []
    for path in directory.rglob("*"):
        if not path.is_file() or path.name.startswith(".") or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        try:
            stat = path.stat()
        except OSError:
            logger.exception("Could not inspect capture for cleanup: %s", path)
            continue
        images.append((path, stat.st_size, stat.st_mtime))
    return images
