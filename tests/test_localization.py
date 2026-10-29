from __future__ import annotations

import pytest

from forza_tracker.localization import (
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    configure_language,
    current_language,
    normalize_language,
    tr,
    translate_source,
    translation_key_for_source,
    validate_catalogs,
)
from forza_tracker.localization.catalogs import ENGLISH, SPANISH


@pytest.fixture(autouse=True)
def restore_english() -> None:
    configure_language("en")
    yield
    configure_language("en")


def test_english_is_the_default_supported_language() -> None:
    assert DEFAULT_LANGUAGE == "en"
    assert SUPPORTED_LANGUAGES == ("en", "es")
    assert current_language() == "en"
    assert tr("nav.home") == "Home"


def test_spanish_is_selectable() -> None:
    configure_language("es")

    assert current_language() == "es"
    assert tr("nav.home") == "Inicio"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("EN", "en"), (" es ", "es"), (None, None), ("de", None), ("", None)],
)
def test_language_codes_are_normalized(value: object, expected: str | None) -> None:
    assert normalize_language(value) == expected


def test_invalid_runtime_language_falls_back_to_english() -> None:
    configure_language("de")

    assert current_language() == "en"
    assert tr("nav.home") == "Home"


def test_missing_spanish_translation_falls_back_to_english(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(ENGLISH, "test.english_only", "English fallback")
    monkeypatch.delitem(SPANISH, "test.english_only", raising=False)
    configure_language("es")

    assert tr("test.english_only") == "English fallback"


def test_unknown_translation_key_is_visible() -> None:
    assert tr("missing.translation.key") == "[missing.translation.key]"


def test_known_spanish_source_text_is_translated_for_legacy_ui_boundaries() -> None:
    configure_language("en")
    assert translate_source("Esperando datos...") == "Waiting for data..."

    configure_language("es")
    assert translate_source("Esperando datos...") == "Esperando datos..."


def test_dynamic_spanish_source_text_preserves_values() -> None:
    configure_language("en")
    assert translate_source("3 carreras guardadas") == "3 races saved"


def test_translation_key_can_be_resolved_from_static_or_dynamic_source() -> None:
    assert translation_key_for_source("Inicio") == "nav.home"
    assert translation_key_for_source("12 carreras guardadas") == "status.races_saved"
    assert translation_key_for_source("unmapped text") is None


def test_catalog_keys_and_placeholders_are_valid() -> None:
    assert validate_catalogs() == []


def test_telemetry_setup_copy_uses_the_tracker_listener_values() -> None:
    assert tr("telemetry.setup.route") == "Forza Horizon 6 → Settings → HUD Settings → Data Out"
    assert tr("telemetry.setup.data_out") == "Data Out: On"
    assert tr("telemetry.setup.ip") == "IP address: 127.0.0.1"
    assert tr("telemetry.setup.port", port=9999) == "Port: 9999"
    assert tr("telemetry.setup.port", port=10001) == "Port: 10001"
    assert tr("telemetry.setup.optional").startswith("Telemetry is optional")


