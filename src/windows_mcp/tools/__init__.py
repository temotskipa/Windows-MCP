"""tools subpackage — registers all MCP tool definitions on a FastMCP instance."""

from windows_mcp.tools import (
    app,
    clipboard,
    filesystem,
    input,
    multi,
    notification,
    process,
    registry,
    scrape,
    shell,
    snapshot,
)

_MODULES = [
    app,
    shell,
    filesystem,
    snapshot,
    input,
    scrape,
    multi,
    clipboard,
    process,
    notification,
    registry,
]


import inspect
from functools import wraps

class ProxyMCP:
    def __init__(self, mcp):
        self._mcp = mcp

    def tool(self, *args, **kwargs):
        decorator = self._mcp.tool(*args, **kwargs)

        def wrapper(func):
            sig = inspect.signature(func)
            params = list(sig.parameters.values())

            wait_param = inspect.Parameter(
                "wait_for_previous",
                inspect.Parameter.KEYWORD_ONLY,
                default=None,
                annotation=bool | None
            )

            new_params = params + [wait_param]
            new_sig = sig.replace(parameters=new_params)

            @wraps(func)
            def new_func(*f_args, **f_kwargs):
                f_kwargs.pop("wait_for_previous", None)
                return func(*f_args, **f_kwargs)

            new_func.__signature__ = new_sig
            new_func.__annotations__["wait_for_previous"] = bool | None

            return decorator(new_func)
        return wrapper

    def __getattr__(self, name):
        return getattr(self._mcp, name)


def register_all(mcp, *, get_desktop, get_analytics):
    """Register every tool module on *mcp*.

    *get_desktop* and *get_analytics* are zero-arg callables that return the
    current ``Desktop`` and ``PostHogAnalytics`` instances (resolved lazily so
    that tools can be registered before ``lifespan`` initializes the singletons).
    """
    proxy_mcp = ProxyMCP(mcp)
    for mod in _MODULES:
        mod.register(proxy_mcp, get_desktop=get_desktop, get_analytics=get_analytics)
