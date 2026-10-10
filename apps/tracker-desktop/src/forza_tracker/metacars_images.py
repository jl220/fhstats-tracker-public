"""Local Meta Cars images with refreshable screenshots and identified-car fallbacks."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata

from PIL import Image
import requests

from forza_tracker.paths import application_root


def _relative_image(source: str) -> Path | None:
    # Catalog image paths are public FHStats assets, never arbitrary URLs or local paths.
    if not isinstance(source, str) or not re.fullmatch(
        r'/images/(?:tierlist|cars/fh6|metacars/[a-zA-Z0-9_-]+/cars)/[a-zA-Z0-9_-]+\.(?:png|jpg|jpeg|webp)', source
    ):
        return None
    return Path(source.lstrip('/'))


def cached_image_path(cache_root: Path, source: str) -> Path | None:
    relative = _relative_image(source)
    return cache_root / 'metacars-images' / relative if relative else None


def local_image_path(cache_root: Path, source: str) -> str:
    relative = _relative_image(source)
    if relative is None:
        return ''
    candidates = [cached_image_path(cache_root, source)]
    root = application_root()
    if source.startswith('/images/tierlist/'):
        candidates.append(root / 'data' / 'tierlist-images' / relative.name)
    elif source.startswith('/images/cars/fh6/'):
        candidates.append(root / 'data' / 'car-images' / 'fh6' / relative.name)
    candidates.append(root / 'web' / 'public' / relative)
    return next((str(path) for path in candidates if path and path.is_file()), '')


def _read_media_json(filename: str, cache_root: Path):
    for root in (cache_root, application_root() / 'data' / 'recommender', application_root() / 'web' / 'data'):
        try:
            return json.loads((root / filename).read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            continue
    return {}


def _name_key(name: str) -> str:
    return re.sub(r'[^a-z0-9]', '', unicodedata.normalize('NFKD', name).casefold())


class CarMedia:
    def __init__(self, cache_root: Path):
        media = _read_media_json('fh6-car-media.json', cache_root)
        cars = media.get('cars', []) if isinstance(media, dict) else []
        self.by_id = {str(car['carId']): car['src'] for car in cars
                      if isinstance(car, dict) and car.get('carId') and _relative_image(car.get('src', ''))}
        names: dict[str, set[str]] = {}
        for car in cars:
            if isinstance(car, dict) and str(car.get('carId')) in self.by_id:
                names.setdefault(_name_key(str(car.get('displayName', ''))), set()).add(str(car['carId']))
        aliases = _read_media_json('fh6-rivals-image-aliases.json', cache_root)
        if isinstance(aliases, dict):
            for name, car_id in aliases.items():
                if str(car_id) in self.by_id:
                    names.setdefault(_name_key(name), set()).add(str(car_id))
        self.by_name = {name: self.by_id[next(iter(ids))] for name, ids in names.items() if len(ids) == 1}

    def source(self, row: dict) -> str:
        if row.get('car_id'):
            return self.by_id.get(str(row['car_id']), '')
        name = f"{row.get('make', '')} {row.get('model', '')}"
        return (self.by_name.get(_name_key(f"{row.get('year', '')} {name}"))
                or self.by_name.get(_name_key(name), ''))


def sync_images(rows: list[dict], cache_root: Path, *, get=requests.get) -> list[str]:
    """Run in the catalog worker. Re-fetch mutable URLs; retain good files on failure."""
    candidates: dict[str, str] = {}
    for row in rows:
        primary = row.get('image_source') or row.get('fallback_source', '')
        if _relative_image(primary):
            candidates[primary] = row.get('fallback_source', '')

    def download(source: str) -> bool:
        target = cached_image_path(cache_root, source)
        if target is None:
            return False
        response = None
        try:
            response = get('https://fhstats.com' + source, timeout=5,
                           headers={'Accept': 'image/*', 'Cache-Control': 'no-cache'})
            response.raise_for_status()
            payload = response.content
            if not payload or len(payload) > 8 * 1024 * 1024:
                return False
            with Image.open(io.BytesIO(payload)) as image:
                image.verify()
            target.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(prefix=target.stem, suffix='.tmp', dir=target.parent)
            try:
                with os.fdopen(handle, 'wb') as output:
                    output.write(payload)
                os.replace(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)
            return True
        except (OSError, ValueError, requests.RequestException):
            return False
        finally:
            if response is not None and hasattr(response, 'close'):
                response.close()

    def refresh(item):
        primary, fallback = item
        if download(primary):
            return None
        if fallback and fallback != primary:
            download(fallback)
        return primary

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix='metacars-images') as pool:
        return [source for source in pool.map(refresh, candidates.items()) if source]
