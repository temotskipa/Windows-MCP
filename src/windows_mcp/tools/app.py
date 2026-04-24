"""App tool — launch, resize, switch applications."""

from typing import Literal

from mcp.types import ToolAnnotations
from windows_mcp.analytics import with_analytics
from fastmcp import Context


def register(mcp, *, get_desktop, get_analytics):
    @mcp.tool(
        name="App",
        description="Open/start/launch applications and manage windows. Keywords: open, start, launch, program, application, window, foreground, focus, resize. Three modes: 'launch' (opens the prescribed application), 'resize' (adjusts the size/position of a named window or the active window if name is omitted), 'switch' (brings specific window into focus).",
        annotations=ToolAnnotations(
            title="App",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "App-Tool")
    def app_tool(
        mode: Literal['launch', 'resize', 'switch'] = 'launch',
        name: str | None = None,
        window_loc: list[int] | None = None,
        window_size: list[int] | None = None,
        ctx: Context = None,
    ):
        return get_desktop().app(mode, name, window_loc, window_size)

    @mcp.tool(
        name="LaunchURI",
        description="Launch an application or URI scheme using the default handler. Examples: spotify://, ms-settings:, https://...",
        annotations=ToolAnnotations(
            title="LaunchURI",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "LaunchURI-Tool")
    def launch_uri_tool(uri: str, ctx: Context = None) -> str:
        import os
        try:
            os.startfile(uri)
            return f"Successfully launched URI: {uri}"
        except Exception as e:
            return f"Error launching URI '{uri}': {e}"

    @mcp.tool(
        name="SetDialogPath",
        description="Sets the absolute file path in a standard Windows 'File name:' open/save dialog and presses Enter. This improves automation reliability when uploading or saving files.",
        annotations=ToolAnnotations(
            title="SetDialogPath",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "SetDialogPath-Tool")
    def set_dialog_path_tool(path: str, ctx: Context = None) -> str:
        import pywinauto
        try:
            desktop = pywinauto.Desktop(backend="uia")
            dialog = desktop.window(title_re=".*", class_name="#32770", visible_only=True)
            if not dialog.exists(timeout=3):
                return "Error: Could not find any active file dialog window."

            # Find the File name combo box/edit control
            file_name_edit = dialog.child_window(title="File name:", control_type="Edit", found_index=0)
            if not file_name_edit.exists(timeout=2):
                 file_name_edit = dialog.child_window(title="File name:", control_type="ComboBox", found_index=0)
                 if not file_name_edit.exists(timeout=2):
                     return "Error: Could not find 'File name:' input field in the active dialog."

            file_name_edit.set_edit_text(path)

            # Find and click Open or Save
            button = dialog.child_window(title="Open", control_type="Button", found_index=0)
            if not button.exists():
                button = dialog.child_window(title="Save", control_type="Button", found_index=0)

            if button.exists():
                button.click()
            else:
                file_name_edit.type_keys("{ENTER}")

            return f"Successfully set dialog path to: {path}"
        except Exception as e:
            return f"Error setting dialog path: {e}"
