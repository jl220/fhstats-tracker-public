from __future__ import annotations

import re
from string import Formatter

from forza_tracker.localization.catalogs import CATALOGS, ENGLISH, SPANISH


SUPPORTED_LANGUAGES = ("en", "es")
DEFAULT_LANGUAGE = "en"
_active_language = DEFAULT_LANGUAGE


def normalize_language(value: object) -> str | None:
    candidate = str(value or "").strip().lower()
    return candidate if candidate in SUPPORTED_LANGUAGES else None


def configure_language(language: str) -> None:
    global _active_language
    _active_language = normalize_language(language) or DEFAULT_LANGUAGE


def current_language() -> str:
    return _active_language


def tr(key: str, **values: object) -> str:
    template = CATALOGS.get(_active_language, {}).get(key) or ENGLISH.get(key)
    if template is None:
        return f"[{key}]"
    return template.format(**values)


def translate_source(source: object) -> object:
    if not isinstance(source, str) or not source:
        return source
    resolved = _resolve_source(source)
    if resolved is None:
        return source
    key, values = resolved
    return tr(key, **values)


def translation_key_for_source(source: str) -> str | None:
    resolved = _resolve_source(source)
    return resolved[0] if resolved is not None else None


def _resolve_source(source: str) -> tuple[str, dict[str, str]] | None:
    for catalog in (ENGLISH, SPANISH):
        for key, template in catalog.items():
            fields = _format_field_order(template)
            if not fields:
                if source == template:
                    return key, {}
                continue
            match = _match_template(template, fields, source)
            if match is not None:
                return key, match
    return None


def validate_catalogs() -> list[str]:
    errors: list[str] = []
    english_keys = set(ENGLISH)
    for language, catalog in CATALOGS.items():
        missing = sorted(english_keys - set(catalog))
        extra = sorted(set(catalog) - english_keys)
        errors.extend(f"{language}: missing key {key}" for key in missing)
        errors.extend(f"{language}: unexpected key {key}" for key in extra)
        for key in sorted(english_keys & set(catalog)):
            english_fields = _format_fields(ENGLISH[key])
            translated_fields = _format_fields(catalog[key])
            if english_fields != translated_fields:
                errors.append(
                    f"{language}: placeholder mismatch for {key}: "
                    f"{sorted(english_fields)} != {sorted(translated_fields)}"
                )
    return errors


def _format_fields(template: str) -> set[str]:
    return {
        field_name
        for _literal, field_name, _format_spec, _conversion in Formatter().parse(template)
        if field_name
    }


def _format_field_order(template: str) -> list[str]:
    return [
        field_name
        for _literal, field_name, _format_spec, _conversion in Formatter().parse(template)
        if field_name
    ]


def _match_template(template: str, fields: list[str], source: str) -> dict[str, str] | None:
    pattern_parts: list[str] = []
    seen: set[str] = set()
    for literal, field_name, _format_spec, _conversion in Formatter().parse(template):
        pattern_parts.append(re.escape(literal))
        if not field_name:
            continue
        if field_name in seen:
            pattern_parts.append(f"(?P={field_name})")
        else:
            pattern_parts.append(f"(?P<{field_name}>.+?)")
            seen.add(field_name)
    match = re.fullmatch("".join(pattern_parts), source, flags=re.DOTALL)
    return match.groupdict() if match is not None else None


__all__ = [
    "DEFAULT_LANGUAGE",
    "SUPPORTED_LANGUAGES",
    "configure_language",
    "current_language",
    "normalize_language",
    "tr",
    "translate_source",
    "translation_key_for_source",
    "validate_catalogs",
]
