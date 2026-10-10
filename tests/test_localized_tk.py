from __future__ import annotations

from types import SimpleNamespace

from forza_tracker.localization import configure_language
from forza_tracker.ui.localized_tk import install_tkinter_localization


class _Widget:
    def __init__(self, *_args: object, **kwargs: object) -> None:
        self.options = dict(kwargs)

    def configure(self, _cnf: object = None, **kwargs: object) -> None:
        self.options.update(kwargs)

    config = configure


class _Menu(_Widget):
    def add_command(self, **kwargs: object) -> None:
        self.options = dict(kwargs)

    def add_cascade(self, **kwargs: object) -> None:
        self.options = dict(kwargs)


class _Treeview(_Widget):
    def heading(self, _column: str, _option: object = None, **kwargs: object) -> None:
        self.options = dict(kwargs)


class _Notebook(_Widget):
    def add(self, _child: object, **kwargs: object) -> None:
        self.options = dict(kwargs)


def test_widget_creation_and_reconfiguration_translate_visible_text() -> None:
    configure_language("en")
    tk = SimpleNamespace(Label=_Widget, Button=_Widget, Checkbutton=_Widget, Radiobutton=_Widget, Menu=_Menu)
    ttk = SimpleNamespace(Label=_Widget, Button=_Widget, Checkbutton=_Widget, LabelFrame=_Widget)
    dialogs = SimpleNamespace()

    install_tkinter_localization(tk, ttk, dialogs, dialogs)
    label = tk.Label(text="Esperando datos...")
    label.configure(text="3 carreras guardadas")

    assert label.options["text"] == "3 races saved"


def test_menu_labels_are_translated() -> None:
    configure_language("en")
    tk = SimpleNamespace(Label=_Widget, Button=_Widget, Checkbutton=_Widget, Radiobutton=_Widget, Menu=_Menu)
    ttk = SimpleNamespace(Label=_Widget, Button=_Widget, Checkbutton=_Widget, LabelFrame=_Widget)
    dialogs = SimpleNamespace()

    install_tkinter_localization(tk, ttk, dialogs, dialogs)
    menu = tk.Menu()
    menu.add_command(label="Inicio")

    assert menu.options["label"] == "Home"


def test_messagebox_title_and_message_are_translated() -> None:
    configure_language("en")
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def showinfo(*args: object, **kwargs: object) -> bool:
        calls.append((args, kwargs))
        return True

    tk = SimpleNamespace(Label=_Widget, Button=_Widget, Checkbutton=_Widget, Radiobutton=_Widget, Menu=_Menu)
    ttk = SimpleNamespace(Label=_Widget, Button=_Widget, Checkbutton=_Widget, LabelFrame=_Widget)
    messagebox = SimpleNamespace(showinfo=showinfo)
    filedialog = SimpleNamespace()

    install_tkinter_localization(tk, ttk, messagebox, filedialog)
    assert messagebox.showinfo("Inicio", "Esperando datos...") is True

    assert calls == [(('Home', 'Waiting for data...'), {})]


def test_tree_headings_and_notebook_tabs_are_translated() -> None:
    configure_language("en")
    tk = SimpleNamespace(Label=_Widget, Button=_Widget, Checkbutton=_Widget, Radiobutton=_Widget, Menu=_Menu)
    ttk = SimpleNamespace(
        Label=_Widget,
        Button=_Widget,
        Checkbutton=_Widget,
        LabelFrame=_Widget,
        Treeview=_Treeview,
        Notebook=_Notebook,
    )
    dialogs = SimpleNamespace()

    install_tkinter_localization(tk, ttk, dialogs, dialogs)
    tree = ttk.Treeview()
    tree.heading("date", text="Fecha")
    notebook = ttk.Notebook()
    notebook.add(object(), text="Resumen")

    assert tree.options["text"] == "Date"
    assert notebook.options["text"] == "Summary"
