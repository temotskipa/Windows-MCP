"""windows_mcp.service — LocalSystem host service for Secure Desktop access.

Two sub-roles live here:

* **Host service** (``host.py``) — a Windows service that runs as LocalSystem,
  opens the Winlogon desktop via SetThreadDesktop, and exposes a named pipe
  server so the user-mode broker can request privileged operations.

* **Pipe client** (``pipe.py``) — used by the broker to call the service.
  Falls back gracefully when the service is not installed.
"""

from .pipe import get_client as get_host_client

__all__ = ["get_host_client"]
