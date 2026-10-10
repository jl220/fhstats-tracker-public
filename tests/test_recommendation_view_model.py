from __future__ import annotations

from forza_tracker.ui.recommendation_view_model import build_meta_category_cards


def test_build_meta_category_cards_formats_top_pick_per_category() -> None:
    meta = {
        "current_meta": [{"car": "Honda Beat", "score": 91.4}],
        "emerging": [{"car": "Nissan Skyline GT-R", "recent_trend": 5.0}],
        "undervalued": [{"car": "Rare Rocket", "score": 72.0}],
        "dominant": [{"car": "Dodge Dart", "top_5": 7}],
        "beginner": [{"car": "Easy Stable", "consistency": 96.0}],
        "expert": [{"car": "Hard Peak", "top_1": 3}],
    }

    cards = build_meta_category_cards(meta)

    assert cards[0].label == "Meta actual"
    assert cards[0].value == "Honda Beat"
    assert cards[0].detail == "91.4 pts"
    assert cards[1].label == "Meta emergente"
    assert cards[1].value == "Nissan Skyline GT-R"
    assert cards[1].detail == "+5.0 tendencia"
    assert cards[2].label == "Infravalorado"
    assert cards[2].value == "Rare Rocket"
    assert cards[3].label == "Dominante"
    assert cards[3].detail == "7 Top 5"
    assert cards[4].label == "Principiante"
    assert cards[5].label == "Experto"


def test_build_meta_category_cards_uses_empty_state_when_category_has_no_data() -> None:
    cards = build_meta_category_cards({})

    assert len(cards) == 6
    assert all(card.value == "-" for card in cards)
    assert all(card.detail == "Sin datos suficientes" for card in cards)
