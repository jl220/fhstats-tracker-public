from __future__ import annotations

import numpy as np

from forza_tracker.garage.capture import WindowFrame, crop_window_frame, frame_difference


def test_crop_window_frame_translates_desktop_coordinates() -> None:
    image = np.arange(100 * 200 * 3, dtype=np.uint8).reshape((100, 200, 3))
    frame = WindowFrame(image=image, left=1000, top=500, monitor_index=2)

    crop = crop_window_frame(frame, (1020, 510, 1120, 560))

    assert crop.shape == (50, 100, 3)
    assert np.array_equal(crop, image[10:60, 20:120])


def test_frame_difference_is_zero_for_identical_frames() -> None:
    image = np.full((40, 60, 3), 80, dtype=np.uint8)

    assert frame_difference(image, image) == 0.0
