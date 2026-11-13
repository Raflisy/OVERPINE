"""Validated, app-data-local settings that can be changed from the UI."""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from dataclasses import replace
from pathlib import Path
from typing import TypedDict, cast

from backend.core.config import Settings

_LOG = logging.getLogger(__name__)
_FILE_VERSION = 1
_SENSOR_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
SETTING_KEYS = frozenset({
    "history_enabled",
    "retention_days",
    "max_mb",
    "recorded_sensors",
    "refresh_interval_seconds",
    "theme",
    "online_device_fact_lookup",
})
RESTART_REQUIRED_KEYS = (
    "history_enabled",
    "retention_days",
    "max_mb",
    "recorded_sensors",
)


class UserSettings(TypedDict):
    history_enabled: bool
    retention_days: int
    max_mb: int
    recorded_sensors: list[str]
    refresh_interval_seconds: int
    theme: str
    online_device_fact_lookup: bool


class SettingsStorageError(RuntimeError):
    """Report a settings file that could not be read or written."""


def settings_values(settings: Settings) -> UserSettings:
    """Convert supported settings to the stable API and file representation."""
    return {
        "history_enabled": settings.history_enabled,
        "retention_days": settings.history_retention_days,
        "max_mb": settings.history_max_mb,
        "recorded_sensors": list(settings.history_record_sensors),
        "refresh_interval_seconds": settings.refresh_interval_seconds,
        "theme": settings.theme,
        "online_device_fact_lookup": settings.online_device_fact_lookup,
    }


def validate_settings_payload(value: object) -> UserSettings:
    """Validate a complete settings payload and return a normalized copy."""
    if not isinstance(value, dict):
        raise ValueError("Request body must be a JSON object")
    raw_payload = cast(dict[object, object], value)
    if any(not isinstance(key, str) for key in raw_payload):
        raise ValueError("Setting names must be strings")
    payload = cast(dict[str, object], raw_payload)
    unknown = set(payload) - SETTING_KEYS
    missing = SETTING_KEYS - set(payload)
    if unknown:
        raise ValueError(f"Unknown settings: {', '.join(sorted(unknown))}")
    if missing:
        raise ValueError(f"Missing settings: {', '.join(sorted(missing))}")

    history_enabled = payload["history_enabled"]
    if type(history_enabled) is not bool:
        raise ValueError("history_enabled must be true or false")

    retention_days = payload["retention_days"]
    if type(retention_days) is not int or not 1 <= retention_days <= 3650:
        raise ValueError("retention_days must be an integer from 1 to 3650")

    max_mb = payload["max_mb"]
    if type(max_mb) is not int or not 1 <= max_mb <= 10240:
        raise ValueError("max_mb must be an integer from 1 to 10240")

    raw_recorded_sensors = payload["recorded_sensors"]
    if not isinstance(raw_recorded_sensors, list):
        raise ValueError("recorded_sensors must be a list of sensor IDs")
    recorded_sensors = cast(list[object], raw_recorded_sensors)
    if len(recorded_sensors) > 64:
        raise ValueError("recorded_sensors may contain at most 64 sensor IDs")
    sensor_ids: list[str] = []
    for sensor_id in recorded_sensors:
        if not isinstance(sensor_id, str) or not _SENSOR_ID.fullmatch(sensor_id):
            raise ValueError("recorded_sensors contains an invalid sensor ID")
        sensor_ids.append(sensor_id)
    if len(set(sensor_ids)) != len(sensor_ids):
        raise ValueError("recorded_sensors must not contain duplicates")

    refresh_interval = payload["refresh_interval_seconds"]
    if (
        type(refresh_interval) is not int
        or not 1 <= refresh_interval <= 60
    ):
        raise ValueError("refresh_interval_seconds must be an integer from 1 to 60")

    theme = payload["theme"]
    if not isinstance(theme, str) or theme not in {"dark", "light"}:
        raise ValueError("theme must be either dark or light")

    online_lookup = payload["online_device_fact_lookup"]
    if type(online_lookup) is not bool:
        raise ValueError("online_device_fact_lookup must be true or false")

    return {
        "history_enabled": history_enabled,
        "retention_days": retention_days,
        "max_mb": max_mb,
        "recorded_sensors": sensor_ids,
        "refresh_interval_seconds": refresh_interval,
        "theme": theme,
        "online_device_fact_lookup": online_lookup,
    }


class SettingsStore:
    """Persist validated UI settings under the application data directory."""

    def __init__(self, path: Path, base_settings: Settings) -> None:
        self.path = Path(path)
        self.base_settings = base_settings
        self._lock = threading.RLock()
        self._values: UserSettings = settings_values(base_settings)
        self._load()

    def values(self) -> UserSettings:
        """Return a copy so callers cannot mutate persisted settings in memory."""
        with self._lock:
            return {
                **self._values,
                "recorded_sensors": list(self._values["recorded_sensors"]),
            }

    def apply(self) -> Settings:
        """Apply stored values to the current config-derived settings."""
        with self._lock:
            values = self._values
            return replace(
                self.base_settings,
                history_enabled=values["history_enabled"],
                history_retention_days=values["retention_days"],
                history_max_mb=values["max_mb"],
                history_record_sensors=tuple(values["recorded_sensors"]),
                refresh_interval_seconds=values["refresh_interval_seconds"],
                theme=values["theme"],
                online_device_fact_lookup=values["online_device_fact_lookup"],
            )

    def save(self, payload: object) -> UserSettings:
        """Validate and atomically persist all user-adjustable settings."""
        values = validate_settings_payload(payload)
        data: dict[str, object] = {
            "version": _FILE_VERSION,
            "settings": values,
        }
        temporary_path: Path | None = None
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=self.path.parent,
                    prefix=f".{self.path.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as stream:
                    temporary_path = Path(stream.name)
                    json.dump(data, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary_path, self.path)
            except OSError as error:
                raise SettingsStorageError(
                    f"Could not save settings to {self.path}: {error}"
                ) from error
            finally:
                if temporary_path is not None and temporary_path.exists():
                    try:
                        temporary_path.unlink()
                    except FileNotFoundError:
                        pass
                    except OSError:
                        _LOG.exception(
                            "Could not remove temporary settings file %s",
                            temporary_path,
                        )
            self._values = values
            return self.values()

    def reset(self) -> UserSettings:
        """Remove the override file and return to config-derived defaults."""
        with self._lock:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            except OSError as error:
                raise SettingsStorageError(
                    f"Could not reset settings at {self.path}: {error}"
                ) from error
            self._values = settings_values(self.base_settings)
            return self.values()

    def _load(self) -> None:
        """Load a previously saved settings file, rejecting malformed data."""
        try:
            raw_value: object = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SettingsStorageError(
                f"Could not read settings from {self.path}: {error}"
            ) from error
        if not isinstance(raw_value, dict):
            raise SettingsStorageError(
                f"Settings file at {self.path} has an unsupported format"
            )
        raw = cast(dict[str, object], raw_value)
        if (
            set(raw) != {"version", "settings"}
            or type(raw.get("version")) is not int
            or raw["version"] != _FILE_VERSION
        ):
            raise SettingsStorageError(
                f"Settings file at {self.path} has an unsupported format"
            )
        try:
            self._values = validate_settings_payload(raw["settings"])
        except ValueError as error:
            raise SettingsStorageError(
                f"Settings file at {self.path} is invalid: {error}"
            ) from error
