"""Brief on-screen visual confirmation that a screenshot was taken.

Draws a glowing orange-red border over the captured area for ~1 second using
a transparent always-on-top Tk window on a daemon thread. The flash is started
*after* the screenshot is captured so it never appears in the captured image.
"""

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

_FLASH_COLOR = "#FF4500"
_DURATION_MS = 2500
_FRAME_INTERVAL_MS = 20
_BORDER_THICKNESS = 6
_FULLSCREEN_INSET = 12

_lock = threading.Lock()
_active_overlay: "_Overlay | None" = None


def _flash_disabled() -> bool:
    value = os.getenv("WINDOWS_MCP_DISABLE_FLASH", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


class _Overlay:
    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self.closed_event = threading.Event()
        self.thread: threading.Thread | None = None


def cancel_active_flash(timeout: float = 0.25) -> None:
    """Tear down any flash overlay currently on screen.

    Call this immediately before taking a screenshot so the previous flash
    can never bleed into the new capture.
    """
    global _active_overlay
    with _lock:
        ov = _active_overlay
        _active_overlay = None
    if ov is None:
        return
    ov.stop_event.set()
    ov.closed_event.wait(timeout=timeout)


def show_capture_flash(
    rects: list[tuple[int, int, int, int]],
    *,
    full_screen: bool,
) -> None:
    """Show a fade-in/out orange-red border over each rect.

    ``rects`` are ``(left, top, right, bottom)`` tuples in virtual-screen
    coordinates. ``full_screen=True`` draws an inner border that fades in
    then out (used when no region was specified). ``full_screen=False`` keeps
    the border solid for most of the duration and fades out at the end.

    Returns immediately; rendering happens on a daemon thread.
    """
    if _flash_disabled() or not rects:
        return
    rects = [tuple(r) for r in rects]
    overlay = _Overlay()
    overlay.thread = threading.Thread(
        target=_run_overlay,
        args=(rects, full_screen, overlay),
        name="windows-mcp-flash",
        daemon=True,
    )
    with _lock:
        global _active_overlay
        _active_overlay = overlay
    overlay.thread.start()


def _run_overlay(
    rects: list[tuple[int, int, int, int]],
    full_screen: bool,
    overlay: _Overlay,
) -> None:
    try:
        import tkinter as tk
    except Exception:
        logger.debug("tkinter unavailable; skipping screenshot flash")
        overlay.closed_event.set()
        return

    root: "tk.Tk | None" = None
    try:
        left = min(r[0] for r in rects)
        top = min(r[1] for r in rects)
        right = max(r[2] for r in rects)
        bottom = max(r[3] for r in rects)
        width = right - left
        height = bottom - top
        if width <= 0 or height <= 0:
            return

        root = tk.Tk()
        root.withdraw()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        try:
            root.attributes("-disabled", True)
        except tk.TclError:
            pass

        transparent_color = "#010203"
        try:
            root.configure(bg=transparent_color)
            root.attributes("-transparentcolor", transparent_color)
            canvas_bg = transparent_color
        except tk.TclError:
            canvas_bg = root.cget("bg")

        root.geometry(f"{width}x{height}+{left}+{top}")

        canvas = tk.Canvas(
            root,
            width=width,
            height=height,
            bg=canvas_bg,
            highlightthickness=0,
            borderwidth=0,
        )
        canvas.pack(fill="both", expand=True)

        inset = _FULLSCREEN_INSET if full_screen else 0
        for r_left, r_top, r_right, r_bottom in rects:
            x1 = r_left - left + inset
            y1 = r_top - top + inset
            x2 = r_right - left - inset - 1
            y2 = r_bottom - top - inset - 1
            if x2 - x1 <= 0 or y2 - y1 <= 0:
                continue
            for i in range(_BORDER_THICKNESS):
                canvas.create_rectangle(
                    x1 + i,
                    y1 + i,
                    x2 - i,
                    y2 - i,
                    outline=_FLASH_COLOR,
                    width=1,
                )

        root.deiconify()
        start = time.perf_counter()

        def tick() -> None:
            if overlay.stop_event.is_set():
                root.destroy()
                return
            elapsed_ms = (time.perf_counter() - start) * 1000
            if elapsed_ms >= _DURATION_MS:
                root.destroy()
                return
            t = elapsed_ms / _DURATION_MS
            if full_screen:
                alpha = 1.0 - abs(2 * t - 1)
            else:
                alpha = 1.0 if t < 0.65 else max(0.0, 1.0 - (t - 0.65) / 0.35)
            try:
                root.attributes("-alpha", max(0.0, min(1.0, alpha)))
            except tk.TclError:
                pass
            root.after(_FRAME_INTERVAL_MS, tick)

        root.after(0, tick)
        root.mainloop()
    except Exception:
        logger.debug("screenshot flash overlay failed", exc_info=True)
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass
    finally:
        with _lock:
            global _active_overlay
            if _active_overlay is overlay:
                _active_overlay = None
        overlay.closed_event.set()
