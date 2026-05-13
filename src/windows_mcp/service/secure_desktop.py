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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _open_winsta0() -> int:
    """Open WinSta0 (the interactive window station).  Returns handle or 0."""
    handle = _user32.OpenWindowStationW("WinSta0", False, _WINSTA_ALL_ACCESS)
    return handle or 0


def _open_input_desktop(access: int = _DESKTOP_ALL_ACCESS) -> int:
    """Open the current input desktop.  Returns handle or 0."""
    handle = _user32.OpenInputDesktop(0, False, access)
    return handle or 0


def _get_desktop_name(hdesk: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    needed = ctypes.wintypes.DWORD()
    _user32.GetUserObjectInformationW(
        hdesk, _UOI_NAME, buf, ctypes.sizeof(buf), ctypes.byref(needed)
    )
    return buf.value


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_input_desktop_name() -> str:
    """Return the name of the current input desktop.

    Returns ``"Default"`` during normal desktop use and ``"Winlogon"`` while a
    UAC prompt is displayed on the Secure Desktop.  This call works from
    user-mode too (used for UAC detection in the broker).
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

    Must be called from a LocalSystem process to access the Winlogon desktop.
    Uses GDI (Pillow ImageGrab) after switching the thread desktop — DXGI is
    not available from Session 0, but GDI BitBlt works correctly once
    SetThreadDesktop points the thread at WinSta0\\<input desktop>.
    """
    hwinsta_prev = _user32.GetProcessWindowStation()
    hdesk_prev = _user32.GetThreadDesktop(_kernel32.GetCurrentThreadId())

    hwinsta = _open_winsta0()
    if hwinsta:
        _user32.SetProcessWindowStation(hwinsta)

    hdesk = _open_input_desktop(_DESKTOP_ALL_ACCESS)
    if hdesk:
        _user32.SetThreadDesktop(hdesk)

    try:
        from PIL import ImageGrab
        img = ImageGrab.grab(all_screens=True)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    finally:
        # Restore previous desktop/winsta so the service thread stays clean.
        if hdesk:
            _user32.SetThreadDesktop(hdesk_prev)
            _user32.CloseDesktop(hdesk)
        if hwinsta:
            _user32.SetProcessWindowStation(hwinsta_prev)
            _user32.CloseWindowStation(hwinsta)


def uia_get_window_titles() -> list[str]:
    """Return names of top-level windows on the current input desktop.

    Must be called from a LocalSystem process.  Returns an empty list on
    failure so the caller always gets a well-typed value.
    """
    hwinsta_prev = _user32.GetProcessWindowStation()
    hdesk_prev = _user32.GetThreadDesktop(_kernel32.GetCurrentThreadId())

    hwinsta = _open_winsta0()
    if hwinsta:
        _user32.SetProcessWindowStation(hwinsta)

    hdesk = _open_input_desktop(_DESKTOP_ALL_ACCESS)
    if hdesk:
        _user32.SetThreadDesktop(hdesk)

    titles: list[str] = []
    try:
        import comtypes.client
        uia_core = comtypes.client.GetModule("UIAutomationCore.dll")
        iuia = comtypes.client.CreateObject(
            "{ff48dba4-60ef-4201-aa87-54103eef594e}",
            interface=uia_core.IUIAutomation,
        )
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
        logger.warning("UIA enumeration failed: %s", exc)
    finally:
        if hdesk:
            _user32.SetThreadDesktop(hdesk_prev)
            _user32.CloseDesktop(hdesk)
        if hwinsta:
            _user32.SetProcessWindowStation(hwinsta_prev)
            _user32.CloseWindowStation(hwinsta)

    return titles
