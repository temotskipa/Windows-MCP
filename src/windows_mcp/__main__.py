from contextlib import asynccontextmanager
from windows_mcp.config import is_debug, enable_debug
from windows_mcp.infrastructure import (
    AuthKeyMiddleware,
    OAuthOnlyMiddleware,
    is_loopback_host,
    IPAllowlistMiddleware,
    parse_ip_allowlist,
    discover_config_path,
    load_config,
    OAuthStore,
    build_oauth_routes,
    validate_oauth_token,
)
from windows_mcp.tiers import filter_tools, get_tier_labels, parse_tool_csv, resolve_enabled_tools
from click.core import ParameterSource
from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from dataclasses import dataclass, field
from textwrap import dedent
from enum import Enum
from typing import Any
import logging
import asyncio
import click
import os

logger = logging.getLogger(__name__)

desktop: Any | None = None
watchdog: Any | None = None
analytics: Any | None = None
screen_size: Any | None = None
_mcp: FastMCP | None = None

instructions = dedent("""
Windows MCP server provides tools to interact directly with the Windows desktop,
thus enabling to operate the desktop on the user's behalf.
""")


def _get_desktop():
    return desktop


def _get_analytics():
    return analytics


def _http_middleware(
    auth_key: str | None = None,
    ip_allowlist: list | None = None,
    oauth_validator=None,
) -> list:
    """Return ASGI middleware for HTTP transports including CORS and OPTIONS handling."""
    middleware = [
        Middleware(OptionsMiddleware),
        Middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]),
    ]
    if ip_allowlist:
        middleware.append(Middleware(IPAllowlistMiddleware, allowlist=ip_allowlist))
    if auth_key:
        middleware.append(Middleware(AuthKeyMiddleware, auth_key=auth_key, oauth_validator=oauth_validator))
    elif oauth_validator:
        middleware.append(Middleware(OAuthOnlyMiddleware, oauth_validator=oauth_validator))
    return middleware


def _param_explicit(ctx: click.Context, name: str) -> bool:
    src = ctx.get_parameter_source(name)
    return src in {ParameterSource.COMMANDLINE, ParameterSource.ENVIRONMENT}


def _choose_value(ctx: click.Context, name: str, cli_value, config_value, default_value):
    if _param_explicit(ctx, name):
        return cli_value
    if config_value is not None:
        return config_value
    return default_value


class OptionsMiddleware:
    """ASGI middleware that intercepts OPTIONS requests and returns 200 OK."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["method"] == "OPTIONS":
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        [b"content-length", b"0"],
                        [b"access-control-allow-origin", b"*"],
                        [b"access-control-allow-methods", b"*"],
                        [b"access-control-allow-headers", b"*"],
                    ],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b"",
                }
            )
        else:
            await self.app(scope, receive, send)


def _build_mcp() -> FastMCP:
    """Create the MCP server instance."""
    global _mcp

    if _mcp is not None:
        return _mcp

    from windows_mcp.infrastructure import PostHogAnalytics
    from windows_mcp.desktop.service import Desktop
    from windows_mcp.tools import register_all
    from windows_mcp.watchdog.service import WatchDog

    @asynccontextmanager
    async def lifespan(app: FastMCP):
        """Runs initialization code before the server starts and cleanup code after it shuts down."""
        global desktop, watchdog, analytics, screen_size

        if os.getenv("ANONYMIZED_TELEMETRY", "true").lower() != "false":
            analytics = PostHogAnalytics()
        desktop = Desktop()
        watchdog = WatchDog()
        screen_size = desktop.get_screen_size()
        watchdog.set_focus_callback(desktop.tree.on_focus_change)

        try:
            watchdog.start()
            await asyncio.sleep(1)  # Simulate startup latency
            logger.debug("Server started, entering main loop")
            yield
        finally:
            logger.debug("Shutting down: stopping watchdog and analytics")
            if watchdog:
                watchdog.stop()
            if analytics:
                await analytics.close()

    _mcp = FastMCP(name="windows-mcp", instructions=instructions, lifespan=lifespan)
    register_all(_mcp, get_desktop=_get_desktop, get_analytics=_get_analytics)
    return _mcp


def __getattr__(name: str):
    if name in {"state_tool", "screenshot_tool"}:
        _build_mcp()
        from windows_mcp.tools import snapshot

        tool = getattr(snapshot, name)
        if tool is None:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        return getattr(tool, "fn", tool)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")




class Transport(Enum):
    STDIO = "stdio"
    SSE = "sse"
    STREAMABLE_HTTP = "streamable-http"

    def __str__(self):
        return self.value


def _run_server(
    transport: str,
    host: str,
    port: int,
    auth_key: str | None = None,
    ip_allowlist: list | None = None,
    enabled_tools: set | None = None,
    ssl_certfile: str | None = None,
    ssl_keyfile: str | None = None,
    oauth_validator=None,
) -> None:
    mcp = _build_mcp()
    if enabled_tools is not None:
        counts = filter_tools(mcp, enabled_tools)
        tiers = get_tier_labels(enabled_tools)
        logger.debug(
            "Tool filter applied: %d/%d enabled (tiers: %s)",
            counts["enabled"], counts["total"], ",".join(tiers) or "none",
        )
    match transport:
        case Transport.STDIO.value:
            mcp.run(transport=Transport.STDIO.value, show_banner=False)
        case Transport.SSE.value | Transport.STREAMABLE_HTTP.value:
            uvicorn_config: dict = {}
            if ssl_certfile and ssl_keyfile:
                uvicorn_config["ssl_certfile"] = ssl_certfile
                uvicorn_config["ssl_keyfile"] = ssl_keyfile
            mcp.run(
                transport=transport,
                host=host,
                port=port,
                show_banner=False,
                middleware=_http_middleware(auth_key=auth_key, ip_allowlist=ip_allowlist, oauth_validator=oauth_validator),
                uvicorn_config=uvicorn_config or None,
            )
        case _:
            raise ValueError(f"Invalid transport: {transport}")


@click.command()
@click.pass_context
@click.option(
    "--transport",
    help="The transport layer used by the MCP server.",
    type=click.Choice(
        [Transport.STDIO.value, Transport.SSE.value, Transport.STREAMABLE_HTTP.value]
    ),
    default="stdio",
)
@click.option(
    "--host",
    help="Host to bind the SSE/Streamable HTTP server.",
    default="localhost",
    type=str,
    show_default=True,
)
@click.option(
    "--port",
    help="Port to bind the SSE/Streamable HTTP server.",
    default=8000,
    type=int,
    show_default=True,
)
@click.option(
    "--debug",
    help="Enable debug mode to provide verbose logging for troubleshooting.",
    is_flag=True,
    default=False,
    show_default=True,
)
@click.option(
    "--config",
    help="Path to windows-mcp config file (default: ~/.windows-mcp/config.toml).",
    default=None,
    type=click.Path(dir_okay=False),
    show_default=False,
)
@click.option(
    "--auth-key",
    help="Bearer token required on all HTTP requests. Can also be set via WINDOWS_MCP_AUTH_KEY.",
    default=None,
    envvar="WINDOWS_MCP_AUTH_KEY",
    type=str,
    show_default=False,
)
@click.option(
    "--allow-insecure-remote",
    help="Allow binding to non-loopback addresses without authentication (not recommended).",
    is_flag=True,
    default=False,
    show_default=True,
)
@click.option(
    "--ip-allowlist",
    help="Comma-separated list of allowed client IPs or CIDR ranges (e.g. '10.0.0.0/8,192.168.1.5'). IPv4 and IPv6 supported.",
    default=None,
    envvar="WINDOWS_MCP_IP_ALLOWLIST",
    type=str,
    show_default=False,
)
@click.option(
    "--enable-tier3",
    help="Enable Tier 3 high-risk tools: App, PowerShell, FileSystem, Registry, Process.",
    is_flag=True,
    default=False,
)
@click.option(
    "--disable-tier2",
    help="Disable Tier 2 interactive tools, leaving only read-only Tier 1 tools active.",
    is_flag=True,
    default=False,
)
@click.option(
    "--tools",
    help="Comma-separated explicit list of tools to enable, overrides tier settings (e.g. 'Screenshot,Click,Snapshot').",
    default=None,
    envvar="WINDOWS_MCP_TOOLS",
    type=str,
    show_default=False,
)
@click.option(
    "--exclude-tools",
    help="Comma-separated list of tools to remove from the active set (e.g. 'PowerShell,Registry').",
    default=None,
    envvar="WINDOWS_MCP_EXCLUDE_TOOLS",
    type=str,
    show_default=False,
)
@click.option(
    "--ssl-certfile",
    help="Path to TLS certificate file (.pem) for HTTPS. Requires --ssl-keyfile.",
    default=None,
    envvar="WINDOWS_MCP_SSL_CERTFILE",
    type=str,
    show_default=False,
)
@click.option(
    "--ssl-keyfile",
    help="Path to TLS private key file (.pem) for HTTPS. Requires --ssl-certfile.",
    default=None,
    envvar="WINDOWS_MCP_SSL_KEYFILE",
    type=str,
    show_default=False,
)
@click.option(
    "--oauth-client-id",
    help="OAuth client ID (pre-provisioned confidential client). Requires --oauth-client-secret.",
    default=None,
    envvar="WINDOWS_MCP_OAUTH_CLIENT_ID",
    type=str,
    show_default=False,
)
@click.option(
    "--oauth-client-secret",
    help="OAuth client secret. Requires --oauth-client-id.",
    default=None,
    envvar="WINDOWS_MCP_OAUTH_CLIENT_SECRET",
    type=str,
    show_default=False,
)
def main(ctx, transport, host, port, debug, config, auth_key, allow_insecure_remote, ip_allowlist, enable_tier3, disable_tier2, tools, exclude_tools, ssl_certfile, ssl_keyfile, oauth_client_id, oauth_client_secret):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    if transport == Transport.STDIO.value:
        os.environ.setdefault("NO_COLOR", "1")
    if debug:
        enable_debug()
        logging.getLogger().setLevel(logging.DEBUG)
        for name in ["uvicorn", "uvicorn.error", "uvicorn.access", "fastmcp"]:
            logging.getLogger(name).setLevel(logging.DEBUG)

    # Load config file and merge with CLI flags (CLI wins)
    config_path = discover_config_path(config)
    try:
        cfg = load_config(config_path)
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc))

    transport = _choose_value(ctx, "transport", transport, cfg.server.transport, "stdio")
    host = _choose_value(ctx, "host", host, cfg.server.host, "localhost")
    port = int(_choose_value(ctx, "port", port, cfg.server.port, 8000))
    auth_key = _choose_value(ctx, "auth_key", auth_key, cfg.server.auth_key, None)
    allow_insecure_remote = bool(
        _choose_value(ctx, "allow_insecure_remote", allow_insecure_remote, cfg.server.allow_insecure_remote, False)
    )
    ssl_certfile = _choose_value(ctx, "ssl_certfile", ssl_certfile, cfg.server.ssl_certfile, None)
    ssl_keyfile = _choose_value(ctx, "ssl_keyfile", ssl_keyfile, cfg.server.ssl_keyfile, None)
    oauth_client_id = _choose_value(ctx, "oauth_client_id", oauth_client_id, cfg.security.oauth_client_id, None)
    oauth_client_secret = _choose_value(
        ctx, "oauth_client_secret", oauth_client_secret, cfg.security.oauth_client_secret, None
    )

    cli_tools = parse_tool_csv(tools)
    cli_exclude = parse_tool_csv(exclude_tools) if _param_explicit(ctx, "exclude_tools") else cfg.tools.exclude
    cli_allowlist = [e.strip() for e in ip_allowlist.split(",")] if ip_allowlist and _param_explicit(ctx, "ip_allowlist") else cfg.security.ip_allowlist

    if bool(ssl_certfile) != bool(ssl_keyfile):
        raise click.ClickException("--ssl-certfile and --ssl-keyfile must be provided together.")

    if bool(oauth_client_id) != bool(oauth_client_secret):
        raise click.ClickException("OAuth requires both --oauth-client-id and --oauth-client-secret.")

    parsed_allowlist = None
    if cli_allowlist:
        try:
            parsed_allowlist = parse_ip_allowlist(cli_allowlist)
        except ValueError as exc:
            raise click.ClickException(f"Invalid ip_allowlist: {exc}")

    try:
        enabled_tools = resolve_enabled_tools(
            enable_tier3=enable_tier3,
            disable_tier2=disable_tier2,
            explicit_tools=cli_tools,
            exclude_tools=list(cli_exclude),
        )
    except ValueError as exc:
        raise click.ClickException(str(exc))

    configured_oauth = bool(oauth_client_id and oauth_client_secret)

    if (
        transport != Transport.STDIO.value
        and not is_loopback_host(host)
        and not auth_key
        and not configured_oauth
        and not allow_insecure_remote
    ):
        raise click.ClickException(
            f"Refusing to bind HTTP transport to '{host}' without authentication.\n"
            "  Use --auth-key <token> or --oauth-client-id/--oauth-client-secret.\n"
            "  Or pass --allow-insecure-remote to explicitly allow unauthenticated access (not recommended)."
        )

    if bool(oauth_client_id) != bool(oauth_client_secret):
        raise click.ClickException("OAuth requires both --oauth-client-id and --oauth-client-secret.")

    if (auth_key or cli_allowlist) and transport == Transport.STDIO.value:
        logger.warning("--auth-key / --ip-allowlist have no effect on stdio transport")

    # Set up OAuth routes if configured (HTTP transports only)
    oauth_validator = None
    if configured_oauth and transport != Transport.STDIO.value:
        mcp = _build_mcp()
        oauth_store = OAuthStore()
        scheme = "https" if (ssl_certfile and ssl_keyfile) else "http"
        issuer = f"{scheme}://{host}:{port}"
        routes = build_oauth_routes(
            store=oauth_store,
            issuer=issuer,
            configured_client_id=oauth_client_id,
            configured_client_secret=oauth_client_secret,
        )
        for path, (handler, methods) in routes.items():
            mcp.custom_route(path, methods=methods)(handler)
        oauth_validator = lambda tok: validate_oauth_token(oauth_store, tok)  # noqa: E731

    tiers = get_tier_labels(enabled_tools)
    scheme = "https" if ssl_certfile else "http"
    logger.debug(
        "Starting windows-mcp (transport=%s, %s, auth=%s, oauth=%s, ip-allowlist=%s, tiers=%s, tools=%d)",
        transport,
        scheme,
        "on" if auth_key else "off",
        "on" if configured_oauth else "off",
        cli_allowlist or "off",
        ",".join(tiers),
        len(enabled_tools),
    )
    try:
        _run_server(
            transport=transport,
            host=host,
            port=port,
            auth_key=auth_key,
            ip_allowlist=parsed_allowlist,
            enabled_tools=enabled_tools,
            ssl_certfile=ssl_certfile,
            ssl_keyfile=ssl_keyfile,
            oauth_validator=oauth_validator,
        )
        logger.debug("Server shut down normally")
    except Exception:
        logger.error("Server exiting due to unhandled exception", exc_info=True)
        raise


if __name__ == "__main__":
    main()
