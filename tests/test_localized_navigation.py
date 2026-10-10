from __future__ import annotations

import tkinter as tk

from forza_tracker.localization import configure_language
from forza_tracker.ui.app import LocalizedChoiceVar, PAGE_SPECS, _display_circuit_name, _discipline_matches, normalize_page_id, visible_page_specs


def test_page_ids_are_language_independent() -> None:
    assert [page_id for page_id, _label_key, _icon in PAGE_SPECS] == [
        "home",
        "history",
        "videos",
        "tierlist",
        "rivals",
        "garage",
        "news",
        "settings",
    ]


def test_legacy_and_english_start_page_names_are_supported() -> None:
    assert normalize_page_id("Inicio") == "home"
    assert normalize_page_id("Home") == "home"
    assert normalize_page_id("Ajustes") == "settings"
    assert normalize_page_id("Settings") == "settings"
    assert normalize_page_id("Mi garaje") == "garage"
    assert normalize_page_id("My Garage") == "garage"
    assert normalize_page_id("Novedades") == "news"
    assert normalize_page_id("What's new") == "news"


def test_unknown_start_page_falls_back_to_home() -> None:
    assert normalize_page_id("does-not-exist") == "home"






def test_circuit_display_names_follow_language_without_changing_saved_identity() -> None:
    configure_language("en")
    assert _display_circuit_name("Circuito de autopista") == "Highway Circuit"

    configure_language("es")
    assert _display_circuit_name("Highway Circuit") == "Circuito de autopista"


def test_choice_variable_keeps_language_independent_filter_values() -> None:
    configure_language("en")
    interpreter = tk.Tcl()
    choice = LocalizedChoiceVar(
        master=interpreter,
        choices={"all": "filter.all", "confirmed": "filter.confirmed"},
        value="all",
    )

    assert choice.get() == "all"
    assert choice.display_values(["all", "confirmed", "A"]) == ("All", "Confirmed", "A")
    choice.set("confirmed")
    assert choice.get() == "confirmed"


def test_summary_discipline_filters_use_language_independent_ids() -> None:
    assert _discipline_matches("Carreras de carretera", "road") is True
    assert _discipline_matches("Road Racing", "road") is True
    assert _discipline_matches("Campo a través", "cross_country") is True
    assert _discipline_matches("Cross Country", "cross_country") is True
    assert _discipline_matches("Carreras callejeras", "street") is True
