import io
import json
from pathlib import Path

from PIL import Image

from forza_tracker import local_catalog


def catalog(tmp_path, monkeypatch):
    app = tmp_path / 'app'
    data = app / 'data' / 'recommender'
    data.mkdir(parents=True)
    (data / 'fh6-car-media.json').write_text(json.dumps({'cars': [
        {'carId': 123, 'displayName': '1991 Honda Beat', 'src': '/images/cars/fh6/123-new.webp'},
    ]}))
    monkeypatch.setattr(local_catalog, 'application_root', lambda: app)
    # The image resolver shares the same application root as the catalog.
    from forza_tracker import metacars_images
    monkeypatch.setattr(metacars_images, 'application_root', lambda: app)
    return app, data


def test_direct_image_and_correct_extension_replace_legacy_png_preference(tmp_path, monkeypatch):
    app, data = catalog(tmp_path, monkeypatch)
    images = app / 'data' / 'tierlist-images'
    images.mkdir()
    for suffix in ('png', 'jpg'):
        Image.new('RGB', (8, 8)).save(images / f'beat.{suffix}')
    (data / 'tierlist-cars.ts').write_text('''
    { id: "one", discipline: "road", className: "B 600", make: "Honda", model: "Beat", year: 1991, image: image("beat")
    },
    { id: "two", discipline: "road", className: "A 700", make: "Honda", model: "Beat", carId: 123, image: "/images/cars/fh6/123-new.webp"
    }
    ''')
    rows = local_catalog.load_tierlist(cache_root=data)
    assert rows[0]['image'] == str(images / 'beat.jpg')
    assert rows[1]['image_source'] == '/images/cars/fh6/123-new.webp'


def test_missing_screenshot_uses_identified_car_image_and_cache_beats_package(tmp_path, monkeypatch):
    app, data = catalog(tmp_path, monkeypatch)
    official = app / 'data' / 'car-images' / 'fh6' / '123-new.webp'
    official.parent.mkdir(parents=True)
    Image.new('RGB', (8, 8), 'blue').save(official)
    (data / 'tierlist-cars.ts').write_text('''
    { id: "one", discipline: "road", className: "B 600", make: "Honda", model: "Beat", carId: 123, image: card("missing")
    }
    ''')
    rows = local_catalog.load_tierlist(cache_root=data)
    assert rows[0]['image'] == str(official)
    cached = data / 'metacars-images' / 'images' / 'tierlist' / 'missing.png'
    cached.parent.mkdir(parents=True)
    Image.new('RGB', (8, 8), 'red').save(cached)
    assert local_catalog.load_tierlist(cache_root=data)[0]['image'] == str(cached)


def test_sync_refreshes_same_url_and_falls_back_without_overwriting_valid_cache(tmp_path):
    from forza_tracker.metacars_images import sync_images, cached_image_path
    def png(color):
        buffer = io.BytesIO()
        Image.new('RGB', (8, 8), color).save(buffer, format='PNG')
        return buffer.getvalue()
    responses = {'/images/tierlist/beat.png': png('red'), '/images/cars/fh6/123-new.webp': png('blue')}
    calls = []
    def get(url, **kwargs):
        from types import SimpleNamespace
        from urllib.parse import urlsplit
        calls.append((url, kwargs))
        return SimpleNamespace(content=responses[urlsplit(url).path], raise_for_status=lambda: None)
    rows = [{'image_source': '/images/tierlist/beat.png', 'fallback_source': '/images/cars/fh6/123-new.webp'}]
    sync_images(rows, tmp_path, get=get)
    target = cached_image_path(tmp_path, rows[0]['image_source'])
    assert Image.open(target).getpixel((0, 0)) == (255, 0, 0)
    responses['/images/tierlist/beat.png'] = png('green')
    sync_images(rows, tmp_path, get=get)
    assert Image.open(target).getpixel((0, 0)) == (0, 128, 0)
    responses['/images/tierlist/beat.png'] = b'<html>error</html>'
    sync_images(rows, tmp_path, get=get)
    assert Image.open(target).getpixel((0, 0)) == (0, 128, 0)
    assert cached_image_path(tmp_path, rows[0]['fallback_source']).is_file()
    assert all(call[1]['headers']['Cache-Control'] == 'no-cache' for call in calls)


def test_image_paths_cannot_escape_cache_or_fetch_other_origins(tmp_path):
    from forza_tracker.metacars_images import cached_image_path
    for source in ('https://example.test/car.png', '//example.test/car.png', '/images/../private.png',
                   '/images/tierlist/../../private.png', '/images/tierlist/%2e%2e/private.png',
                   '/images/tierlist/C:/private.png'):
        assert cached_image_path(tmp_path, source) is None


def test_official_fallback_uses_exact_id_and_never_substitutes_different_car(tmp_path, monkeypatch):
    _, data = catalog(tmp_path, monkeypatch)
    from forza_tracker.metacars_images import CarMedia
    media = CarMedia(data)
    assert media.source({'make': 'Honda', 'model': 'Beat', 'year': '1991'}) == '/images/cars/fh6/123-new.webp'
    assert media.source({'car_id': '999', 'make': 'Honda', 'model': 'Beat', 'year': '1991'}) == ''


def test_card_renders_car_when_screenshot_is_corrupt_in_fixed_pixel_frame(tmp_path):
    import tkinter as tk
    from types import SimpleNamespace
    from forza_tracker.ui.app import TrackerDashboard
    broken = tmp_path / 'broken.png'
    broken.write_bytes(b'not an image')
    car = tmp_path / 'car.webp'
    Image.new('RGBA', (640, 360), 'blue').save(car)
    row = {'image': str(broken), 'fallback_image': str(car), 'make': 'Honda', 'model': 'Beat',
           'class': 'B 600', 'discipline': 'road', 'tune': '', 'tuner': '', 'build': '', 'difficulty': '', 'url': ''}
    root = tk.Tk()
    root.withdraw()
    try:
        card = tk.Frame(root)
        host = SimpleNamespace(_tierlist_images=[])
        TrackerDashboard._build_tierlist_card(host, card, row, 'Meta', '#ffffff')
        frame = card.winfo_children()[0]
        assert frame.cget('height') == 110
        assert frame.pack_propagate() is None or not frame.pack_propagate()
        assert len(host._tierlist_images) == 1
        assert frame.winfo_children()[0].cget('image')
    finally:
        root.destroy()
