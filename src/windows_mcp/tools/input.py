"""Input tools — Click, Type, Scroll, Move, Shortcut, Wait."""

import time
from typing import Literal

from mcp.types import ToolAnnotations
from windows_mcp.analytics import with_analytics
from fastmcp import Context


def _resolve_label(desktop, label):
    """Resolve a UI element label to screen coordinates."""
    if desktop.desktop_state is None:
        raise ValueError("Desktop state is empty. Please call Snapshot first.")
    try:
        return list(desktop.get_coordinates_from_label(label))
    except Exception as e:
        raise ValueError(f"Failed to find element with label {label}: {e}")


def register(mcp, *, get_desktop, get_analytics):
    @mcp.tool(
        name="Click",
        description=(
            "Performs mouse clicks at specified coordinates [x, y] or passing a UI element's label/id. "
            "Supports button types: 'left' for selection/activation, 'right' for context menus, 'middle'. "
            "Supports clicks: 0=hover only (no click), 1=single click (select/focus), 2=double click (open/activate). "
            "Provide either loc or label."
        ),
        annotations=ToolAnnotations(
            title="Click",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Click-Tool")
    def click_tool(
        loc: list[int] | None = None,
        label: int | None = None,
        button: Literal["left", "right", "middle"] = "left",
        clicks: int = 1,
        ctx: Context = None,
    ) -> str:
        desktop = get_desktop()
        if loc is None and label is None:
            raise ValueError("Either loc or label must be provided.")
        if label is not None:
            loc = _resolve_label(desktop, label)
        if len(loc) != 2:
            raise ValueError("Location must be a list of exactly 2 integers [x, y]")
        x, y = loc[0], loc[1]
        desktop.click(loc=loc, button=button, clicks=clicks)
        num_clicks = {0: "Hover", 1: "Single", 2: "Double"}
        return f"{num_clicks.get(clicks)} {button} clicked at ({x},{y})."

    @mcp.tool(
        name="Type",
        description="Types text at specified coordinates [x, y] or passing a UI element's label/id. Set clear=True to clear existing text first, False to append. Set press_enter=True to submit after typing. Set caret_position to 'start' (beginning), 'end' (end), or 'idle' (default). Provide either loc or label.",
        annotations=ToolAnnotations(
            title="Type",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Type-Tool")
    def type_tool(
        text: str,
        loc: list[int] | None = None,
        label: int | None = None,
        clear: bool | str = False,
        caret_position: Literal["start", "idle", "end"] = "idle",
        press_enter: bool | str = False,
        ctx: Context = None,
    ) -> str:
        desktop = get_desktop()
        if loc is None and label is None:
            raise ValueError("Either loc or label must be provided.")
        if label is not None:
            loc = _resolve_label(desktop, label)
        if len(loc) != 2:
            raise ValueError("Location must be a list of exactly 2 integers [x, y]")
        x, y = loc[0], loc[1]
        desktop.type(
            loc=loc,
            text=text,
            caret_position=caret_position,
            clear=clear,
            press_enter=press_enter,
        )
        return f"Typed {text} at ({x},{y})."

    @mcp.tool(
        name="Scroll",
        description="Scrolls at coordinates [x, y], a UI element's label/id, or current mouse position if loc=None. Type: vertical (default) or horizontal. Direction: up/down for vertical, left/right for horizontal. wheel_times controls amount (1 wheel ≈ 3-5 lines). Use for navigating long content, lists, and web pages.",
        annotations=ToolAnnotations(
            title="Scroll",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Scroll-Tool")
    def scroll_tool(
        loc: list[int] | None = None,
        label: int | None = None,
        type: Literal["horizontal", "vertical"] = "vertical",
        direction: Literal["up", "down", "left", "right"] = "down",
        wheel_times: int = 1,
        ctx: Context = None,
    ) -> str:
        desktop = get_desktop()
        if label is not None:
            loc = _resolve_label(desktop, label)
        if loc and len(loc) != 2:
            raise ValueError("Location must be a list of exactly 2 integers [x, y]")
        response = desktop.scroll(loc, type, direction, wheel_times)
        if response:
            return response
        return (
            f"Scrolled {type} {direction} by {wheel_times} wheel times" + f" at ({loc[0]},{loc[1]})."
            if loc
            else ""
        )

    @mcp.tool(
        name="Move",
        description=(
            "Moves mouse cursor to coordinates [x, y] or passing a UI element's label/id. "
            "Set drag=True to perform a drag-and-drop operation from the current mouse position "
            "to the target coordinates. Default (drag=False) is a simple cursor move (hover). "
            "Provide either loc or label."
        ),
        annotations=ToolAnnotations(
            title="Move",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Move-Tool")
    def move_tool(
        loc: list[int] | None = None,
        label: int | None = None,
        drag: bool | str = False,
        ctx: Context = None,
    ) -> str:
        desktop = get_desktop()
        drag = drag is True or (isinstance(drag, str) and drag.lower() == "true")
        if loc is None and label is None:
            raise ValueError("Either loc or label must be provided.")
        if label is not None:
            loc = _resolve_label(desktop, label)
        if len(loc) != 2:
            raise ValueError("loc must be a list of exactly 2 integers [x, y]")
        x, y = loc[0], loc[1]
        if drag:
            desktop.drag(loc)
            return f"Dragged to ({x},{y})."
        else:
            desktop.move(loc)
            return f"Moved the mouse pointer to ({x},{y})."

    @mcp.tool(
        name="Shortcut",
        description='Executes keyboard shortcuts using key combinations separated by +. Examples: "ctrl+c" (copy), "ctrl+v" (paste), "alt+tab" (switch apps), "win+r" (Run dialog), "win" (Start menu), "ctrl+shift+esc" (Task Manager). Use for quick actions and system commands.',
        annotations=ToolAnnotations(
            title="Shortcut",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Shortcut-Tool")
    def shortcut_tool(shortcut: str, ctx: Context = None):
        get_desktop().shortcut(shortcut)
        return f"Pressed {shortcut}."

    @mcp.tool(
        name="Wait",
        description="Pauses execution for specified duration in seconds. Use when waiting for: applications to launch/load, UI animations to complete, page content to render, dialogs to appear, or between rapid actions. Helps ensure UI is ready before next interaction.",
        annotations=ToolAnnotations(
            title="Wait",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Wait-Tool")
    def wait_tool(duration: int, ctx: Context = None) -> str:
        time.sleep(duration)
        return f"Waited for {duration} seconds."

    @mcp.tool(
        name="WaitForElement",
        description="Waits until a specific UI element appears on the screen (by exact name). Returns the coordinates if found, or times out. Use this instead of Wait when waiting for a specific window, button, or loading text to appear.",
        annotations=ToolAnnotations(
            title="WaitForElement",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "WaitForElement-Tool")
    def wait_for_element_tool(name: str, timeout: int = 30, ctx: Context = None) -> str:
        desktop = get_desktop()
        start_time = time.time()
        while time.time() - start_time < timeout:
            state = desktop.get_state(use_vision=False, use_annotation=False, use_dom=False, use_ui_tree=True)
            if state and state.tree:
                for node in state.tree.interactive_nodes + state.tree.scrollable_nodes:
                    if node.name == name:
                        return f"Found '{name}' at {node.center.to_string()} after {int(time.time() - start_time)} seconds."
            time.sleep(1)
        return f"Error: Timeout after {timeout} seconds waiting for element '{name}'."

    @mcp.tool(
        name="FindElement",
        description="Finds UI elements matching a query (name or regex) and returns a list of them with coordinates and metadata without capturing a screenshot. Much faster and cheaper than Snapshot.",
        annotations=ToolAnnotations(
            title="FindElement",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "FindElement-Tool")
    def find_element_tool(query: str, ctx: Context = None) -> list[dict]:
        import re
        desktop = get_desktop()
        state = desktop.get_state(use_vision=False, use_annotation=False, use_dom=False, use_ui_tree=True)
        results = []
        if state and state.tree:
            all_nodes = state.tree.interactive_nodes + state.tree.scrollable_nodes
            for node in all_nodes:
                if query.lower() in node.name.lower() or re.search(query, node.name, re.IGNORECASE):
                    results.append({
                        "name": node.name,
                        "control_type": node.control_type,
                        "window_name": node.window_name,
                        "coords": node.center.to_string(),
                        "metadata": node.metadata
                    })
        return results
