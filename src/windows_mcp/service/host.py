"""Windows MCP host service — runs as NT AUTHORITY\\SYSTEM.

This module serves two purposes:

1. **Service class** (``WindowsMCPHostService``) — a ``pywin32``
   ``ServiceFramework`` subclass installed via ``windows-mcp service install``.
   It starts a named pipe server that handles privileged desktop operations
   requested by the user-mode broker.

2. **Entry point** — when executed directly (``python -m
   windows_mcp.service.host``) it delegates to pywin32's
   ``HandleCommandLine``, which is how ``sc.exe`` / the SCM invokes service
   executables.

Named pipe server design
------------------------
* Message-mode pipe so each request/response is a discrete packet.
* One pipe instance per client connection; a fresh instance is created after
  each client disconnects so the server is always listening.
* Each connection is handled on its own daemon thread — the main service
  thread just waits for the stop event.
* Security descriptor allows only SYSTEM + the interactive console user.
"""

from __future__ import annotations

import base64
import logging
import threading
from typing import Any

from .protocol import PIPE_NAME, PIPE_BUFFER_SIZE, Request, Response
from . import secure_desktop

logger = logging.getLogger(__name__)

try:
    import win32file
    import win32pipe
    import win32security
    import win32service
    import win32serviceutil
    import win32event
    import pywintypes
    import servicemanager
    _WIN32_AVAILABLE = True
except ImportError:
    _WIN32_AVAILABLE = False

# ---------------------------------------------------------------------------
# Pipe security
# ---------------------------------------------------------------------------

def _build_pipe_sa() -> Any:
    """Return a SECURITY_ATTRIBUTES restricting pipe access to SYSTEM + console user."""
    if not _WIN32_AVAILABLE:
        return None
    try:
        dacl = win32security.ACL()
        _rw = win32file.GENERIC_READ | win32file.GENERIC_WRITE

        # Always allow SYSTEM (the service itself needs to create/own the pipe).
        system_sid = win32security.CreateWellKnownSid(
            win32security.WinLocalSystemSid, None
        )
        dacl.AddAccessAllowedAce(win32security.ACL_REVISION, _rw, system_sid)

        # Allow the interactive console session user so the broker can connect.
        try:
            import win32ts
            session_id = win32ts.WTSGetActiveConsoleSessionId()
            username = win32ts.WTSQuerySessionInformation(
                win32ts.WTS_CURRENT_SERVER_HANDLE,
                session_id,
                win32ts.WTSUserName,
            )
            domain = win32ts.WTSQuerySessionInformation(
                win32ts.WTS_CURRENT_SERVER_HANDLE,
                session_id,
                win32ts.WTSDomainName,
            )
            if username:
                user_sid, _, _ = win32security.LookupAccountName(domain or None, username)
                dacl.AddAccessAllowedAce(win32security.ACL_REVISION, _rw, user_sid)
        except Exception as exc:
            logger.warning("Could not resolve console user SID for pipe DACL: %s", exc)

        sd = win32security.SECURITY_DESCRIPTOR()
        sd.SetSecurityDescriptorDacl(True, dacl, False)
        sa = win32security.SECURITY_ATTRIBUTES()
        sa.SECURITY_DESCRIPTOR = sd
        return sa
    except Exception as exc:
        logger.warning("Failed to build pipe security descriptor, using default: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Request dispatcher
# ---------------------------------------------------------------------------

def _dispatch(req: Request) -> Response:
    """Execute a single request and return a response."""
    try:
        match req.method:
            case "ping":
                return Response(id=req.id, result="pong")

            case "desktop_name":
                name = secure_desktop.get_input_desktop_name()
                return Response(id=req.id, result=name)

            case "screenshot":
                png = secure_desktop.capture_screenshot()
                return Response(id=req.id, result=base64.b64encode(png).decode())

            case "uia_windows":
                titles = secure_desktop.uia_get_window_titles()
                return Response(id=req.id, result=titles)

            case _:
                return Response(id=req.id, error=f"Unknown method: {req.method!r}")

    except Exception as exc:
        logger.exception("Unhandled error dispatching method %r", req.method)
        return Response(id=req.id, error=str(exc))


# ---------------------------------------------------------------------------
# Pipe server
# ---------------------------------------------------------------------------

class PipeServer:
    """Synchronous named pipe server — one daemon thread per client."""

    def __init__(self) -> None:
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        """Accept connections forever until stop() is called."""
        logger.info("Pipe server starting on %s", PIPE_NAME)
        while not self._stop.is_set():
            sa = _build_pipe_sa()
            try:
                handle = win32pipe.CreateNamedPipe(
                    PIPE_NAME,
                    win32pipe.PIPE_ACCESS_DUPLEX,
                    win32pipe.PIPE_TYPE_MESSAGE
                    | win32pipe.PIPE_READMODE_MESSAGE
                    | win32pipe.PIPE_WAIT,
                    win32pipe.PIPE_UNLIMITED_INSTANCES,
                    PIPE_BUFFER_SIZE,
                    PIPE_BUFFER_SIZE,
                    0,
                    sa,
                )
            except pywintypes.error as exc:
                logger.error("CreateNamedPipe failed: %s", exc)
                break

            try:
                # Blocks here until a client connects.
                win32pipe.ConnectNamedPipe(handle, None)
            except pywintypes.error as exc:
                logger.warning("ConnectNamedPipe failed: %s", exc)
                try:
                    win32file.CloseHandle(handle)
                except Exception:
                    pass
                continue

            threading.Thread(
                target=_serve_one_client,
                args=(handle,),
                daemon=True,
                name="pipe-client",
            ).start()

        logger.info("Pipe server stopped")


def _serve_one_client(handle: Any) -> None:
    """Read one request, write one response, close the connection."""
    try:
        _, data = win32file.ReadFile(handle, PIPE_BUFFER_SIZE)
        req = Request.decode(data)
        resp = _dispatch(req)
        win32file.WriteFile(handle, resp.encode())
    except Exception as exc:
        logger.warning("Error serving pipe client: %s", exc)
    finally:
        try:
            win32pipe.DisconnectNamedPipe(handle)
            win32file.CloseHandle(handle)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Windows service
# ---------------------------------------------------------------------------

if _WIN32_AVAILABLE:
    class WindowsMCPHostService(win32serviceutil.ServiceFramework):
        _svc_name_ = "WindowsMCPHost"
        _svc_display_name_ = "Windows MCP Host"
        _svc_description_ = (
            "Privileged helper for Windows MCP.  Enables screenshot capture and "
            "UI Automation access to the Secure Desktop (UAC consent prompts) so "
            "that LLM agents can operate Windows unattended."
        )

        def __init__(self, args: Any) -> None:
            win32serviceutil.ServiceFramework.__init__(self, args)
            self._stop_event = win32event.CreateEvent(None, 0, 0, None)
            self._server = PipeServer()

        def SvcStop(self) -> None:
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self._server.stop()
            win32event.SetEvent(self._stop_event)

        def SvcDoRun(self) -> None:
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STARTED,
                (self._svc_name_, ""),
            )
            server_thread = threading.Thread(
                target=self._server.run, daemon=True, name="pipe-server"
            )
            server_thread.start()
            win32event.WaitForSingleObject(self._stop_event, win32event.INFINITE)
            server_thread.join(timeout=5)
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STOPPED,
                (self._svc_name_, ""),
            )


# ---------------------------------------------------------------------------
# Direct invocation (used by the SCM)
# ---------------------------------------------------------------------------

def main() -> None:
    if not _WIN32_AVAILABLE:
        raise SystemExit("pywin32 is required to run the host service")
    if len(__import__("sys").argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(WindowsMCPHostService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(WindowsMCPHostService)


if __name__ == "__main__":
    main()
