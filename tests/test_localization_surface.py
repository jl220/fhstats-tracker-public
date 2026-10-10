from __future__ import annotations

import ast
import re
from pathlib import Path

from forza_tracker.localization import translation_key_for_source


ROOT = Path(__file__).resolve().parents[1]
SURFACE_FILES = (
    "apps/tracker-desktop/src/forza_tracker/ui/app.py",
    "apps/tracker-desktop/src/forza_tracker/main.py",
    "apps/tracker-desktop/src/forza_tracker/updater.py",
)
VISIBLE_KEYWORDS = {"text", "label", "title", "message"}
VISIBLE_CALLS = {
    "set",
    "progress",
    "showinfo",
    "showwarning",
    "showerror",
    "askyesno",
    "askokcancel",
    "askretrycancel",
    "askdirectory",
    "title",
    "_build_section_header",
    "_build_empty_state",
    "_notify",
}
BILINGUAL_CHOOSER_COPY = {
    "Choose language / Elegir idioma",
    "Choose your language",
    "Elige tu idioma",
    "English",
    "Español",
    "Continue / Continuar",
    "Could not save the language / No se pudo guardar el idioma",
}
ALLOWED_VISIBLE_COPY = {
    "",
    "F",
    "FHSTATS",
    "FHStats Tracker",
    "Forza Horizon Tracker",
    "COMMAND CENTER",
    "MB",
    "-10s",
    "+10s",
    "-",
    "●",
    "▶",
    "00:00 / 00:00",
    "current",
    "$message",
}


def _call_name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return ""


def _literal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        field_index = 0
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                parts.append(f"{{value_{field_index}}}")
                field_index += 1
            else:
                return None
        return "".join(parts)
    return None


def _visible_literals(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        for keyword in node.keywords:
            if keyword.arg in VISIBLE_KEYWORDS:
                value = _literal(keyword.value)
                if value is not None:
                    found.append((keyword.value.lineno, value))
        if name not in VISIBLE_CALLS:
            continue
        arguments = node.args if name in {"set", "progress", "_build_section_header", "_build_empty_state"} else node.args[:2]
        for argument in arguments:
            value = _literal(argument)
            if value is not None:
                found.append((argument.lineno, value))
    return found


def _is_allowed_data_copy(value: str) -> bool:
    residue = re.sub(r"\{value_\d+\}", "", value)
    residue = " ".join(residue.split())
    return residue in {
        "",
        "v",
        "- MB",
        "00:00 /",
        "/",
        "0/",
        "·",
        "· ·",
        "· %",
        "/3",
        "real",
        "· pts",
        "Tier · · · Rivals",
    }


def test_user_visible_literals_have_catalog_entries() -> None:
    missing: list[str] = []
    for relative in SURFACE_FILES:
        source = (ROOT / relative).read_text(encoding="utf-8")
        for line, value in _visible_literals(source):
            normalized = " ".join(value.split())
            if value in BILINGUAL_CHOOSER_COPY or value in ALLOWED_VISIBLE_COPY or _is_allowed_data_copy(value):
                continue
            if translation_key_for_source(value) is None:
                missing.append(f"{relative}:{line}: {normalized}")

    assert missing == [], "Uncatalogued visible copy:\n" + "\n".join(missing)


def test_recommendation_status_does_not_build_spanish_copy_dynamically() -> None:
    source = (ROOT / "apps/tracker-desktop/src/forza_tracker/ui/app.py").read_text(encoding="utf-8")

    assert 'f"Recomendación · {car_class}"' not in source
    assert 'f"{discipline} · confianza {confidence} · mapas' not in source
    assert 'tr("recommendation.result"' in source
    assert '"recommendation.confidence"' in source
