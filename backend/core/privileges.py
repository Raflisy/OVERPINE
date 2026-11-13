"""Windows administrator detection and explicit elevation support."""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path


def is_admin() -> bool:
    """Return whether this process has administrator privileges."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def restart_as_admin(on_launch: Callable[[], None] | None = None) -> None:
    """Relaunch the current executable or script with a UAC prompt, then exit."""
    if os.name != "nt":
        raise OSError("Administrator restart is available only on Windows")

    if getattr(sys, "frozen", False):
        executable = sys.executable
        arguments = sys.argv[1:]
        working_directory = Path(sys.executable).resolve().parent
    else:
        executable = sys.executable
        script = str(Path(sys.argv[0]).resolve())
        arguments = [script, *sys.argv[1:]]
        working_directory = Path.cwd()

    parameters = subprocess.list2cmdline(arguments)
    result = ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        executable,
        parameters,
        str(working_directory),
        1,
    )
    if result <= 32:
        raise OSError(f"Administrator restart failed with ShellExecuteW code {result}")
    if on_launch is not None:
        on_launch()
    os._exit(0)
