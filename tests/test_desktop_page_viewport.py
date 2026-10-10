import tkinter as tk


def test_content_taller_than_minimum_remains_fully_reachable(capfd):
    from forza_tracker.ui.viewport import PageViewport
    # Tcl standard channels must not inherit pytest's temporary file descriptors.
    with capfd.disabled():
        root = tk.Tk()
    root.attributes("-alpha", 0.0)
    try:
        root.geometry("700x300")
        page = PageViewport(root, background="#101214", minimum_height=600)
        page.pack(fill="both", expand=True)
        for _ in range(8):
            tk.Frame(page.content, height=100).pack(fill="x")
        button = tk.Button(page.content, text="Stop")
        button.pack()
        root.update()
        assert page.content.winfo_height() >= page.content.winfo_reqheight()
        assert button.winfo_ismapped()
        page.canvas.yview_moveto(1.0)
        root.update()
        assert page.canvas.winfo_rooty() <= button.winfo_rooty()
        assert button.winfo_rooty() + button.winfo_height() <= page.canvas.winfo_rooty() + page.canvas.winfo_height()
    finally:
        root.destroy()


def test_short_window_can_scroll_to_controls_at_bottom(capfd):
    from forza_tracker.ui.viewport import PageViewport
    # Tcl standard channels must not inherit pytest's temporary file descriptors.
    with capfd.disabled():
        root = tk.Tk()
    root.attributes("-alpha", 0.0)
    try:
        root.geometry("700x300")
        page = PageViewport(root, background="#101214", minimum_height=600)
        page.pack(fill="both", expand=True)
        button = tk.Button(page.content, text="Bottom action")
        button.pack(side="bottom")
        root.update()
        assert page.canvas.yview()[1] < 1.0
        page.canvas.yview_moveto(1.0)
        root.update()
        assert page.canvas.winfo_rooty() <= button.winfo_rooty()
        assert button.winfo_rooty() + button.winfo_height() <= page.canvas.winfo_rooty() + page.canvas.winfo_height()
    finally:
        root.destroy()


def test_tall_window_uses_available_space_without_scroll(capfd):
    from forza_tracker.ui.viewport import PageViewport
    # Tcl standard channels must not inherit pytest's temporary file descriptors.
    with capfd.disabled():
        root = tk.Tk()
    root.attributes("-alpha", 0.0)
    try:
        root.geometry("700x800")
        page = PageViewport(root, background="#101214", minimum_height=600)
        page.pack(fill="both", expand=True)
        root.update()
        assert page.canvas.yview() == (0.0, 1.0)
        assert page.content.winfo_height() == page.canvas.winfo_height()
    finally:
        root.destroy()
