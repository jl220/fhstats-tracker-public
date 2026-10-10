from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MetaCategoryCard:
    label: str
    value: str
    detail: str
    category: str


META_CATEGORY_ORDER = (
    ("current_meta", "Meta actual", "score"),
    ("emerging", "Meta emergente", "recent_trend"),
    ("undervalued", "Infravalorado", "score"),
    ("dominant", "Dominante", "top_5"),
    ("beginner", "Principiante", "consistency"),
    ("expert", "Experto", "top_1"),
)


def build_meta_category_cards(meta: dict[str, Any] | None) -> list[MetaCategoryCard]:
    payload = meta if isinstance(meta, dict) else {}
    cards: list[MetaCategoryCard] = []
    for category, label, metric in META_CATEGORY_ORDER:
        item = _first_item(payload.get(category))
        if item is None:
            cards.append(MetaCategoryCard(label, "-", "Sin datos suficientes", category))
            continue
        cards.append(
            MetaCategoryCard(
                label=label,
                value=str(item.get("car") or "-"),
                detail=_metric_detail(item, metric),
                category=category,
            )
        )
    return cards


def _first_item(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, list) or not value:
        return None
    item = value[0]
    return item if isinstance(item, dict) else None


def _metric_detail(item: dict[str, Any], metric: str) -> str:
    value = item.get(metric)
    if metric == "recent_trend":
        number = _number(value)
        return f"{number:+.1f} tendencia" if number is not None else "Tendencia nueva"
    if metric == "score":
        number = _number(value)
        return f"{number:.1f} pts" if number is not None else "Puntuacion activa"
    if metric == "top_5":
        count = _integer(value)
        return f"{count} Top 5" if count is not None else "Dominio Rivals"
    if metric == "consistency":
        number = _number(value)
        return f"{number:.1f}% consistencia" if number is not None else "Estable"
    if metric == "top_1":
        count = _integer(value)
        return f"{count} Top 1" if count is not None else "Pico alto"
    return "Activo"


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
