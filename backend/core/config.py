"""Central configuration loader for OVERPINE.

Reads config/default_config.toml. If the file is missing or broken, safe
built-in defaults are used so the app can still start.
"""
from __future__ import annotations

import logging
import math
import tomllib
from dataclasses import dataclass, field

from backend.core.constants import (
    CONFIG_FILE,
    DATA_DIR,
    FRONTEND_DIR,
    LOGS_DIR,
    PROJECT_ROOT,
    USER_DATA_DIR,
)
from backend.core.paths import user_data_dir

__all__ = [
    "APP_NAME",
    "APP_VERSION",
    "CONFIG_FILE",
    "DATA_DIR",
    "FRONTEND_DIR",
    "LOGS_DIR",
    "PROJECT_ROOT",
    "USER_DATA_DIR",
    "Settings",
    "load_settings",
]

APP_NAME = "OVERPINE Project Software"
APP_VERSION = "0.4.0"

_DEFAULT_INTERVALS = {
    "cpu": 1.0,
    "memory": 2.0,
    "storage": 5.0,
    "battery": 5.0,
    "thermal": 2.0,
    "fan": 2.0,
    "power": 2.0,
    "gpu": 10.0,
    "network": 1.0,
    "windows": 10.0,
}


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"
    log_file: str = "logs/overpine.log"
    monitoring_intervals: dict[str, float] = field(
        default_factory=lambda: dict(_DEFAULT_INTERVALS)
    )
    monitoring_history_size: int = 3600
    monitoring_simulation: bool = False
    lhm_enabled: bool = True
    lhm_host: str = "127.0.0.1"
    lhm_port: int = 8085
    lhm_timeout_s: float = 2.0
    smart_enabled: bool = False


def _coerce_port(value: object, fallback: int) -> int:
    """Validate configured ports and keep a safe local-only default."""
    try:
        port = int(value)
    except (TypeError, ValueError):
        return fallback
    return port if 1 <= port <= 65535 else fallback


def _coerce_bool(value: object, fallback: bool) -> bool:
    return value if isinstance(value, bool) else fallback


def _coerce_positive_float(value: object, fallback: float, minimum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if math.isfinite(number) and number >= minimum else fallback


def load_settings() -> Settings:
    """Load bundled defaults, then apply optional user-specific overrides."""
    defaults = Settings()
    raw: dict[str, object] = {}
    try:
        with CONFIG_FILE.open("rb") as f:
            bundled = tomllib.load(f)
            if isinstance(bundled, dict):
                raw.update(bundled)
    except (OSError, tomllib.TOMLDecodeError):
        pass

    user_config = user_data_dir() / "config.toml"
    try:
        with user_config.open("rb") as f:
            overrides = tomllib.load(f)
            if isinstance(overrides, dict):
                for section, values in overrides.items():
                    if isinstance(values, dict) and isinstance(raw.get(section), dict):
                        raw[section] = {**raw[section], **values}
                    else:
                        raw[section] = values
    except FileNotFoundError:
        pass
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logging.getLogger(__name__).warning(
            "User config override could not be loaded from %s: %s",
            user_config,
            exc,
        )

    server = raw.get("server", {})
    logging_cfg = raw.get("logging", {})
    monitoring = raw.get("monitoring", {})
    lhm = raw.get("lhm", {})
    smart = raw.get("smart", {})
    if not isinstance(server, dict):
        server = {}
    if not isinstance(logging_cfg, dict):
        logging_cfg = {}
    if not isinstance(monitoring, dict):
        monitoring = {}
    if not isinstance(lhm, dict):
        lhm = {}
    if not isinstance(smart, dict):
        smart = {}
    raw_intervals = monitoring.get("intervals", {})
    if not isinstance(raw_intervals, dict):
        raw_intervals = {}
    intervals: dict[str, float] = {}
    for category, fallback in _DEFAULT_INTERVALS.items():
        intervals[category] = _coerce_positive_float(
            raw_intervals.get(category, fallback),
            fallback,
            0.5,
        )

    host = str(server.get("host", defaults.host))
    if host != "127.0.0.1":
        # Security rule: never bind publicly in this version.
        host = "127.0.0.1"

    log_level = str(logging_cfg.get("level", defaults.log_level)).upper()
    if not hasattr(logging, log_level):
        log_level = defaults.log_level

    log_file = str(logging_cfg.get("file", defaults.log_file))
    lhm_host = str(lhm.get("host", defaults.lhm_host)).strip().casefold()
    if lhm_host not in {"127.0.0.1", "localhost"}:
        logging.getLogger(__name__).warning(
            "Ignoring non-loopback LibreHardwareMonitor host %r",
            lhm_host,
        )
        lhm_host = defaults.lhm_host
    return Settings(
        host=host,
        port=_coerce_port(server.get("port", defaults.port), defaults.port),
        log_level=log_level,
        log_file=log_file or defaults.log_file,
        monitoring_intervals=intervals,
        monitoring_history_size=min(
            3600,
            max(1, _coerce_port(monitoring.get("history_size", 3600), 3600)),
        ),
        monitoring_simulation=_coerce_bool(
            monitoring.get("simulation"),
            defaults.monitoring_simulation,
        ),
        lhm_enabled=_coerce_bool(lhm.get("enabled"), defaults.lhm_enabled),
        lhm_host=lhm_host,
        lhm_port=_coerce_port(lhm.get("port", defaults.lhm_port), defaults.lhm_port),
        lhm_timeout_s=_coerce_positive_float(
            lhm.get("timeout_s", defaults.lhm_timeout_s),
            defaults.lhm_timeout_s,
            0.1,
        ),
        smart_enabled=_coerce_bool(smart.get("enabled"), defaults.smart_enabled),
    )
