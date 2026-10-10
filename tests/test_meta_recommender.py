from __future__ import annotations

import pytest

from forza_tracker import local_recommender
from forza_tracker.recommender.meta_engine import build_meta_report


def _entry(
    car: str,
    rank: int,
    *,
    event: str = "Circuit A",
    car_class: str = "B 600",
    sequence: int = 1,
) -> dict[str, object]:
    return {
        "event": event,
        "class": car_class,
        "rank": rank,
        "player": f"Driver {sequence}",
        "car": car,
        "time": f"01:{sequence:02d}.000",
        "sequence": sequence,
    }


def test_meta_report_identifies_dominant_and_current_meta() -> None:
    rows = [
        _entry("Honda Beat", 1, event="Circuit A", sequence=1),
        _entry("Honda Beat", 2, event="Circuit B", sequence=2),
        _entry("Honda Beat", 5, event="Circuit C", sequence=3),
        _entry("Mazda MX-5", 8, event="Circuit A", sequence=4),
    ]

    report = build_meta_report(rows, car_class="B 600")

    assert report["current_meta"][0]["car"] == "Honda Beat"
    assert report["dominant"][0]["car"] == "Honda Beat"
    assert report["dominant"][0]["top_5"] == 3
    assert "Top 5" in report["dominant"][0]["reason"]


def test_meta_report_identifies_emerging_and_declining_trends() -> None:
    rows = [
        _entry("Old Meta", 1, sequence=1),
        _entry("Old Meta", 2, sequence=2),
        _entry("Old Meta", 10, sequence=9),
        _entry("New Meta", 9, sequence=3),
        _entry("New Meta", 3, sequence=10),
        _entry("New Meta", 1, sequence=11),
    ]

    report = build_meta_report(rows, car_class="B 600")

    assert report["emerging"][0]["car"] == "New Meta"
    assert report["declining"][0]["car"] == "Old Meta"
    assert report["emerging"][0]["recent_trend"] > 0
    assert report["declining"][0]["recent_trend"] < 0


def test_meta_report_identifies_undervalued_beginner_and_expert_picks() -> None:
    rows = [
        _entry("Easy Stable", 4, event="Circuit A", sequence=1),
        _entry("Easy Stable", 5, event="Circuit B", sequence=2),
        _entry("Easy Stable", 4, event="Circuit C", sequence=3),
        _entry("Rare Rocket", 1, event="Circuit A", sequence=4),
        _entry("Hard Peak", 1, event="Circuit A", sequence=5),
        _entry("Hard Peak", 11, event="Circuit B", sequence=6),
    ]

    report = build_meta_report(rows, car_class="B 600")

    assert report["undervalued"][0]["car"] == "Rare Rocket"
    assert report["beginner"][0]["car"] == "Easy Stable"
    assert report["expert"][0]["car"] == "Hard Peak"


def test_recommend_championship_includes_dynamic_meta_report(monkeypatch: pytest.MonkeyPatch) -> None:
    event = {
        "name_es": "Circuit A",
        "slug": "circuit-a",
        "discipline": "road",
        "event_type": "road",
        "classes": [
            {
                "label": "B 600",
                "entries": [
                    {"rank": 1, "car": "Honda Beat"},
                    {"rank": 5, "car": "Honda Beat"},
                ],
            }
        ],
    }
    car = local_recommender.TierCar(
        id="honda-beat",
        discipline="road",
        class_name="B 600",
        tier="S+",
        make="Honda",
        model="Beat",
        image=None,
        tune_code="123 456 789",
        tune_name=None,
        tuner="FHStats",
        build_type="Grip",
        difficulty="low",
    )

    monkeypatch.setattr(local_recommender, "_match_event", lambda _name: event)
    monkeypatch.setattr(local_recommender, "_load_tier_cars", lambda: (car,))
    monkeypatch.setattr(
        local_recommender,
        "load_rivals",
        lambda discipline: [
            _entry("Honda Beat", 1, event="Circuit A", sequence=1),
            _entry("Honda Beat", 3, event="Circuit B", sequence=2),
            _entry("Mazda MX-5", 8, event="Circuit A", sequence=3),
        ]
        if discipline == "road"
        else [],
        raising=False,
    )

    payload = local_recommender.recommend_championship(["Circuit A"], "B 600")

    assert payload["status"] == "ok"
    assert payload["recommendations"][0]["car"]["name"] == "Honda Beat"
    assert payload["meta"]["current_meta"][0]["car"] == "Honda Beat"
    assert payload["meta"]["dominant"][0]["top_5"] == 2
