"""Privileged desktop primitives — runs inside the LocalSystem host service.

All public functions here are called from the pipe server thread inside the
Windows service.  They must be called from a LocalSystem process; calling them
from a normal user-mode process will silently degrade (OpenInputDesktop returns
NULL for Winlogon, SetThreadDesktop has no effect).

Desktop access sequence
-----------------------
From Session 0 (where Windows services run), the interactive window station
"WinSta0" is not the default.  We must:

  1. OpenWindowStation("WinSta0") → SetProcessWindowStation()
  2. OpenInputDesktop()  — returns a handle to whichever desktop currently
     receives keyboard/mouse input (Default during normal use, Winlogon during
     UAC).
  3. SetThreadDesktop()  — attaches the calling thread to that desktop so that
     GDI/UIA calls resolve against the correct desktop object.

This is the same pattern used by LookingGlass, RustDesk, and Splashtop.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import io
import logging
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Win32 constants
# ---------------------------------------------------------------------------

_UOI_NAME = 2
_WINSTA_ALL_ACCESS = 0x037F
_DESKTOP_ALL_ACCESS = 0x01FF
_DESKTOP_READOBJECTS = 0x0001

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

# UIA constants
_UIA_InvokePatternId = 10000
_UIA_NamePropertyId = 30005
_UIA_TreeScope_Descendants = 4


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _open_winsta0() -> int:
    handle = _user32.OpenWindowStationW("WinSta0", False, _WINSTA_ALL_ACCESS)
    return handle or 0


def _open_input_desktop(access: int = _DESKTOP_ALL_ACCESS) -> int:
    handle = _user32.OpenInputDesktop(0, False, access)
    return handle or 0


def _get_desktop_name(hdesk: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    needed = ctypes.wintypes.DWORD()
    _user32.GetUserObjectInformationW(
        hdesk, _UOI_NAME, buf, ctypes.sizeof(buf), ctypes.byref(needed)
    )
    return buf.value


@contextmanager
def _input_desktop():
    """Switch process/thread to WinSta0\\<input desktop>, then restore on exit."""
    hwinsta_prev = _user32.GetProcessWindowStation()
    hdesk_prev = _user32.GetThreadDesktop(_kernel32.GetCurrentThreadId())
    hwinsta = _open_winsta0()
    if hwinsta:
        _user32.SetProcessWindowStation(hwinsta)
    hdesk = _open_input_desktop(_DESKTOP_ALL_ACCESS)
    if hdesk:
        _user32.SetThreadDesktop(hdesk)
    try:
        yield
    finally:
        if hdesk:
            _user32.SetThreadDesktop(hdesk_prev)
            _user32.CloseDesktop(hdesk)
        if hwinsta:
            _user32.SetProcessWindowStation(hwinsta_prev)
            _user32.CloseWindowStation(hwinsta)


def _create_uia() -> tuple[Any, Any]:
    """Return (IUIAutomation, uia_core) on the current thread desktop."""
    import comtypes.client
    ctypes.windll.ole32.CoInitializeEx(None, 0)  # safe to call multiple times
    uia_core = comtypes.client.GetModule("UIAutomationCore.dll")
    iuia = comtypes.client.CreateObject(
        "{ff48dba4-60ef-4201-aa87-54103eef594e}",
        interface=uia_core.IUIAutomation,
    )
    return iuia, uia_core


def _serialize_element(element: Any, walker: Any, depth: int = 0) -> dict | None:
    """Recursively serialize a UIA element to a JSON-safe dict."""
    if depth > 8:
        return None
    try:
        rect = element.CurrentBoundingRectangle
        name = element.CurrentName or ""
        ctrl = element.CurrentLocalizedControlType or ""

        can_invoke = False
        try:
            can_invoke = element.GetCurrentPattern(_UIA_InvokePatternId) is not None
        except Exception:
            pass

        children: list[dict] = []
        try:
            child = walker.GetFirstChildElement(element)
            while child:
                node = _serialize_element(child, walker, depth + 1)
                if node:
                    children.append(node)
                child = walker.GetNextSiblingElement(child)
        except Exception:
            pass

        w = rect.right - rect.left
        h = rect.bottom - rect.top
        return {
            "name": name,
            "control_type": ctrl,
            "bbox": {
                "left": rect.left, "top": rect.top,
                "right": rect.right, "bottom": rect.bottom,
                "width": w, "height": h,
            },
            "center": {"x": rect.left + w // 2, "y": rect.top + h // 2},
            "can_invoke": can_invoke,
            "children": children,
        }
    except Exception as exc:
        logger.debug("_serialize_element error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_input_desktop_name() -> str:
    """Return the name of the current input desktop.

    Returns ``"Default"`` during normal desktop use and ``"Winlogon"`` while a
    UAC prompt is active.  Works from user-mode too (used for detection in the
    broker via :func:`~windows_mcp.desktop.screenshot.is_secure_desktop_active`).
    """
    hdesk = _open_input_desktop(_DESKTOP_READOBJECTS)
    if not hdesk:
        return "Default"
    try:
        return _get_desktop_name(hdesk)
    finally:
        _user32.CloseDesktop(hdesk)


def capture_screenshot() -> bytes:
    """Capture the current input desktop as PNG bytes.

    Uses GDI (Pillow ImageGrab) after SetThreadDesktop — DXGI is unavailable
    from Session 0, but GDI BitBlt works once the thread is on the right desktop.
    """
    with _input_desktop():
        from PIL import ImageGrab
        img = ImageGrab.grab(all_screens=True)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


def uia_get_window_titles() -> list[str]:
    """Return names of top-level windows on the current input desktop."""
    titles: list[str] = []
    with _input_desktop():
        try:
            iuia, _ = _create_uia()
            root = iuia.GetRootElement()
            walker = iuia.RawViewWalker
            child = walker.GetFirstChildElement(root)
            while child:
                try:
                    name = child.CurrentName
                    if name:
                        titles.append(name)
                except Exception:
                    pass
                try:
                    child = walker.GetNextSiblingElement(child)
                except Exception:
                    break
        except Exception as exc:
            logger.warning("uia_get_window_titles failed: %s", exc)
    return titles


def uia_get_tree() -> list[dict]:
    """Return the full UIA tree of the current input desktop.

    Each entry is a top-level window serialized as a nested dict.  Elements with
    ``can_invoke=True`` support ``IUIAutomationInvokePattern`` — the broker uses
    this to identify clickable buttons (Yes/No on a UAC dialog) without
    re-walking the tree.
    """
    nodes: list[dict] = []
    with _input_desktop():
        try:
            iuia, _ = _create_uia()
            root = iuia.GetRootElement()
            walker = iuia.RawViewWalker
            child = walker.GetFirstChildElement(root)
            while child:
                node = _serialize_element(child, walker)
                if node:
                    nodes.append(node)
                try:
                    child = walker.GetNextSiblingElement(child)
                except Exception:
                    break
        except Exception as exc:
            logger.error("uia_get_tree failed: %s", exc)
    return nodes


def uia_invoke_element(name: str) -> bool:
    """Find a named element on the input desktop and invoke it via UIA.

    Uses ``IUIAutomation.FindFirst`` with a name condition, then calls
    ``IUIAutomationInvokePattern.Invoke()``.  This is a direct COM call —
    no input injection needed, works from Session 0.
    """
    with _input_desktop():
        try:
            iuia, uia_core = _create_uia()
            root = iuia.GetRootElement()
            condition = iuia.CreatePropertyCondition(_UIA_NamePropertyId, name)
            element = root.FindFirst(_UIA_TreeScope_Descendants, condition)
            if element is None:
                logger.warning("uia_invoke_element: no element named %r", name)
                return False
            pattern = element.GetCurrentPattern(_UIA_InvokePatternId)
            if pattern is None:
                logger.warning("uia_invoke_element: %r has no InvokePattern", name)
                return False
            invoke = pattern.QueryInterface(uia_core.IUIAutomationInvokePattern)
            invoke.Invoke()
            logger.info("uia_invoke_element: invoked %r", name)
            return True
        except Exception as exc:
            logger.error("uia_invoke_element(%r) failed: %s", name, exc)
            return False


def uia_click_at(x: int, y: int) -> bool:
    """Invoke the UIA element at screen coordinates (x, y) on the input desktop.

    Uses ``IUIAutomation.ElementFromPoint`` so callers can use coordinates from
    the screenshot directly.  Falls back gracefully if no invokable element is
    found at the given point.
    """

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    with _input_desktop():
        try:
            iuia, uia_core = _create_uia()
            element = iuia.ElementFromPoint(_POINT(x, y))
            if element is None:
                logger.warning("uia_click_at(%d,%d): no element found", x, y)
                return False
            pattern = element.GetCurrentPattern(_UIA_InvokePatternId)
            if pattern is None:
                logger.warning("uia_click_at(%d,%d): no InvokePattern", x, y)
                return False
            invoke = pattern.QueryInterface(uia_core.IUIAutomationInvokePattern)
            invoke.Invoke()
            logger.info("uia_click_at(%d,%d): invoked %r", x, y, element.CurrentName)
            return True
        except Exception as exc:
            logger.error("uia_click_at(%d,%d) failed: %s", x, y, exc)
            return False
