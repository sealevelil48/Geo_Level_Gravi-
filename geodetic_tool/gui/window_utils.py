"""Shared Tk window helpers."""
from __future__ import annotations


def fit_to_screen(win, width: int, height: int, margin: int = 60) -> None:
    """Size *win* to width x height, shrunk to fit the screen (minus a margin
    for the taskbar / title bar), and centre it, so no window or list opens
    past the screen edges on small laptop displays."""
    try:
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    except Exception:
        win.geometry(f"{width}x{height}")
        return
    w = max(320, min(int(width), sw - margin))
    h = max(240, min(int(height), sh - margin))
    x = max(0, (sw - w) // 2)
    y = max(0, (sh - h) // 2 - margin // 4)
    win.geometry(f"{w}x{h}+{x}+{y}")
