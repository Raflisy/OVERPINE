"""Application resource and writable-directory constants."""
from __future__ import annotations

from backend.core.paths import resource_dir, user_data_dir

PROJECT_ROOT = resource_dir()
USER_DATA_DIR = user_data_dir()
CONFIG_FILE = PROJECT_ROOT / "config" / "default_config.toml"
FRONTEND_DIR = PROJECT_ROOT / "frontend"
LOGS_DIR = USER_DATA_DIR / "logs"
DATA_DIR = USER_DATA_DIR / "data"
EXPORTS_DIR = DATA_DIR / "exports"
STRESS_DIR = DATA_DIR / "stress"

__all__ = [
    "CONFIG_FILE",
    "DATA_DIR",
    "EXPORTS_DIR",
    "FRONTEND_DIR",
    "LOGS_DIR",
    "PROJECT_ROOT",
    "STRESS_DIR",
    "USER_DATA_DIR",
]
