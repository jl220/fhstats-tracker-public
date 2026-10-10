from forza_tracker.ui.catalog_view_model import (
    ALL_CIRCUITS,
    build_meta_tier_sections,
    filter_rivals_rows,
    rivals_circuit_options,
)


def test_rivals_circuit_options_are_unique_and_sorted() -> None:
    rows = [
        {"event": "Circuito Z"},
        {"event": "Circuito A"},
        {"event": "Circuito Z"},
        {"event": ""},
    ]

    assert rivals_circuit_options(rows) == (ALL_CIRCUITS, "Circuito A", "Circuito Z")


def test_filter_rivals_rows_uses_exact_circuit_within_loaded_discipline() -> None:
    rows = [
        {"event": "Circuito Norte", "class": "A 700", "player": "Jolu", "car": "Subaru"},
        {"event": "Circuito Norte Pro", "class": "A 700", "player": "Jolu", "car": "Subaru"},
        {"event": "Circuito Norte", "class": "S1 800", "player": "Ana", "car": "Ford"},
    ]

    filtered = filter_rivals_rows(
        rows,
        circuit="Circuito Norte",
        car_class="A 700",
        query="subaru",
    )

    assert filtered == (rows[0],)


def test_meta_tier_sections_keep_visible_names_and_order() -> None:
    rows = [
        {"tier": "A", "model": "Offmeta car"},
        {"tier": "S+", "model": "Meta plus car"},
        {"tier": "S", "model": "Meta car"},
    ]

    sections = build_meta_tier_sections(rows)

    assert [section.label for section in sections] == ["Meta+", "Meta", "Offmeta"]
    assert [section.rows[0]["model"] for section in sections] == [
        "Meta plus car",
        "Meta car",
        "Offmeta car",
    ]
