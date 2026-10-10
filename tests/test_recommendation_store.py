from forza_tracker.recommendation import (
    read_recommendation,
    recommendation_signature,
    write_recommendation,
)


def test_recommendation_store_round_trip(tmp_path) -> None:
    path = tmp_path / "runtime" / "recommendation.json"
    write_recommendation(
        {
            "status": "ok",
            "car_class": "S1 800",
            "recommendations": [{"car": {"name": "McLaren 620R"}}],
        },
        path,
    )

    payload = read_recommendation(path)

    assert payload["status"] == "ok"
    assert payload["recommendations"][0]["car"]["name"] == "McLaren 620R"
    assert payload["updated_at"]
    assert recommendation_signature(path) is not None
