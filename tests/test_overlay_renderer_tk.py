import inspect
from pathlib import Path
from types import SimpleNamespace

from forza_tracker.config.settings import OverlaySettings
from forza_tracker.overlay.renderer_tk import OverlayRenderer, ThumbnailCache
from forza_tracker.overlay.window_win32 import OverlayGeometry, Rect


def test_thumbnail_cache_reuses_photo_reference(tmp_path: Path) -> None:
    image_path = tmp_path / "map.png"
    image_path.write_bytes(b"image")
    calls = []

    class FakeImage:
        def copy(self):
            return self

        def thumbnail(self, size, _resample):
            calls.append(size)

    cache = ThumbnailCache(
        image_loader=lambda _path: FakeImage(),
        photo_factory=lambda image: image,
    )

    first = cache.get(image_path, (120, 80))
    second = cache.get(image_path, (120, 80))

    assert first is second
    assert calls == [(120, 80)]


def test_renderer_draws_only_when_signature_or_geometry_changes() -> None:
    class RecordingRenderer(OverlayRenderer):
        def _ensure_windows(self):
            return None

        def _draw(self, view, geometry, settings):
            self.draws += 1

    renderer = RecordingRenderer(root=None, window_adapter=None)
    renderer.draws = 0
    view = SimpleNamespace(content_signature="same")
    geometry = OverlayGeometry(
        Rect(0, 0, 200, 500),
        (Rect(0, 0, 200, 100), Rect(0, 200, 200, 300), Rect(0, 400, 200, 500)),
        Rect(250, 500, 900, 600),
    )
    settings = OverlaySettings()

    assert renderer.render(view, geometry, settings) is True
    assert renderer.render(view, geometry, settings) is False
    moved = OverlayGeometry(geometry.map_window, geometry.map_slots, Rect(251, 500, 901, 600))
    assert renderer.render(view, moved, settings) is True
    assert renderer.draws == 2


def test_renderer_uses_six_independent_surfaces_without_shared_panels() -> None:
    source = inspect.getsource(OverlayRenderer)

    assert "_map_windows" in source
    assert "_recommendation_windows" in source
    assert "track.name" not in source
    assert "track.discipline" not in source
    assert "def show_preview" not in source
    assert "highlightbackground" not in source
