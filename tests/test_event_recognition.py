import cv2
import numpy as np

from forza_tracker.parser.result_parser import (
    _infer_registration_class_from_badge,
    _normalize_event_name,
)


def test_event_plan_normalizes_common_ocr_errors() -> None:
    assert _normalize_event_name("Me Daccancn dal Norikiira") == "Descenso de Norikura"
    assert _normalize_event_name("waa Persecucion por Daikoku") == "Persecución de Daikoku"
    assert _normalize_event_name("Carrera por los muelles de Tokio") == "Carrera de los muelles de Tokio"
    assert _normalize_event_name("Circuito de terra de Hirosaki") == "Circuito de tierra de Hirosaki"
    assert _normalize_event_name("Circuito de Herra de Chiheisen") == "Circuito de tierra de Chiheisen"
    assert _normalize_event_name("Tramo de Ovashirazu") == "Tramo de Oyashirazu"
    assert _normalize_event_name("Descenso del nuente Rainbow") == "Descenso del Puente Arcoíris"


def test_registration_badge_color_identifies_class() -> None:
    known_hues = {
        159: "R 998",
        109: "S2 900",
        140: "S1 800",
        174: "A 700",
        8: "B 600",
        21: "C 500",
    }
    for hue, expected in known_hues.items():
        hsv = np.zeros((80, 80, 3), dtype=np.uint8)
        hsv[:, :] = (hue, 220, 220)
        badge = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
        assert _infer_registration_class_from_badge(badge) == expected
