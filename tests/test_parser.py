from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import pytest

from forza_tracker.config.settings import load_settings
from forza_tracker.models.screen_type import ScreenType
from forza_tracker.parser.ocr import TesseractOcr
from forza_tracker.parser import result_parser as result_parser_module
from forza_tracker.parser.result_parser import (
    ResultParser,
    _clean_car_text,
    _complete_event_plan,
)


ROOT = Path(__file__).resolve().parents[1]
TEST_IMAGES = Path(__file__).resolve().parent


def _parser() -> ResultParser:
    settings = load_settings(ROOT / "config" / "tracker.yaml")
    if settings.ocr.tesseract_cmd and not settings.ocr.tesseract_cmd.exists():
        pytest.skip(f"Tesseract executable not found: {settings.ocr.tesseract_cmd}")
    return ResultParser(
        default_player=settings.app.player_name,
        ocr=TesseractOcr(settings.ocr.enabled, settings.ocr.tesseract_cmd),
    )


def test_parser_reads_starting_grid_player_row() -> None:
    screenshot = TEST_IMAGES / "starting_grid_20260614_230438.png"
    image = cv2.imread(str(screenshot))

    result = _parser().parse(
        screenshot=image,
        screenshot_path=screenshot,
        timestamp=datetime.now(timezone.utc),
        screen_type=ScreenType.STARTING_GRID,
    )

    assert result.starting_position == 7
    assert result.car == "Shelby Daytona"
    assert result.car_class == "A 700"


def test_parser_uses_top_car_when_highlighted_row_is_missing() -> None:
    class MissingRowLocator:
        def locate(self, _screenshot):
            return None

    screenshot = TEST_IMAGES / "starting_grid_20260614_230438.png"
    image = cv2.imread(str(screenshot))
    parser = _parser()
    parser._highlighted_row_locator = MissingRowLocator()

    result = parser.parse(
        screenshot=image,
        screenshot_path=screenshot,
        timestamp=datetime.now(timezone.utc),
        screen_type=ScreenType.STARTING_GRID,
    )

    assert "Shelby Cobra Daytona" in (result.car or "")
    assert result.car_class == "A 700"


def test_parser_reads_race_result_player_row() -> None:
    screenshot = TEST_IMAGES / "race_result_20260615_002154.png"
    image = cv2.imread(str(screenshot))

    result = _parser().parse(
        screenshot=image,
        screenshot_path=screenshot,
        timestamp=datetime.now(timezone.utc),
        screen_type=ScreenType.RACE_RESULT,
    )

    assert result.position == 1
    assert result.car == "SUBARU BRZ"
    assert result.progress_percent == 100
    assert result.total_time == "02:33,900"


def test_car_cleaning_normalizes_common_ocr_variants() -> None:
    assert _clean_car_text("X-5 FE '94") == "Mazda MX-5 FE '94"
    assert _clean_car_text("Lambo SCV12") == "Lamborghini Essenza SCV12"
    assert _clean_car_text("Toy. Starlet '96") == "Toyota Starlet '96"
    assert _clean_car_text(")odge Viper FE '99") == "Dodge Viper FE '99"


def test_event_registration_exposes_partial_slots_without_completing_plan(monkeypatch) -> None:
    class FakeOcr:
        def read_line(self, _image):
            return "Inscripción al evento"

    slots = ("Circuito A", None, "Circuito C")
    monkeypatch.setattr(result_parser_module, "_extract_event_plan_slots", lambda *_: slots)
    monkeypatch.setattr(result_parser_module, "_extract_registration_class", lambda *_: "A 800")

    result = ResultParser("Player", ocr=FakeOcr()).parse(
        screenshot=np.zeros((1152, 2048, 3), dtype=np.uint8),
        screenshot_path=Path("registration.png"),
        timestamp=datetime.now(timezone.utc),
        screen_type=ScreenType.EVENT_REGISTRATION,
    )

    assert result.event_plan_slots == slots
    assert result.event_plan is None
    assert result.to_json_dict()["event_plan_slots"] == ["Circuito A", None, "Circuito C"]


def test_complete_event_plan_keeps_existing_all_or_none_contract() -> None:
    assert _complete_event_plan(("Circuito A", None, "Circuito C")) is None
    assert _complete_event_plan(("Circuito A", "Circuito B", "Circuito C")) == [
        "Circuito A",
        "Circuito B",
        "Circuito C",
    ]
