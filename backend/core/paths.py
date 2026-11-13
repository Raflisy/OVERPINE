"""Resolve bundled resources and writable application data locations."""
from __future__ import annotations

import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def is_frozen() -> bool:
    """Return whether OVERPINE is running from a frozen executable."""
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    """Return the read-only root for bundled resources or source files."""
    if is_frozen():
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root is None:
            raise RuntimeError("Frozen OVERPINE is missing its bundled resource directory")
        return Path(bundle_root)
    return _PROJECT_ROOT


def user_data_dir() -> Path:
    """Return the writable app-data root, creating it for frozen installs."""
    if not is_frozen():
        return _PROJECT_ROOT
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("LOCALAPPDATA is not available for OVERPINE data storage")
    data_dir = Path(local_app_data) / "OVERPINE"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def ensure_user_directories() -> None:
    """Create all writable application directories before serving requests."""
    root = user_data_dir()
    for directory in (
        root / "logs",
        root / "data",
        root / "data" / "exports",
        root / "data" / "stress",
    ):
        directory.mkdir(parents=True, exist_ok=True)
