from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from forza_tracker.localization import tr


class NewsPage(ttk.Frame):
    def __init__(
        self,
        parent,
        *,
        colors: dict[str, str],
        on_check: Callable[[], None],
        on_install: Callable[[], None],
    ):
        super().__init__(parent, padding=12)
        self._colors = colors
        self._on_install = on_install
        self._can_install = False
        self._release_installable = False
        self._busy = False

        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        heading = ttk.Frame(self)
        heading.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(heading, text=tr("news.title"), style="Title.TLabel").pack(anchor="w")
        self._subtitle = ttk.Label(
            heading, text=tr("news.subtitle"), style="Muted.TLabel", wraplength=590
        )
        self._subtitle.pack(anchor="w", pady=(3, 0))
        self.bind("<Configure>", self._resize_subtitle, add="+")

        actions = ttk.Frame(self)
        actions.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self._check_button = ttk.Button(
            actions, text=tr("news.check"), command=on_check
        )
        self._check_button.pack(side="left")
        self._install_button = ttk.Button(
            actions,
            text=tr("news.install"),
            style="Accent.TButton",
            command=self._handle_install,
            state="disabled",
        )
        self._install_button.pack(side="left", padx=(8, 0))
        self._status = tk.StringVar(value=tr("news.ready"))
        self._status_label = ttk.Label(
            self, textvariable=self._status, style="Muted.TLabel", anchor="w"
        )
        self._status_label.grid(row=2, column=0, sticky="ew", pady=(0, 8))

        content = ttk.Frame(self)
        self._content = content
        content.grid(row=3, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1, uniform="news")
        content.columnconfigure(1, weight=1, uniform="news")
        content.rowconfigure(1, weight=1)
        self._panel_wrap_groups: list[tuple[tk.Frame, list[tk.Label]]] = []

        self._installed_version = tk.StringVar()
        self._available_heading = tk.StringVar(value=tr("news.no_update_title"))
        self._available_meta = tk.StringVar()
        self._build_notes_panel(
            content,
            column=0,
            title=tr("news.installed_title"),
            heading_var=self._installed_version,
            notes_attribute="_installed_notes",
        )
        self._build_notes_panel(
            content,
            column=1,
            title=tr("news.available_title"),
            heading_var=self._available_heading,
            notes_attribute="_available_notes",
            meta_var=self._available_meta,
        )

        self._progress_value = tk.DoubleVar(value=0.0)
        self._progress = ttk.Progressbar(
            self, variable=self._progress_value, maximum=100.0, mode="determinate"
        )
        self._progress.grid(row=4, column=0, sticky="ew", pady=(10, 0))

    def _build_notes_panel(
        self,
        parent: ttk.Frame,
        *,
        column: int,
        title: str,
        heading_var: tk.StringVar,
        notes_attribute: str,
        meta_var: tk.StringVar | None = None,
    ) -> None:
        panel = tk.Frame(parent, bg=self._colors["panel"], padx=12, pady=10)
        panel.grid(row=0, column=column, rowspan=2, sticky="nsew", padx=(0, 5) if column == 0 else (5, 0))
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(3, weight=1)
        title_label = tk.Label(
            panel, text=title, bg=self._colors["panel"], fg=self._colors["muted"],
            font=("Segoe UI Semibold", 9), anchor="w", justify="left",
        )
        title_label.grid(row=0, column=0, sticky="ew")
        heading_label = tk.Label(
            panel, textvariable=heading_var, bg=self._colors["panel"], fg=self._colors["text"],
            font=("Segoe UI Semibold", 13), anchor="w", justify="left",
        )
        heading_label.grid(row=1, column=0, sticky="ew", pady=(3, 0))
        wrap_labels = [title_label, heading_label]
        if meta_var is not None:
            meta_label = tk.Label(
                panel, textvariable=meta_var, bg=self._colors["panel"], fg=self._colors["muted"],
                font=("Segoe UI", 8), anchor="w", justify="left",
            )
            meta_label.grid(row=2, column=0, sticky="ew", pady=(2, 4))
            wrap_labels.append(meta_label)
        self._panel_wrap_groups.append((panel, wrap_labels))
        panel.bind(
            "<Configure>",
            lambda event, labels=wrap_labels: self._resize_panel_labels(event, labels),
            add="+",
        )
        notes_frame = tk.Frame(panel, bg=self._colors["panel"])
        notes_frame.grid(row=3, column=0, sticky="nsew", pady=(6, 0))
        notes_frame.columnconfigure(0, weight=1)
        notes_frame.rowconfigure(0, weight=1)
        notes = tk.Text(
            notes_frame, width=1, height=1, wrap="word", state="disabled", relief="flat", borderwidth=0,
            bg=self._colors["panel"], fg=self._colors["text"], insertbackground=self._colors["text"],
            selectbackground=self._colors.get("accent", "#b8ef3c"), selectforeground=self._colors.get("bg", "#111111"),
            font=("Segoe UI", 10), padx=0, pady=0,
        )
        scrollbar = ttk.Scrollbar(notes_frame, orient="vertical", command=notes.yview)
        notes.configure(yscrollcommand=scrollbar.set)
        notes.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        setattr(self, notes_attribute, notes)

    def _resize_subtitle(self, event) -> None:
        wraplength = max(200, int(event.width) - 24)
        self._subtitle.configure(wraplength=wraplength)
        self._status_label.configure(wraplength=wraplength)

    @staticmethod
    def _resize_panel_labels(event, labels: list[tk.Label]) -> None:
        wraplength = max(120, int(event.width) - 24)
        for label in labels:
            label.configure(wraplength=wraplength)

    def show_release(
        self,
        *,
        installed_version: str,
        installed_notes: str,
        available_version: str = "",
        available_notes: str = "",
        checked_at: str = "",
        cached: bool = False,
    ) -> None:
        self._installed_version.set(tr("news.version", version=installed_version))
        self._replace_notes(self._installed_notes, installed_notes)
        self._release_installable = bool(available_version) and not cached
        self._can_install = self._release_installable and not self._busy
        if available_version:
            self._available_heading.set(tr("news.version", version=available_version))
            if cached:
                self._available_meta.set(tr("news.cached_at", checked_at=checked_at))
            elif checked_at:
                self._available_meta.set(tr("news.checked_at", checked_at=checked_at))
            else:
                self._available_meta.set("")
            self._replace_notes(self._available_notes, available_notes)
        else:
            self._available_heading.set(tr("news.no_update_title"))
            self._available_meta.set("")
            self._replace_notes(self._available_notes, tr("news.no_update_notes"))
        self._install_button.configure(state="normal" if self._can_install else "disabled")

    def set_status(
        self,
        text: str,
        *,
        busy: bool = False,
        can_install: bool = False,
        progress: float | None = None,
    ) -> None:
        self._status.set(text)
        self._busy = busy
        self._can_install = bool(can_install) and self._release_installable and not busy
        self._check_button.configure(state="disabled" if busy else "normal")
        self._install_button.configure(
            state="normal" if self._can_install else "disabled"
        )
        if progress is None:
            self._progress.configure(mode="indeterminate" if busy else "determinate")
            if busy:
                self._progress.start(12)
            else:
                self._progress.stop()
                self._progress_value.set(0.0)
        else:
            self._progress.stop()
            self._progress.configure(mode="determinate")
            self._progress_value.set(max(0.0, min(100.0, float(progress))))

    @staticmethod
    def _replace_notes(widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _handle_install(self) -> None:
        if self._can_install and self._release_installable and not self._busy:
            self._on_install()


__all__ = ["NewsPage"]
