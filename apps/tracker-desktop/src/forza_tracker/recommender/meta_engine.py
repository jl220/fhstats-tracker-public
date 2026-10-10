from __future__ import annotations

from collections import defaultdict
from statistics import mean, pstdev
from typing import Any


def build_meta_report(
    rivals_rows: list[dict[str, Any]],
    *,
    car_class: str | None = None,
    recent_window: int = 5,
    limit: int = 3,
) -> dict[str, Any]:
    rows = [
        row
        for row in rivals_rows
        if _clean(row.get("car")) != "-"
        and (car_class is None or _clean(row.get("class")) == car_class)
        and _positive_int(row.get("rank")) is not None
    ]
    metrics = [_score_car(car, car_rows, recent_window=recent_window) for car, car_rows in _group_by_car(rows).items()]
    metrics.sort(key=lambda item: (-float(item["score"]), item["car"]))

    return {
        "status": "ok" if metrics else "no_data",
        "car_class": car_class,
        "current_meta": metrics[:limit],
        "dominant": _top(metrics, lambda item: (item["top_1"], item["top_5"], item["frequency"]), limit),
        "emerging": _top([item for item in metrics if item["recent_trend"] > 0], lambda item: (item["recent_trend"], item["score"]), limit),
        "undervalued": _top(
            [item for item in metrics if item["frequency"] <= 2 and item["top_5"] >= 1],
            lambda item: (item["score"], -item["frequency"]),
            limit,
        ),
        "beginner": _top(
            [item for item in metrics if item["frequency"] >= 2],
            lambda item: (item["consistency"] + item["coverage"] * 12.0, item["top_11"], item["score"]),
            limit,
        ),
        "expert": _top(
            [item for item in metrics if item["frequency"] >= 2],
            lambda item: (item["top_1"], 100.0 - item["consistency"], item["best_rank_score"], item["score"]),
            limit,
        ),
        "declining": _top([item for item in metrics if item["recent_trend"] < 0], lambda item: (-item["recent_trend"], item["frequency"]), limit),
    }


def _group_by_car(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, row in enumerate(rows):
        copied = dict(row)
        copied["_sequence"] = _positive_int(row.get("sequence")) or index
        grouped[_clean(row.get("car"))].append(copied)
    return grouped


def _score_car(car: str, rows: list[dict[str, Any]], *, recent_window: int) -> dict[str, Any]:
    ranks = [_positive_int(row.get("rank")) or 999 for row in rows]
    events = {_clean(row.get("event")) for row in rows if _clean(row.get("event")) != "-"}
    frequency = len(rows)
    average_position = mean(ranks)
    top_1 = sum(1 for rank in ranks if rank == 1)
    top_5 = sum(1 for rank in ranks if rank <= 5)
    top_11 = sum(1 for rank in ranks if rank <= 11)
    coverage = len(events)
    spread = pstdev(ranks) if len(ranks) > 1 else 0.0
    consistency = max(0.0, 100.0 - spread * 10.0)
    recent_trend = _recent_trend(rows, recent_window=recent_window)
    best_rank = min(ranks)
    best_rank_score = max(0.0, 120.0 - best_rank * 10.0)
    score = (
        32.0 * (top_5 / frequency)
        + 20.0 * (top_11 / frequency)
        + 18.0 * top_1
        + 10.0 * coverage
        + 12.0 * (consistency / 100.0)
        + 8.0 * recent_trend
        + best_rank_score * 0.15
        - average_position * 0.8
    )
    return {
        "car": car,
        "score": round(score, 1),
        "average_position": round(average_position, 2),
        "frequency": frequency,
        "top_1": top_1,
        "top_5": top_5,
        "top_11": top_11,
        "coverage": coverage,
        "consistency": round(consistency, 1),
        "recent_trend": round(recent_trend, 2),
        "best_rank": best_rank,
        "best_rank_score": round(best_rank_score, 1),
        "reason": _reason(top_1=top_1, top_5=top_5, top_11=top_11, coverage=coverage, frequency=frequency),
    }


def _recent_trend(rows: list[dict[str, Any]], *, recent_window: int) -> float:
    ordered = sorted(rows, key=lambda row: int(row.get("_sequence") or 0))
    if len(ordered) < 2:
        return 0.0
    midpoint = max(1, min(recent_window, len(ordered) // 2))
    early = [_positive_int(row.get("rank")) or 999 for row in ordered[:midpoint]]
    recent = [_positive_int(row.get("rank")) or 999 for row in ordered[-midpoint:]]
    return mean(early) - mean(recent)


def _top(items: list[dict[str, Any]], key: Any, limit: int) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: (*[-value for value in key(item)], item["car"]))[:limit]


def _reason(*, top_1: int, top_5: int, top_11: int, coverage: int, frequency: int) -> str:
    if top_1:
        return f"{top_1} Top 1, {top_5} Top 5 y cobertura en {coverage} circuitos."
    if top_5:
        return f"{top_5} Top 5, {top_11} Top 11 y {frequency} apariciones."
    return f"{top_11} Top 11 y cobertura en {coverage} circuitos."


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _clean(value: Any) -> str:
    if value in {None, ""}:
        return "-"
    return str(value).strip() or "-"
