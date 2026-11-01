"""Logging setup: console + rotating file in logs/."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from backend.core.config import Settings
from backend.core.paths import ensure_user_directories, user_data_dir

_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def setup_logging(settings: Settings) -> None:
    """Configure the root logger once (safe to call repeatedly)."""
    root = logging.getLogger()
    ensure_user_directories()
    if getattr(root, "_overpine_configured", False):
        return

    level = getattr(logging, settings.log_level, logging.INFO)
    root.setLevel(level)
    formatter = logging.Formatter(_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    configured_log = Path(settings.log_file)
    data_root = user_data_dir().resolve()
    log_path = (
        configured_log.resolve()
        if configured_log.is_absolute()
        else (data_root / configured_log).resolve()
    )
    if not log_path.is_relative_to(data_root):
        raise ValueError("Configured log file must stay inside OVERPINE writable data")
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError as exc:
        logging.getLogger(__name__).warning("File logging disabled: %s", exc)

    root._overpine_configured = True  # type: ignore[attr-defined]
