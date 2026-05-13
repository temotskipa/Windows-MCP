"""Bearer token authentication middleware for HTTP transports."""

from __future__ import annotations

import ipaddress
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp


_PUBLIC_PATHS = frozenset({"/health"})


class AuthKeyMiddleware(BaseHTTPMiddleware):
    """Validate Bearer token on all non-public HTTP paths."""

    def __init__(self, app: ASGIApp, *, auth_key: str) -> None:
        super().__init__(app)
        self._key = auth_key.encode()

    async def dispatch(self, request, call_next):
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                {"error": "Missing or invalid Authorization header. Expected: Bearer <token>"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth_header.removeprefix("Bearer ").strip().encode()
        if not secrets.compare_digest(token, self._key):
            return JSONResponse(
                {"error": "Invalid authentication token"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        return await call_next(request)


def is_loopback_host(host: str) -> bool:
    """Return True if host refers only to a loopback interface."""
    if host.lower() in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
