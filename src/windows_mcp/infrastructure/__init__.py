"""Infrastructure layer — cross-cutting concerns: auth, security, analytics."""

from windows_mcp.infrastructure.auth import AuthKeyMiddleware, is_loopback_host
from windows_mcp.infrastructure.security import (
    IPAllowlistMiddleware,
    parse_ip_allowlist,
    validate_url,
)
from windows_mcp.infrastructure.analytics import Analytics, PostHogAnalytics, with_analytics

__all__ = [
    "AuthKeyMiddleware",
    "is_loopback_host",
    "IPAllowlistMiddleware",
    "parse_ip_allowlist",
    "validate_url",
    "Analytics",
    "PostHogAnalytics",
    "with_analytics",
]
