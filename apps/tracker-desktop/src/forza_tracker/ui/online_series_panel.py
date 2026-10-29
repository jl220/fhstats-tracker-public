"""Compact English-only series names from memory and the installed-game catalog."""
import tkinter as tk

from forza_tracker.online_series_runtime import panel_text


class OnlineSeriesPanel(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg='#111820', padx=12, pady=8)
        self.label = tk.Label(self, text=panel_text({}), justify='left', anchor='w',
                              bg='#111820', fg='#e5edf5', font=('Segoe UI', 10))
        self.label.pack(fill='x')
        self.last_text = None

    def render(self, state):
        text = panel_text(state)
        if text != self.last_text:
            self.label.configure(text=text)
            self.last_text = text
