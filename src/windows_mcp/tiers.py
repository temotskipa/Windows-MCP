"""Tool tier definitions and access control."""

from __future__ import annotations

TOOL_TIERS: dict[str, set[str]] = {
    "tier1": {
        # Read-only — safe to expose by default
        "Screenshot",
        "Snapshot",
        "Wait",
        "Notification",
    },
    "tier2": {
        # Interactive — enabled by default, no persistent side-effects
        "Click",
        "Type",
        "Scroll",
        "Move",
        "Shortcut",
        "MultiSelect",
        "MultiEdit",
        "Clipboard",
        "Scrape",
    },
    "tier3": {
        # Destructive / high-risk — disabled by default
        "App",
        "PowerShell",
        "FileSystem",
        "Registry",
        "Process",
    },
}

ALL_TOOLS: set[str] = TOOL_TIERS["tier1"] | TOOL_TIERS["tier2"] | TOOL_TIERS["tier3"]
_NAME_LOOKUP: dict[str, str] = {name.lower(): name for name in ALL_TOOLS}


def parse_tool_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def normalize_tool_names(tool_names: list[str]) -> list[str]:
    normalized = []
    unknown = []
    for name in tool_names:
        hit = _NAME_LOOKUP.get(name.lower())
        if hit:
            normalized.append(hit)
        else:
            unknown.append(name)
    if unknown:
        allowed = ", ".join(sorted(ALL_TOOLS))
        raise ValueError(f"Unknown tools: {', '.join(unknown)}. Allowed: {allowed}")
    return normalized


def resolve_enabled_tools(
    *,
    enable_tier3: bool = False,
    disable_tier2: bool = False,
    enable_all: bool = False,
    explicit_tools: list[str] | None = None,
    exclude_tools: list[str] | None = None,
) -> set[str]:
    """Compute the active tool set.

    Precedence: explicit tool list > tier toggles > defaults (tier1 + tier2).
    """
    explicit_tools = explicit_tools or []
    exclude_tools = exclude_tools or []

    if explicit_tools:
        enabled = set(normalize_tool_names(explicit_tools))
    elif enable_all:
        enabled = set(ALL_TOOLS)
    else:
        enabled = set(TOOL_TIERS["tier1"])
        if not disable_tier2:
            enabled |= TOOL_TIERS["tier2"]
        if enable_tier3:
            enabled |= TOOL_TIERS["tier3"]

    if exclude_tools:
        enabled -= set(normalize_tool_names(exclude_tools))

    return enabled


def get_tier_labels(enabled_tools: set[str]) -> list[str]:
    labels = []
    if TOOL_TIERS["tier1"] & enabled_tools:
        labels.append("1")
    if TOOL_TIERS["tier2"] & enabled_tools:
        labels.append("2")
    if TOOL_TIERS["tier3"] & enabled_tools:
        labels.append("3")
    return labels


# --- fastmcp internals -------------------------------------------------------

def _get_registered_tools(mcp) -> dict[str, object]:
    # fastmcp 2.x
    tool_mgr = getattr(mcp, "_tool_manager", None)
    tools = getattr(tool_mgr, "_tools", None)
    if isinstance(tools, dict):
        return tools

    # fastmcp 3.x
    provider = getattr(mcp, "_local_provider", None)
    components = getattr(provider, "_components", None)
    if isinstance(components, dict):
        out: dict[str, object] = {}
        for key, comp in components.items():
            if not isinstance(key, str) or not key.startswith("tool:"):
                continue
            name = getattr(comp, "name", None)
            if not isinstance(name, str) or not name:
                name = key.split(":", 1)[1].split("@", 1)[0]
            out[name] = comp
        return out

    raise RuntimeError("Unsupported fastmcp version: cannot locate registered tools")


def _remove_tool(mcp, name: str) -> None:
    # fastmcp 2.x
    tool_mgr = getattr(mcp, "_tool_manager", None)
    tools = getattr(tool_mgr, "_tools", None)
    if isinstance(tools, dict):
        tools.pop(name, None)
        return

    # fastmcp 3.x
    provider = getattr(mcp, "_local_provider", None)
    components = getattr(provider, "_components", None)
    if isinstance(components, dict):
        keys_to_remove = [
            k for k, v in components.items()
            if isinstance(k, str)
            and k.startswith("tool:")
            and (
                getattr(v, "name", None) == name
                or k.split(":", 1)[1].split("@", 1)[0] == name
            )
        ]
        for k in keys_to_remove:
            components.pop(k, None)


def filter_tools(mcp, enabled_tools: set[str]) -> dict[str, int]:
    """Remove disabled tools from the MCP registry. Returns counts."""
    all_registered = list(_get_registered_tools(mcp).keys())
    total = len(all_registered)
    for name in all_registered:
        if name not in enabled_tools:
            _remove_tool(mcp, name)
    return {"enabled": len(enabled_tools & set(all_registered)), "total": total}
