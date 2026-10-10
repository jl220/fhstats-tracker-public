from __future__ import annotations

import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import cv2
import numpy as np

from forza_tracker.models.result import EventPlanSlots, ParsedResult
from forza_tracker.models.screen_type import ScreenType
from forza_tracker.parser.highlighted_row import HighlightedRowLocator
from forza_tracker.parser.ocr import TesseractOcr
from forza_tracker.parser.title_extractor import TitleExtractor

logger = logging.getLogger(__name__)

_TIME_RE = re.compile(r"\d{1,2}:\d{2}[,.]\d{3}")
_CLASS_RE = re.compile(r"\b(R|X|S2|S1|A|B|C|D)\s*([0-9O]{3})", re.IGNORECASE)
_KNOWN_CLASSES = [
    ("D", "400"),
    ("C", "500"),
    ("B", "600"),
    ("A", "700"),
    ("S1", "800"),
    ("S2", "900"),
    ("R", "998"),
]
_PROGRESS_RE = re.compile(r"(\d{1,3})\s*%")
_EVENT_RE = re.compile(r"(?:evento\s*)?(\d)\s*/\s*(\d)", re.IGNORECASE)
_KNOWN_EVENT_NAMES = [
    "Circuito de autopista", "Circuito de Narai-juku", "Circuito de Shirakawa",
    "Circuito de Shimanoyama", "Circuito de Hokubu", "Circuito de Soni",
    "Circuito de Daikoku", "Circuito del barrio electronico", "Circuito de la isla legendaria",
    "Circuito de Irokawa", "Carrera por el festival", "Carrera por Venus",
    "Carrera por el parque de la costa", "Carrera por Ito", "Carrera por Tateyama Kurobe",
    "Carrera por Shimanoyama", "Carrera por Satta", "Carrera por la costa",
    "Carrera del tren de Tokio", "Carrera por Shikisai", "El Coloso", "La Goliat",
    "Persecución de Daikoku", "Descenso de Norikura", "Carrera de Nachi",
    "Ascenso de Hokubu", "Carrera de Okishinaimura", "Carrera de los girasoles",
    "Kita Ine", "Descenso del Puente Arcoíris", "Carrera de Shimanoyama",
    "Persecución del festival", "Ascenso de Matsumi", "Persecución de Minami",
    "Descenso del río", "Carrera de los cedros", "Carrera de los muelles de Tokio",
    "Circuito de tierra de Chiheisen", "Circuito de tierra de Sotoyama",
    "Circuito de tierra de Hirosaki", "Circuito de tierra de los Girasoles",
    "Circuito de tierra del Bosque de Bambu", "Circuito de tierra de Ine",
    "Circuito de tierra del Estadio Horizon", "Circuito de tierra de Tayaki",
    "Circuito de tierra de Kawazu Nanadaru", "Circuito de tierra de Sekibe",
    "Tramo de Nukabira", "Tramo de la cascada", "Tramo de Hokubu", "Tramo de Ito",
    "Tramo del Aerodromo", "Tramo del campo de cerezos", "Tramo de Kinkaku-ji",
    "Tramo de la isla legendaria", "Tramo de Oyashirazu", "Tramo de Takashiro",
    "El Gran Desafio", "Circuito campo a través de la isla legendaria",
    "Circuito campo a través de Naruo", "Circuito campo a través del bosque nevado",
    "Circuito campo a través de Oka", "Circuito campo a través de Edogawa",
    "Circuito campo a través de los muelles", "Circuito campo a través del estadio",
    "Circuito campo a través de Nangan", "Campo a través de las tierras altas de Soni",
    "Campo a través de Ruriko-ji", "Campo a través de Takashiro",
    "Campo a través alpino de Tateyama", "Campo a través del parque eólico",
    "Campo a través de Yahikoyama", "Campo a través de Shimanoyama",
    "Campo a través de Shinjuku Gyoen", "Campo a través del templo",
    "Campo a través de Izu", "El Titán",
]


class ResultParser:
    def __init__(self, default_player: str, ocr: TesseractOcr | None = None) -> None:
        self._default_player = default_player
        self._highlighted_row_locator = HighlightedRowLocator()
        self._ocr = ocr or TesseractOcr(enabled=False, tesseract_cmd=None)
        self._title_extractor = TitleExtractor(self._ocr)

    def parse(
        self,
        screenshot: np.ndarray,
        screenshot_path: Path,
        timestamp: datetime,
        screen_type: ScreenType,
    ) -> ParsedResult:
        circuit_name = None
        position: int | None = None
        starting_position: int | None = None
        championship_position: int | None = None
        event_number: int | None = None
        event_total: int | None = None
        discipline: str | None = None
        row_detection_confidence: float | None = None
        raw_row_text: str | None = None
        car: str | None = None
        car_class: str | None = None
        class_crop_path: Path | None = None
        progress_percent: int | None = None
        best_lap: str | None = None
        total_time: str | None = None
        event_type: str | None = None
        event_plan: list[str] | None = None
        event_plan_slots: EventPlanSlots | None = None
        top_car: str | None = None

        if screen_type == ScreenType.EVENT_REGISTRATION:
            raw_row_text = _clean_text(self._ocr.read_line(_event_registration_header_crop(screenshot)))
            event_type = "online_matchmaking"
            event_number, event_total = _extract_event_number(raw_row_text)
            event_plan_slots = _extract_event_plan_slots(screenshot, self._ocr)
            event_plan = _complete_event_plan(event_plan_slots)
            car_class = _extract_registration_class(screenshot, self._ocr)
        elif screen_type == ScreenType.MATCHMAKING_SEARCH:
            circuit_name = None
            raw_row_text = _best_text(
                self._ocr.read_line(_matchmaking_status_crop(screenshot)),
                self._ocr.read_line(screenshot),
            )
            event_type = "matchmaking_search"
        elif screen_type == ScreenType.MATCHMAKING_FOUND:
            circuit_name = None
            raw_row_text = _best_text(
                self._ocr.read_line(_matchmaking_status_crop(screenshot)),
                self._ocr.read_line(screenshot),
            )
            event_type = "matchmaking_found"
        elif screen_type == ScreenType.STARTING_GRID:
            circuit_name = self._title_extractor.extract(screenshot)
            event_number, event_total = _extract_event_number(_clean_text(self._ocr.read_line(_event_header_crop(screenshot))))
            discipline = (
                _extract_discipline(circuit_name)
                or _match_discipline_icon(screenshot)
                or _extract_discipline(_clean_text(self._ocr.read_line(_discipline_crop(screenshot))))
            )
            class_crop = _top_class_crop(screenshot)
            car_class = _extract_class(_clean_text(self._ocr.read_line(class_crop))) or _infer_class_from_crop(class_crop)
            top_car = _extract_top_car(screenshot, self._ocr)
            class_crop_path = _save_class_crop(screenshot, screenshot_path)
        elif screen_type == ScreenType.CHAMPIONSHIP_RESULT:
            circuit_name = self._title_extractor.extract(screenshot)
            championship_position = _extract_championship_position(screenshot, self._ocr, self._default_player)
            car_class = _extract_class(_clean_text(self._ocr.read_line(_championship_player_class_crop(screenshot, self._ocr, self._default_player))))

        if screen_type in {ScreenType.STARTING_GRID, ScreenType.RACE_RESULT}:
            if circuit_name is None:
                circuit_name = self._title_extractor.extract(screenshot)
            highlighted_row = self._highlighted_row_locator.locate(screenshot)
            if highlighted_row is not None:
                row_detection_confidence = highlighted_row.confidence
                if screen_type == ScreenType.STARTING_GRID:
                    starting_position = highlighted_row.row_index
                else:
                    position = highlighted_row.row_index

                row_crop = _crop_box(screenshot, highlighted_row.x, highlighted_row.y, highlighted_row.width, highlighted_row.height)
                raw_row_text = _clean_text(self._ocr.read_line(row_crop))
                car = _extract_row_car(row_crop, self._ocr, raw_row_text, self._default_player, car_class)
                if not car and top_car:
                    car = top_car
                car_class = car_class or _extract_class(raw_row_text)

                if screen_type == ScreenType.RACE_RESULT:
                    right1 = _clean_text(self._ocr.read_line(_crop_ratio(row_crop, 0.70, 0.84)))
                    right2 = _clean_text(self._ocr.read_line(_crop_ratio(row_crop, 0.84, 0.99)))
                    car_class = car_class or _extract_class(" ".join(text for text in [right1, right2] if text))
                    progress_percent = _extract_progress(right1)
                    times = _extract_times(" ".join(text for text in [right1, right2, raw_row_text] if text))
                    if len(times) >= 2:
                        best_lap = times[0]
                        total_time = times[1]
                    elif len(times) == 1:
                        total_time = times[0]
                elif screen_type == ScreenType.STARTING_GRID:
                    car_class = car_class or _extract_class(raw_row_text)

                logger.info(
                    "Parsed row screen_type=%s circuit=%s row=%s car=%s class=%s progress=%s best_lap=%s total_time=%s confidence=%.3f",
                    screen_type.value,
                    circuit_name,
                    highlighted_row.row_index,
                    car,
                    car_class,
                    progress_percent,
                    best_lap,
                    total_time,
                    highlighted_row.confidence,
                )
            else:
                logger.info("No highlighted player row found for screen_type=%s circuit=%s", screen_type.value, circuit_name)

        if screen_type == ScreenType.STARTING_GRID and not car and top_car:
            car = top_car

        return ParsedResult(
            screenshot_path=screenshot_path,
            screen_type=screen_type,
            player=self._default_player,
            circuit_name=circuit_name,
            position=position,
            starting_position=starting_position,
            event_type=event_type,
            event_number=event_number,
            event_total=event_total,
            discipline=discipline,
            car=car,
            car_class=car_class,
            class_crop_path=class_crop_path,
            championship_position=championship_position,
            progress_percent=progress_percent,
            best_lap=best_lap,
            total_time=total_time,
            row_detection_confidence=row_detection_confidence,
            raw_row_text=raw_row_text,
            timestamp=timestamp,
            event_plan=event_plan,
            event_plan_slots=event_plan_slots,
        )


def _crop_box(image: np.ndarray, x: int, y: int, width: int, height: int) -> np.ndarray:
    return image[y : y + height, x : x + width]


def _crop_ratio(image: np.ndarray, start: float, end: float) -> np.ndarray:
    _, width = image.shape[:2]
    return image[:, int(width * start) : int(width * end)]


def _crop_screen_ratio(image: np.ndarray, x: float, y: float, width: float, height: float) -> np.ndarray:
    image_height, image_width = image.shape[:2]
    left = int(image_width * x)
    top = int(image_height * y)
    right = int(image_width * (x + width))
    bottom = int(image_height * (y + height))
    return image[top:bottom, left:right]


def _top_class_crop(image: np.ndarray) -> np.ndarray:
    return _crop_screen_ratio(image, 0.02, 0.035, 0.16, 0.055)


def _top_car_crop(image: np.ndarray) -> np.ndarray:
    return _crop_screen_ratio(image, 0.085, 0.030, 0.32, 0.065)


def _extract_top_car(image: np.ndarray, ocr: TesseractOcr) -> str | None:
    crops = [
        _top_car_crop(image),
        _crop_screen_ratio(image, 0.095, 0.035, 0.27, 0.055),
    ]
    candidates = [_clean_car_text(ocr.read_line(crop)) for crop in crops]
    return _best_car_name(candidates)


def _extract_row_car(
    row_crop: np.ndarray,
    ocr: TesseractOcr,
    raw_row_text: str | None,
    player: str,
    car_class: str | None,
) -> str | None:
    primary = _clean_car_text(ocr.read_line(_crop_ratio(row_crop, 0.30, 0.58)))
    if primary:
        return primary

    candidates = [
        _clean_car_text(ocr.read_line(_crop_ratio(row_crop, 0.28, 0.62))),
        _clean_car_text(ocr.read_line(_crop_ratio(row_crop, 0.34, 0.68))),
        _guess_car_from_row_text(raw_row_text, player, car_class),
    ]
    return _best_car_name(candidates)


def _guess_car_from_row_text(raw_row_text: str | None, player: str, car_class: str | None) -> str | None:
    if not raw_row_text:
        return None
    cleaned = _clean_text(raw_row_text)
    if not cleaned:
        return None
    if player:
        cleaned = re.sub(re.escape(player), " ", cleaned, flags=re.IGNORECASE)
    if car_class:
        class_name, class_score = car_class.split(" ", 1)
        cleaned = re.sub(rf"\b{re.escape(class_name)}\s*{re.escape(class_score)}\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = _CLASS_RE.sub(" ", cleaned)
    cleaned = _TIME_RE.sub(" ", cleaned)
    cleaned = _PROGRESS_RE.sub(" ", cleaned)
    cleaned = re.sub(r"^\s*\d{1,2}\b", " ", cleaned)
    tokens = [
        token
        for token in cleaned.split()
        if not token.isdigit() and not re.fullmatch(r"[\W_]+", token)
    ]
    return _clean_car_text(" ".join(tokens))


def _best_car_name(candidates: list[str | None]) -> str | None:
    clean_candidates = [candidate for candidate in candidates if candidate]
    if not clean_candidates:
        return None
    return max(clean_candidates, key=_car_name_score)


def _car_name_score(value: str) -> int:
    score = len(value)
    if re.search(r"\d{4}", value):
        score += 12
    if re.search(r"\b(?:Coupe|Coup.|FE|GT|GTR|RWD|AWD|RS|SRT|Honda|Ford|Mazda|Toyota|Subaru|Porsche|Lamborghini|Mercedes|Chevrolet|McLaren)\b", value, re.IGNORECASE):
        score += 18
    if re.search(r"\b(?:Piloto|Clase|Evento|Horizon|Forza|Reproductor|GPU|FPS)\b", value, re.IGNORECASE):
        score -= 80
    return score


def _clean_car_text(text: str | None) -> str | None:
    cleaned = _clean_text(text)
    if not cleaned:
        return None
    cleaned = re.sub(r"\b(?:Piloto|Coche|Clase|Evento)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = _CLASS_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\b(?:CR|PE|XP)\b.*$", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^[\W_]+|[\W_]+$", " ", cleaned)
    cleaned = " ".join(cleaned.split())
    if not cleaned or not re.search(r"[A-Za-z]", cleaned):
        return None
    if re.search(r"\b(?:Horizon|Forza|Reproductor|Mostrar|Tarjeta|Jugador|GPU|FPS)\b", cleaned, re.IGNORECASE):
        return None
    cleaned = _normalize_car_name(cleaned)
    return cleaned if len(cleaned) >= 3 else None


def _normalize_car_name(value: str) -> str:
    normalized = value.strip()
    replacements = [
        (r"^\s*[Xx][ -]?5\s+FE\b", "Mazda MX-5 FE"),
        (r"\b(?:Lambo|mbo)\s+SCV12\b", "Lamborghini Essenza SCV12"),
        (r"^\s*(?:Toy\.?|y\.)\s+Starlet\s+'?96\b", "Toyota Starlet '96"),
        (r"^\s*\)?odge\b", "Dodge"),
        (r"^\s*(?:nda|n da)\s+\$?800\b", "Honda S800"),
    ]
    for pattern, replacement in replacements:
        normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
    return " ".join(normalized.split())


def _event_header_crop(image: np.ndarray) -> np.ndarray:
    return _crop_screen_ratio(image, 0.70, 0.20, 0.13, 0.06)


def _discipline_crop(image: np.ndarray) -> np.ndarray:
    return _crop_screen_ratio(image, 0.65, 0.555, 0.19, 0.075)


def _discipline_icon_region(image: np.ndarray) -> np.ndarray:
    return _crop_screen_ratio(image, 0.64, 0.55, 0.22, 0.22)


def _event_registration_crop(image: np.ndarray) -> np.ndarray:
    return _crop_screen_ratio(image, 0.05, 0.0, 0.75, 0.22)


def _event_registration_header_crop(image: np.ndarray) -> np.ndarray:
    return _crop_screen_ratio(image, 0.20, 0.17, 0.40, 0.06)


def _event_registration_class_crop(image: np.ndarray) -> np.ndarray:
    return _crop_screen_ratio(image, 0.770, 0.170, 0.050, 0.090)


def _matchmaking_status_crop(image: np.ndarray) -> np.ndarray:
    return _crop_screen_ratio(image, 0.0, 0.0, 0.95, 0.32)


def _event_plan_name_crops(image: np.ndarray, index: int) -> list[np.ndarray]:
    base_y = [0.225, 0.350, 0.470][index]
    crops: list[np.ndarray] = []
    for y_offset in [-0.005, 0.005]:
        for x in [0.245, 0.265]:
            crops.append(_crop_screen_ratio(image, x, base_y + y_offset, 0.355, 0.040))
    return crops


def _event_plan_name_fallback_crops(image: np.ndarray, index: int) -> list[np.ndarray]:
    base_y = [0.225, 0.350, 0.470][index]
    crops: list[np.ndarray] = []
    for y_offset in [-0.010, 0.0, 0.010, 0.015]:
        for x in [0.245, 0.265]:
            crops.append(_crop_screen_ratio(image, x, base_y + y_offset, 0.355, 0.040))
    return crops


def _championship_player_class_crop(image: np.ndarray, ocr: TesseractOcr, player: str) -> np.ndarray:
    row = _find_championship_player_row(image, ocr, player)
    if row is None:
        return image[0:1, 0:1]
    height, width = image.shape[:2]
    y = int(height * (0.253 + row * 0.05))
    return image[y + 5 : y + int(height * 0.047), int(width * 0.605) : int(width * 0.675)]


def _save_class_crop(image: np.ndarray, screenshot_path: Path) -> Path:
    crop = _top_class_crop(image)
    output_dir = screenshot_path.parent / "class_crops"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{screenshot_path.stem}_class.png"
    cv2.imwrite(str(output_path), crop)
    return output_path


def _clean_text(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = " ".join(text.replace("|", " ").split())
    return cleaned or None


def _best_text(*values: str | None) -> str | None:
    cleaned = [_clean_text(value) for value in values]
    candidates = [value for value in cleaned if value]
    if not candidates:
        return None
    with_event = [value for value in candidates if _EVENT_RE.search(value)]
    if with_event:
        return max(with_event, key=len)
    return max(candidates, key=len)


def _extract_event_plan_slots(image: np.ndarray, ocr: TesseractOcr) -> EventPlanSlots:
    def extract(index: int) -> str | None:
        candidates = [_clean_event_name(ocr.read_line(crop)) for crop in _event_plan_name_crops(image, index)]
        text = _best_event_name(candidates)
        if not _is_known_event_name(text):
            candidates.extend(
                _clean_event_name(ocr.read_line(crop))
                for crop in _event_plan_name_fallback_crops(image, index)
            )
            text = _best_event_name(candidates)
        return text if _is_known_event_name(text) else None

    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="event-ocr") as executor:
        names = tuple(executor.map(extract, range(3)))
    return names


def _complete_event_plan(slots: EventPlanSlots) -> list[str] | None:
    if not all(slots):
        return None
    return [name for name in slots if name is not None]


def _extract_event_plan(image: np.ndarray, ocr: TesseractOcr) -> list[str] | None:
    return _complete_event_plan(_extract_event_plan_slots(image, ocr))


def _extract_registration_class(image: np.ndarray, ocr: TesseractOcr) -> str | None:
    crop = _event_registration_class_crop(image)
    inferred = _infer_registration_class_from_badge(crop)
    if inferred:
        return inferred
    text = _clean_text(ocr.read_line(crop, whitelist="XRS12ABCD0123456789"))
    if text:
        normalized = re.sub(r"[^A-Z0-9]", "", text.upper())
        prefixes = [
            ("S2", "S2 900"),
            ("S1", "S1 800"),
            ("R", "R 998"),
            ("A", "A 700"),
            ("B", "B 600"),
            ("C", "C 500"),
            ("D", "D 400"),
        ]
        for prefix, class_name in prefixes:
            if prefix in normalized:
                return class_name
    return None


def _infer_registration_class_from_badge(image: np.ndarray) -> str | None:
    if image.size == 0:
        return None
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    saturated = (hsv[:, :, 1] >= 110) & (hsv[:, :, 2] >= 100)
    if int(saturated.sum()) < 40:
        return None
    hues = hsv[:, :, 0][saturated]
    counts = np.bincount(hues, minlength=180)
    dominant_hue = int(np.argmax(counts))
    known_hues = {
        "R 998": 159,
        "S2 900": 109,
        "S1 800": 140,
        "A 700": 174,
        "B 600": 8,
        "C 500": 21,
    }
    class_name, class_hue = min(
        known_hues.items(),
        key=lambda item: min(abs(dominant_hue - item[1]), 180 - abs(dominant_hue - item[1])),
    )
    distance = min(abs(dominant_hue - class_hue), 180 - abs(dominant_hue - class_hue))
    return class_name if distance <= 12 else None


def _best_event_name(candidates: list[str | None]) -> str | None:
    clean_candidates = [candidate for candidate in candidates if candidate]
    if not clean_candidates:
        return None
    best = max(clean_candidates, key=_event_name_score)
    return _normalize_event_name(best)


def _is_known_event_name(value: str | None) -> bool:
    if not value:
        return False
    key = _event_match_key(value)
    return any(key == _event_match_key(candidate) for candidate in _KNOWN_EVENT_NAMES)


def _event_name_score(value: str) -> int:
    normalized = value.lower()
    score = 100 - abs(len(value) - 28)
    for keyword in ["circuito", "tramo", "carrera", "campo"]:
        if keyword in normalized:
            score += 60
    if " de " in normalized or " por " in normalized:
        score += 12
    if re.match(r"^(?:circuito|tramo|carrera|campo)\b", normalized):
        score += 18
    if " da " in normalized or "virculto" in normalized or "rculio" in normalized:
        score -= 30
    for bad in [
        "inscripcion",
        "inscripción",
        "accediendo",
        "horizon play",
        "evento",
        "siguiente",
        "actual",
        "pendiente",
        "vuelta",
        "vueltas",
    ]:
        if bad in normalized:
            score -= 180
    return score


def _clean_event_name(text: str | None) -> str | None:
    cleaned = _clean_text(text)
    if not cleaned:
        return None
    if re.fullmatch(r".*\b(?:inscripci[oó]n\s+al\s+evento|accediendo|horizon\s+play)\b.*", cleaned, re.IGNORECASE):
        return None
    keyword_match = re.search(r"\b(?:Circuito|Tramo|Carrera|Campo)\b", cleaned, re.IGNORECASE)
    if keyword_match:
        cleaned = cleaned[keyword_match.start() :]
    cleaned = re.sub(r"\bS\w{0,4}iente\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bSiguiente\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bCompletad[ao]\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bEn\s*cur(?:so|se)\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bEncurso\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b\d{1,2}[,.]\d\s*KM(?:\s*-\s*\d+\s*VUELTAS)?\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b\d+\s*VUELTAS\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = " ".join(cleaned.split(" - ")[0].split())
    cleaned = re.sub(r"\s+[A-Za-z]\s*[\]}).]*$", "", cleaned).strip()
    cleaned = re.sub(r"[^\wáéíóúÁÉÍÓÚñÑ#' -]+$", "", cleaned).strip()
    cleaned = _remove_trailing_ocr_noise(cleaned)
    cleaned = _normalize_event_name(cleaned)
    return cleaned or None


def _normalize_event_name(value: str) -> str:
    normalized = value.strip()
    replacements = [
        (r"^.*Dacc\w*\s+d[ae]l?\s+Norik\w*$", "Descenso de Norikura"),
        (r"^.*Persecuci[oó]n\s+por\s+Daikoku.*$", "Persecución de Daikoku"),
        (r"^.*Carrera\s+por\s+los\s+muelles\s+de\s+Tokio.*$", "Carrera de los muelles de Tokio"),
        (r"\b(?:Naral|Narai|Naral-|Narai-)[\s-]*(?:julor|juior|iuku|juku|juko)\b", "Narai-juku"),
        (r"\bKinkaku[\s-]*(?:ii|ji|j1|jl|jii|iii)\b", "Kinkaku-ji"),
        (r"\btaivaki\b", "taiyaki"),
        (r"\btayiaki\b", "taiyaki"),
        (r"\btraves\b", "través"),
        (r"\bbambu\b", "bambú"),
        (r"\btiarra\b", "tierra"),
        (r"\b(?:terra|Herra)\b", "tierra"),
        (r"\bOvashirazu\b", "Oyashirazu"),
        (r"\bnuente\s+Rainbow\b", "Puente Arcoíris"),
        (r"\bda tierra\b", "de tierra"),
        (r"\bde ta?iyaki\b", "de Taiyaki"),
        (r"\bde oka\b", "de Oka"),
        (r"\bpor izu\b", "por Izu"),
        (r"\bde ito\b", "de Ito"),
    ]
    for pattern, replacement in replacements:
        normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return _closest_known_event(normalized)


def _closest_known_event(value: str) -> str:
    key = _event_match_key(value)
    if not key:
        return value
    best_name = value
    best_score = 0.0
    for candidate in _KNOWN_EVENT_NAMES:
        score = SequenceMatcher(None, key, _event_match_key(candidate)).ratio()
        if score > best_score:
            best_score = score
            best_name = candidate
    return best_name if best_score >= 0.72 else value


def _event_match_key(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(character for character in decomposed if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9]+", " ", ascii_value.lower()).strip()


def _remove_trailing_ocr_noise(value: str) -> str:
    allowed_short_names = {"ito", "izu", "oka"}
    noisy_tokens = {"bea", "bees", "bist", "li", "ls", "es", "sti", "rss", "wv", "eee", "lien", "yr", "he"}
    tokens = value.split()
    while len(tokens) > 1:
        normalized = re.sub(r"[^A-Za-zÁÉÍÓÚáéíóúÑñ-]", "", tokens[-1]).lower()
        if not normalized:
            tokens.pop()
            continue
        if normalized in allowed_short_names:
            break
        if normalized in noisy_tokens or len(normalized) <= 2:
            tokens.pop()
            continue
        break
    return " ".join(tokens)


def _extract_times(text: str) -> list[str]:
    times: list[str] = []
    for match in _TIME_RE.findall(text):
        normalized = match.replace(".", ",")
        if normalized not in times:
            times.append(normalized)
    return times


def _extract_progress(text: str | None) -> int | None:
    if not text:
        return None
    match = _PROGRESS_RE.search(text)
    if not match:
        return None
    value = int(match.group(1))
    return value if 0 <= value <= 100 else None


def _extract_class(text: str | None) -> str | None:
    if not text:
        return None
    normalized = _normalize_class_text(text)
    compact = re.sub(r"[^A-Z0-9]", "", normalized)
    compact = re.sub(r"S[IIL]800", "S1800", compact)
    for class_name, score in _KNOWN_CLASSES:
        if f"{class_name}{score}" in compact:
            return f"{class_name} {score}"

    match = _CLASS_RE.search(normalized)
    if not match:
        return None
    class_name = match.group(1).upper()
    score = match.group(2).upper().replace("O", "0")
    if (class_name, score) not in _KNOWN_CLASSES:
        return None
    return f"{class_name} {score}"


def _normalize_class_text(text: str) -> str:
    normalized = text.upper()
    normalized = normalized.replace("$2", "S2").replace("§2", "S2")
    normalized = normalized.replace("$1", "S1").replace("§1", "S1")
    normalized = re.sub(r"\bS[IIL]\s*800\b", "S1 800", normalized)
    normalized = normalized.replace("€", "C").replace("¢", "C").replace("©", "C")
    normalized = normalized.replace("AY ", "A ").replace("A Y ", "A ")
    normalized = normalized.replace("À", "A")
    return normalized


def _infer_class_from_crop(image: np.ndarray) -> str | None:
    height, width = image.shape[:2]
    badge = image[int(height * 0.15) : int(height * 0.85), 0 : int(width * 0.13)]
    if badge.size == 0:
        return None
    hsv = cv2.cvtColor(badge, cv2.COLOR_BGR2HSV)
    hue = float(hsv[:, :, 0].mean())
    saturation = float(hsv[:, :, 1].mean())
    value = float(hsv[:, :, 2].mean())
    if saturation < 80 or value < 120:
        return None
    if 30 <= hue <= 52:
        return "C 500"
    if 92 <= hue <= 115:
        return "S1 800"
    return None


def _extract_event_number(text: str | None) -> tuple[int | None, int | None]:
    if not text:
        return None, None
    match = _EVENT_RE.search(text)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _extract_discipline(text: str | None) -> str | None:
    if not text:
        return None
    normalized = text.lower()
    if "callej" in normalized:
        return "callejera"
    if "campo" in normalized or "cross" in normalized:
        return "campo a traves"
    if "tierra" in normalized or "dirt" in normalized:
        return "tierra"
    if "carretera" in normalized or "road" in normalized:
        return "carretera"
    return None


def _match_discipline_icon(image: np.ndarray) -> str | None:
    template_map = {
        "dirt.png": "tierra",
        "road.png": "carretera",
        "circuit.png": "carretera",
        "street.png": "callejera",
        "cross_country.png": "campo a traves",
    }
    templates_dir = Path("templates")
    region = cv2.cvtColor(_discipline_icon_region(image), cv2.COLOR_BGR2GRAY)
    best_name: str | None = None
    best_confidence = 0.0

    for filename, discipline in template_map.items():
        template_path = templates_dir / filename
        if not template_path.exists():
            continue
        template = cv2.imread(str(template_path), cv2.IMREAD_GRAYSCALE)
        if template is None:
            continue
        for scale in [0.8, 0.9, 1.0, 1.1, 1.2]:
            width = max(1, int(template.shape[1] * scale))
            height = max(1, int(template.shape[0] * scale))
            candidate = cv2.resize(template, (width, height), interpolation=cv2.INTER_AREA)
            if candidate.shape[0] > region.shape[0] or candidate.shape[1] > region.shape[1]:
                continue
            match = cv2.matchTemplate(region, candidate, cv2.TM_CCOEFF_NORMED)
            _, confidence, _, _ = cv2.minMaxLoc(match)
            if confidence > best_confidence:
                best_confidence = float(confidence)
                best_name = discipline

    return best_name if best_confidence >= 0.70 else None


def _extract_championship_position(image: np.ndarray, ocr: TesseractOcr, player: str) -> int | None:
    row = _find_championship_player_row(image, ocr, player)
    return None if row is None else row + 1


def _find_championship_player_row(image: np.ndarray, ocr: TesseractOcr, player: str) -> int | None:
    height, width = image.shape[:2]
    player_key = player.lower()
    for row in range(10):
        y = int(height * (0.253 + row * 0.05))
        crop = image[y : y + int(height * 0.05), int(width * 0.18) : int(width * 0.76)]
        text = _clean_text(ocr.read_line(crop)) or ""
        if player_key in text.lower():
            return row
    return None
