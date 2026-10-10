"""Shared desktop palette and native ttk styling."""
from tkinter import ttk

COLORS = {
    "bg": "#101214", "panel": "#181b1e", "panel2": "#141719",
    "surface": "#202428", "surface2": "#1a1e21", "text": "#f1f3ef",
    "muted": "#afb7b9", "subtle": "#889397", "accent": "#c6f36b",
    "accent2": "#93d8c7", "danger": "#fa8195", "warning": "#eac775",
    "line": "#343b40", "line_soft": "#292f33", "done": "#181f1d",
    "current": "#252f22", "pending": "#181b1e", "row": "#1b1f22",
    "row_alt": "#171b1e", "sidebar": "#15181a",
}

def configure_theme(root) -> None:
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TFrame", background=COLORS["bg"])
    style.configure("Panel.TFrame", background=COLORS["panel"])
    style.configure("Surface.TFrame", background=COLORS["surface"])
    style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=("Segoe UI", 10))
    style.configure("Muted.TLabel", background=COLORS["bg"], foreground=COLORS["muted"], font=("Segoe UI", 10))
    style.configure("Title.TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=("Segoe UI Semibold", 18))
    style.configure("SectionTitle.TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=("Segoe UI Semibold", 15))
    style.configure("PanelTitle.TLabel", background=COLORS["panel"], foreground=COLORS["text"], font=("Segoe UI Semibold", 13))
    style.configure("PanelMuted.TLabel", background=COLORS["panel"], foreground=COLORS["muted"], font=("Segoe UI", 10))
    style.configure("PanelField.TLabel", background=COLORS["panel"], foreground=COLORS["text"], font=("Segoe UI", 10))
    style.configure("CardTitle.TLabel", background=COLORS["panel"], foreground=COLORS["muted"], font=("Segoe UI", 10))
    style.configure("CardValue.TLabel", background=COLORS["panel"], foreground=COLORS["accent"], font=("Segoe UI Semibold", 20))
    style.configure("TButton", background=COLORS["surface"], foreground=COLORS["text"], borderwidth=0, focusthickness=1, focuscolor=COLORS["accent2"], padding=(12, 7), font=("Segoe UI Semibold", 9))
    style.map("TButton", background=[("active", COLORS["line"]), ("disabled", COLORS["panel2"])], foreground=[("disabled", COLORS["subtle"])])
    style.configure("TEntry", fieldbackground=COLORS["panel2"], foreground=COLORS["text"], insertcolor=COLORS["accent"], bordercolor=COLORS["line"], lightcolor=COLORS["line"], darkcolor=COLORS["line"], padding=(8, 6))
    style.configure("TCombobox", fieldbackground=COLORS["panel2"], background=COLORS["surface"], foreground=COLORS["text"], arrowcolor=COLORS["accent2"], bordercolor=COLORS["line"], lightcolor=COLORS["line"], darkcolor=COLORS["line"], padding=(8, 5))
    style.map("TCombobox", fieldbackground=[("readonly", COLORS["panel2"])], foreground=[("readonly", COLORS["text"])])
    style.configure("Treeview", bordercolor=COLORS["line_soft"], lightcolor=COLORS["line_soft"], darkcolor=COLORS["line_soft"], background=COLORS["panel"], foreground=COLORS["text"], fieldbackground=COLORS["panel"], rowheight=40, borderwidth=0, relief="flat", font=("Segoe UI", 10))
    style.configure("Treeview.Heading", background=COLORS["surface"], foreground=COLORS["muted"], font=("Segoe UI Semibold", 9), borderwidth=0, relief="flat", padding=(8, 8))
    style.map("Treeview", background=[("selected", "#344332")], foreground=[("selected", COLORS["text"])])
    style.configure("Vertical.TScrollbar",
        background="#2a3540",
        troughcolor=COLORS["bg"],
        bordercolor=COLORS["bg"],
        lightcolor="#2a3540",
        darkcolor="#2a3540",
        arrowcolor=COLORS["bg"],
        relief="flat",
        borderwidth=0,
        arrowsize=0,
        width=10,
    )
    style.map("Vertical.TScrollbar",
        background=[("pressed", "#44515e"), ("active", "#36424d")],
        lightcolor=[("pressed", "#44515e"), ("active", "#36424d")],
        darkcolor=[("pressed", "#44515e"), ("active", "#36424d")],
    )
    style.configure("TNotebook", background=COLORS["bg"], borderwidth=0, bordercolor=COLORS["line_soft"], lightcolor=COLORS["line_soft"], darkcolor=COLORS["line_soft"])
    style.configure("TNotebook.Tab", background=COLORS["panel"], foreground=COLORS["muted"], padding=(16, 9), borderwidth=0, bordercolor=COLORS["line_soft"], lightcolor=COLORS["line_soft"], darkcolor=COLORS["line_soft"], font=("Segoe UI Semibold", 9))
    style.map("TNotebook.Tab", background=[("selected", COLORS["current"])], foreground=[("selected", COLORS["accent"])])
    style.configure("TCheckbutton", background=COLORS["bg"], foreground=COLORS["text"])
    style.configure("Accent.TButton", background=COLORS["accent"], foreground=COLORS["bg"], borderwidth=0, focusthickness=1, padding=(14, 8), font=("Segoe UI Semibold", 10))
    style.map("Accent.TButton", background=[("active", "#d7ff8a")], foreground=[("active", COLORS["bg"])])
    style.configure("Danger.TButton", background=COLORS["danger"], foreground=COLORS["text"], borderwidth=0, focusthickness=1, padding=(14, 8), font=("Segoe UI Semibold", 10))
    style.map("Danger.TButton", background=[("disabled", COLORS["surface"]), ("active", "#ff9bad")], foreground=[("disabled", COLORS["subtle"])])
    style.configure("Quiet.TButton", background=COLORS["panel2"], foreground=COLORS["muted"], borderwidth=0, focusthickness=1, padding=(12, 7), font=("Segoe UI Semibold", 9))
    style.map("Quiet.TButton", background=[("active", COLORS["line"])], foreground=[("active", COLORS["text"])])

    style.configure("TRadiobutton", background=COLORS["panel"], foreground=COLORS["text"], font=("Segoe UI", 10))
    style.map("TRadiobutton", background=[("active", COLORS["panel"])], foreground=[("disabled", COLORS["subtle"])])
    style.configure("Horizontal.TProgressbar", background=COLORS["accent"], troughcolor=COLORS["surface"], borderwidth=0, bordercolor=COLORS["surface"], lightcolor=COLORS["surface"], darkcolor=COLORS["surface"])
    style.configure("Horizontal.TScrollbar", background=COLORS["line"], troughcolor=COLORS["bg"], borderwidth=0, arrowsize=12)
    style.map("Accent.TButton", background=[("disabled", COLORS["surface"]), ("active", "#d7ff8a")], foreground=[("disabled", COLORS["subtle"]), ("active", COLORS["bg"])])
    root.option_add("*TCombobox*Listbox.background", COLORS["panel"])
    root.option_add("*TCombobox*Listbox.foreground", COLORS["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", COLORS["current"])
    root.option_add("*TCombobox*Listbox.selectForeground", COLORS["accent"])
