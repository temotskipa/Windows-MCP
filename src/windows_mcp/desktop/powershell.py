"""Static PowerShell command executor utility."""

import base64
import logging
import os
import shutil
import subprocess

from windows_mcp.desktop.utils import run_with_graceful_timeout

logger = logging.getLogger(__name__)


class PowerShellExecutor:
    """Static utility class for executing PowerShell commands."""

    @staticmethod
    def execute_command(
        command: str, timeout: int = 10, shell: str | None = None
    ) -> tuple[str, int]:
        try:
            # $OutputEncoding: controls how PS5.1 encodes output written to its stdout pipe.
            # Without this set to UTF-8, PS5.1 uses the system codepage and native process
            # stdout is silently lost when Python reads the pipe.
            # [Console]::OutputEncoding: controls how PS decodes bytes from native exe stdout.
            utf8_command = (
                "$OutputEncoding = [System.Text.Encoding]::UTF8; "
                "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
                f"{command}"
            )
            encoded = base64.b64encode(utf8_command.encode("utf-16le")).decode("ascii")
            env = os.environ.copy()
            # NO_COLOR suppresses ANSI escape sequences in pwsh 7.2+ (and many other CLI tools).
            # PS5.1 has no ANSI output, so this is harmlessly ignored there.
            # https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_ansi_terminals#disabling-ansi-output
            env["NO_COLOR"] = "1"

            # Rebuild PATH and PATHEXT from registry so system executables (e.g. OpenSSH at
            # C:\Windows\System32\OpenSSH) are discoverable without requiring absolute paths.
            # The inherited env may be stripped down by venv activation or the MCP host.
            try:
                import winreg

                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                ) as machine_key:
                    machine_path = winreg.QueryValueEx(machine_key, "PATH")[0]
                    if ".EXE" not in env.get("PATHEXT", ""):
                        env["PATHEXT"] = winreg.QueryValueEx(machine_key, "PATHEXT")[0]

                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as user_key:
                        user_path = winreg.QueryValueEx(user_key, "PATH")[0]
                except FileNotFoundError:
                    user_path = ""

                env["PATH"] = ";".join(filter(None, [machine_path, user_path, env.get("PATH", "")]))
            except Exception:
                if ".EXE" not in env.get("PATHEXT", ""):
                    env["PATHEXT"] = ".COM;.EXE;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH;.MSC;.CPL;.PY;.PYW"

            shell = shell or ("pwsh" if shutil.which("pwsh") else "powershell")

            args = [shell, "-NoProfile"]
            # Only older Windows PowerShell (5.1) uses -OutputFormat Text successfully here
            shell_name = os.path.basename(shell).lower().replace(".exe", "")
            if shell_name == "powershell":
                args.extend(["-OutputFormat", "Text"])
            args.extend(["-EncodedCommand", encoded])

            result = run_with_graceful_timeout(
                args,
                stdin=subprocess.DEVNULL,  # Prevent child processes from inheriting the MCP pipe stdin
                capture_output=True,  # No errors='ignore' - let subprocess return bytes
                timeout=timeout,
                cwd=os.path.expanduser(path="~"),
                env=env,
            )
            # Handle both bytes and str output (subprocess behavior varies by environment)
            stdout = result.stdout
            stderr = result.stderr
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            return stdout or stderr, result.returncode
        except subprocess.TimeoutExpired:
            return "Command execution timed out", 1
        except Exception as e:
            return f"Command execution failed: {type(e).__name__}: {e}", 1
