"""Central configuration loader for OVERPINE.

Reads config/default_config.toml. If the file is missing or broken, safe
built-in defaults are used so the app can still start.
"""
from __future__ import annotations

import logging
import math
import tomllib
from dataclasses import dataclass, field
from typing import cast

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
APP_VERSION = "0.5.0"

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

DEFAULT_HISTORY_SENSOR_IDS = (
    "cpu.total",
    "cpu.frequency.current",
    "cpu.frequency.max",
    "cpu.throttling.estimated",
    "memory.used",
    "storage.disk.read",
    "storage.disk.write",
    "network.receive",
    "network.send",
    "battery.charge",
    "battery.power_plugged",
    "battery.seconds_left",
    "battery.voltage",
    "gpu.3d.utilization",
    "thermal.wmi.acpi-zone.0",
    "storage.temperature.disk0.sensor0",
)


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
    allow_external_programs: bool = False
    history_enabled: bool = True
    history_retention_days: int = 7
    history_max_mb: int = 100
    history_record_sensors: tuple[str, ...] = DEFAULT_HISTORY_SENSOR_IDS
    refresh_interval_seconds: int = 3
    theme: str = "dark"
    online_device_fact_lookup: bool = False
    about_credits: str = ""
    about_course_university: str = ""


def _coerce_port(value: object, fallback: int) -> int:
    """Validate configured ports and keep a safe local-only default."""
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        port = value
    elif isinstance(value, str) and value.isascii() and value.isdecimal():
        port = int(value)
    else:
        return fallback
    return port if 1 <= port <= 65535 else fallback


def _coerce_positive_float(value: object, fallback: float, minimum: float) -> float:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return fallback
    else:
        return fallback
    return number if math.isfinite(number) and number >= minimum else fallback


def _coerce_positive_int(value: object, fallback: int, maximum: int) -> int:
    """Validate a positive integer setting with a safe upper bound."""
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.isascii() and value.isdecimal():
        number = int(value)
    else:
        return fallback
    return number if 1 <= number <= maximum else fallback


def _coerce_sensor_ids(value: object, fallback: tuple[str, ...]) -> tuple[str, ...]:
    """Accept configured sensor IDs while discarding malformed entries."""
    if not isinstance(value, list):
        return fallback
    items = cast(list[object], value)
    return tuple(
        dict.fromkeys(
            item.strip()
            for item in items
            if isinstance(item, str) and item.strip() and len(item.strip()) <= 256
        )
    )


def _config_mapping(value: object) -> dict[str, object]:
    """Return a string-keyed view of one TOML table."""
    if not isinstance(value, dict):
        return {}
    result: dict[str, object] = {}
    mapping = cast(dict[object, object], value)
    for key, item in mapping.items():
        if isinstance(key, str):
            result[key] = item
    return result


def load_settings() -> Settings:
    """Load bundled defaults, then apply optional user-specific overrides."""
    defaults = Settings()
    raw: dict[str, object] = {}
    try:
        with CONFIG_FILE.open("rb") as f:
            raw.update(_config_mapping(tomllib.load(f)))
    except (OSError, tomllib.TOMLDecodeError):
        pass

    user_config = user_data_dir() / "config.toml"
    try:
        with user_config.open("rb") as f:
            overrides = _config_mapping(tomllib.load(f))
            for section, values in overrides.items():
                override = _config_mapping(values)
                existing = raw.get(section)
                if isinstance(values, dict) and isinstance(existing, dict):
                    raw[section] = {
                        **_config_mapping(cast(object, existing)),
                        **override,
                    }
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

    server = _config_mapping(raw.get("server"))
    logging_cfg = _config_mapping(raw.get("logging"))
    monitoring = _config_mapping(raw.get("monitoring"))
    history = _config_mapping(raw.get("history"))
    windows_tools = _config_mapping(raw.get("windows_tools"))
    interface = _config_mapping(raw.get("interface"))
    privacy = _config_mapping(raw.get("privacy"))
    about = _config_mapping(raw.get("about"))
    raw_intervals = _config_mapping(monitoring.get("intervals"))
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
    allow_external_programs = windows_tools.get(
        "allow_external_programs",
        defaults.allow_external_programs,
    )
    if not isinstance(allow_external_programs, bool):
        allow_external_programs = defaults.allow_external_programs
    history_enabled = history.get("enabled", defaults.history_enabled)
    if not isinstance(history_enabled, bool):
        history_enabled = defaults.history_enabled
    online_device_fact_lookup = privacy.get(
        "online_device_fact_lookup",
        defaults.online_device_fact_lookup,
    )
    if not isinstance(online_device_fact_lookup, bool):
        online_device_fact_lookup = defaults.online_device_fact_lookup
    refresh_interval_seconds = interface.get(
        "refresh_interval_seconds",
        defaults.refresh_interval_seconds,
    )
    if (
        isinstance(refresh_interval_seconds, bool)
        or not isinstance(refresh_interval_seconds, int)
        or not 1 <= refresh_interval_seconds <= 60
    ):
        refresh_interval_seconds = defaults.refresh_interval_seconds
    theme = interface.get("theme", defaults.theme)
    if not isinstance(theme, str) or theme not in {"dark", "light"}:
        theme = defaults.theme
    credits = about.get("credits", defaults.about_credits)
    course_university = about.get(
        "course_university",
        defaults.about_course_university,
    )
    if not isinstance(credits, str):
        credits = defaults.about_credits
    if not isinstance(course_university, str):
        course_university = defaults.about_course_university
    return Settings(
        host=host,
        port=_coerce_port(server.get("port", defaults.port), defaults.port),
        log_level=log_level,
        log_file=log_file or defaults.log_file,
        allow_external_programs=allow_external_programs,
        monitoring_intervals=intervals,
        monitoring_history_size=min(
            3600,
            max(1, _coerce_port(monitoring.get("history_size", 3600), 3600)),
        ),
        history_enabled=history_enabled,
        history_retention_days=_coerce_positive_int(
            history.get("retention_days", defaults.history_retention_days),
            defaults.history_retention_days,
            3650,
        ),
        history_max_mb=_coerce_positive_int(
            history.get("max_mb", defaults.history_max_mb),
            defaults.history_max_mb,
            10240,
        ),
        history_record_sensors=_coerce_sensor_ids(
            history.get("record_sensors", list(defaults.history_record_sensors)),
            defaults.history_record_sensors,
        ),
        refresh_interval_seconds=refresh_interval_seconds,
        theme=theme,
        online_device_fact_lookup=online_device_fact_lookup,
        about_credits=credits.strip()[:500],
        about_course_university=course_university.strip()[:500],
    )
