from __future__ import annotations

from collections.abc import Callable
from typing import Any

from forza_tracker.localization import translate_source


_WIDGET_NAMES = (
    "Label",
    "Button",
    "Checkbutton",
    "Radiobutton",
    "LabelFrame",
)
_DIALOG_NAMES = (
    "showinfo",
    "showwarning",
    "showerror",
    "askyesno",
    "askokcancel",
    "askretrycancel",
    "askquestion",
)
_FILE_DIALOG_NAMES = (
    "askdirectory",
    "askopenfilename",
    "askopenfilenames",
    "asksaveasfilename",
)


def install_tkinter_localization(
    tk_module: Any,
    ttk_module: Any,
    messagebox_module: Any,
    filedialog_module: Any,
) -> None:
    for module in (tk_module, ttk_module):
        for name in _WIDGET_NAMES:
            base = getattr(module, name, None)
            if base is not None and not getattr(base, "_fhstats_localized", False):
                setattr(module, name, _localized_widget_class(base))

    menu_base = getattr(tk_module, "Menu", None)
    if menu_base is not None and not getattr(menu_base, "_fhstats_localized", False):
        setattr(tk_module, "Menu", _localized_menu_class(menu_base))

    for name in ("Tk", "Toplevel"):
        base = getattr(tk_module, name, None)
        if base is not None and not getattr(base, "_fhstats_localized", False):
            setattr(tk_module, name, _localized_window_class(base))

    treeview_base = getattr(ttk_module, "Treeview", None)
    if treeview_base is not None and not getattr(treeview_base, "_fhstats_localized", False):
        setattr(ttk_module, "Treeview", _localized_treeview_class(treeview_base))

    notebook_base = getattr(ttk_module, "Notebook", None)
    if notebook_base is not None and not getattr(notebook_base, "_fhstats_localized", False):
        setattr(ttk_module, "Notebook", _localized_notebook_class(notebook_base))

    for name in _DIALOG_NAMES:
        _wrap_dialog(messagebox_module, name, translate_message=True)
    for name in _FILE_DIALOG_NAMES:
        _wrap_dialog(filedialog_module, name, translate_message=False)


def _localized_widget_class(base: type[Any]) -> type[Any]:
    class LocalizedWidget(base):  # type: ignore[misc, valid-type]
        _fhstats_localized = True

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **_translated_options(kwargs))

        def configure(self, cnf: Any = None, **kwargs: Any) -> Any:
            translated_cnf = _translated_options(cnf) if isinstance(cnf, dict) else cnf
            return super().configure(translated_cnf, **_translated_options(kwargs))

        config = configure

    LocalizedWidget.__name__ = f"Localized{base.__name__}"
    return LocalizedWidget


def _localized_menu_class(base: type[Any]) -> type[Any]:
    class LocalizedMenu(base):  # type: ignore[misc, valid-type]
        _fhstats_localized = True

        def add_command(self, **kwargs: Any) -> Any:
            return super().add_command(**_translated_label(kwargs))

        def add_cascade(self, **kwargs: Any) -> Any:
            return super().add_cascade(**_translated_label(kwargs))

        def add_checkbutton(self, **kwargs: Any) -> Any:
            return super().add_checkbutton(**_translated_label(kwargs))

        def add_radiobutton(self, **kwargs: Any) -> Any:
            return super().add_radiobutton(**_translated_label(kwargs))

    LocalizedMenu.__name__ = f"Localized{base.__name__}"
    return LocalizedMenu


def _localized_window_class(base: type[Any]) -> type[Any]:
    class LocalizedWindow(base):  # type: ignore[misc, valid-type]
        _fhstats_localized = True

        def title(self, text: str | None = None) -> Any:
            if text is None:
                return super().title()
            return super().title(translate_source(text))

    LocalizedWindow.__name__ = f"Localized{base.__name__}"
    return LocalizedWindow


def _localized_treeview_class(base: type[Any]) -> type[Any]:
    class LocalizedTreeview(base):  # type: ignore[misc, valid-type]
        _fhstats_localized = True

        def heading(self, column: str, option: str | None = None, **kwargs: Any) -> Any:
            return super().heading(column, option, **_translated_options(kwargs))

    LocalizedTreeview.__name__ = f"Localized{base.__name__}"
    return LocalizedTreeview


def _localized_notebook_class(base: type[Any]) -> type[Any]:
    class LocalizedNotebook(base):  # type: ignore[misc, valid-type]
        _fhstats_localized = True

        def add(self, child: Any, **kwargs: Any) -> Any:
            return super().add(child, **_translated_options(kwargs))

        def insert(self, pos: Any, child: Any, **kwargs: Any) -> Any:
            return super().insert(pos, child, **_translated_options(kwargs))

        def tab(self, tab_id: Any, option: str | None = None, **kwargs: Any) -> Any:
            return super().tab(tab_id, option, **_translated_options(kwargs))

    LocalizedNotebook.__name__ = f"Localized{base.__name__}"
    return LocalizedNotebook


def _wrap_dialog(module: Any, name: str, *, translate_message: bool) -> None:
    function = getattr(module, name, None)
    if function is None or getattr(function, "_fhstats_localized", False):
        return

    def localized(*args: Any, **kwargs: Any) -> Any:
        translated_args = list(args)
        if translated_args:
            translated_args[0] = translate_source(translated_args[0])
        if translate_message and len(translated_args) > 1:
            translated_args[1] = translate_source(translated_args[1])
        translated_kwargs = dict(kwargs)
        if "title" in translated_kwargs:
            translated_kwargs["title"] = translate_source(translated_kwargs["title"])
        if translate_message and "message" in translated_kwargs:
            translated_kwargs["message"] = translate_source(translated_kwargs["message"])
        return function(*translated_args, **translated_kwargs)

    localized._fhstats_localized = True  # type: ignore[attr-defined]
    localized.__wrapped__ = function  # type: ignore[attr-defined]
    setattr(module, name, localized)


def _translated_options(options: dict[str, Any]) -> dict[str, Any]:
    translated = dict(options)
    if "text" in translated:
        translated["text"] = translate_source(translated["text"])
    return translated


def _translated_label(options: dict[str, Any]) -> dict[str, Any]:
    translated = dict(options)
    if "label" in translated:
        translated["label"] = translate_source(translated["label"])
    return translated
