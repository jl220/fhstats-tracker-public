"""Keep complete desktop pages reachable on short Windows displays."""
import tkinter as tk
from tkinter import ttk


class PageViewport(tk.Frame):
    def __init__(self, parent, *, background: str, minimum_height: int = 600):
        super().__init__(parent, bg=background)
        self.minimum_height = minimum_height
        self.canvas = tk.Canvas(self, bg=background, highlightthickness=0, borderwidth=0)
        self._scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._scrollbar.set)
        self._scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.content = tk.Frame(self.canvas, bg=background)
        self._window = self.canvas.create_window((0, 0), anchor="nw", window=self.content)
        self.canvas.bind("<Configure>", self._resize)
        self.content.bind("<Configure>", self._resize)
        self.bind_all("<Configure>", self._content_changed, add="+")
        self.bind_all("<MouseWheel>", self._scroll, add="+")

    def _content_changed(self, event) -> None:
        widget = event.widget
        while widget is not None:
            if widget is self.content:
                height = max(self.minimum_height, self.canvas.winfo_height(), self.content.winfo_reqheight())
                if height != self.content.winfo_height():
                    self._resize(event)
                return
            widget = getattr(widget, "master", None)

    def _resize(self, event) -> None:
        width = self.canvas.winfo_width()
        viewport_height = self.canvas.winfo_height()
        height = max(self.minimum_height, viewport_height, self.content.winfo_reqheight())
        self.canvas.itemconfigure(self._window, width=width, height=height)
        self.canvas.configure(scrollregion=(0, 0, width, height))
        if height <= viewport_height:
            self.canvas.yview_moveto(0)

    def _scroll(self, event):
        if not self.winfo_ismapped() or self.canvas.yview() == (0.0, 1.0):
            return None
        widget = event.widget
        if isinstance(widget, (tk.Text, ttk.Treeview, ttk.Combobox)):
            return None
        while widget is not None:
            if widget is self.content or widget is self.canvas:
                self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
                return "break"
            widget = getattr(widget, "master", None)
        return None
