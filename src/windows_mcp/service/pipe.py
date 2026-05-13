"""Named pipe client — runs in the user-mode broker process.

Usage
-----
    from windows_mcp.service.pipe import get_client

    client = get_client()
    if client.is_available():
        png = client.screenshot()   # bytes
        name = client.desktop_name()  # "Default" | "Winlogon"
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any

from .protocol import PIPE_NAME, PIPE_BUFFER_SIZE, CALL_TIMEOUT_MS, Request, Response

logger = logging.getLogger(__name__)

try:
    import win32file
    import win32pipe
    import pywintypes
    _WIN32_AVAILABLE = True
except ImportError:
    _WIN32_AVAILABLE = False

# Availability is cached for this many seconds to avoid a ping on every screenshot.
_AVAILABILITY_CACHE_TTL = 30.0


class HostServiceClient:
    """Client for the Windows MCP host service named pipe."""

    def __init__(self) -> None:
        self._available: bool | None = None
        self._available_ts: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Return True if the host service is running and reachable."""
        if not _WIN32_AVAILABLE:
            return False
        now = time.monotonic()
        if self._available is not None and (now - self._available_ts) < _AVAILABILITY_CACHE_TTL:
            return self._available
        try:
            result = self._call("ping", {})
            available = result == "pong"
        except Exception:
            available = False
        self._available = available
        self._available_ts = now
        return available

    def invalidate_cache(self) -> None:
        """Force the next is_available() call to re-check the pipe."""
        self._available = None

    def desktop_name(self) -> str:
        """Return the name of the current input desktop ('Default' or 'Winlogon')."""
        return self._call("desktop_name", {})

    def screenshot(self) -> bytes:
        """Capture the current input desktop (incl. UAC). Returns PNG bytes."""
        encoded = self._call("screenshot", {})
        return base64.b64decode(encoded)

    def uia_windows(self) -> list[str]:
        """Return top-level window titles visible on the input desktop."""
        return self._call("uia_windows", {})

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _call(self, method: str, params: dict[str, Any]) -> Any:
        if not _WIN32_AVAILABLE:
            raise RuntimeError("pywin32 is not available")

        req = Request(method=method, params=params)

        try:
            # Block until the pipe is available (or timeout).
            win32pipe.WaitNamedPipe(PIPE_NAME, CALL_TIMEOUT_MS)

            handle = win32file.CreateFile(
                PIPE_NAME,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0,
                None,
                win32file.OPEN_EXISTING,
                0,
                None,
            )
        except pywintypes.error as exc:
            raise RuntimeError(f"Cannot connect to host service pipe: {exc}") from exc

        try:
            # Switch to message read mode so we get whole messages back.
            win32pipe.SetNamedPipeHandleState(
                handle,
                win32pipe.PIPE_READMODE_MESSAGE,
                None,
                None,
            )
            win32file.WriteFile(handle, req.encode())
            _, data = win32file.ReadFile(handle, PIPE_BUFFER_SIZE)
        except pywintypes.error as exc:
            raise RuntimeError(f"Pipe I/O error: {exc}") from exc
        finally:
            try:
                win32file.CloseHandle(handle)
            except Exception:
                pass

        resp = Response.decode(data)
        if resp.error:
            raise RuntimeError(f"Host service error ({method}): {resp.error}")
        return resp.result


_client: HostServiceClient | None = None


def get_client() -> HostServiceClient:
    """Return the process-wide singleton pipe client."""
    global _client
    if _client is None:
        _client = HostServiceClient()
    return _client
