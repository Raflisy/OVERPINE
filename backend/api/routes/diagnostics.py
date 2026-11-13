"""Read-only log viewer and diagnostics summary for the local dashboard."""
from __future__ import annotations

import platform
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.core.config import APP_NAME, APP_VERSION
from backend.core.paths import user_data_dir
from backend.monitoring.models import SensorState
from backend.storage.history import HistoryStorageError, is_detail_sensor
from backend.web.router import HTTPError, Request

_MAX_TAIL_BYTES = 512 * 1024
_DEFAULT_LIMIT = 200
_MAX_LIMIT = 500
_MAX_QUERY_LENGTH = 100
_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
_LINE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) \| (\w+)\s*\| ([^|]*?) \| (.*)$"
)


def _one_query_value(request: Request, key: str) -> str | None:
    """Return one query parameter and reject ambiguous duplicates."""
    values = request.query.get(key)
    if values is None:
        return None
    if len(values) != 1:
        raise HTTPError(422, f"Query parameter {key} must appear once")
    return values[0]


def _log_path(request: Request) -> Path | None:
    """Return the configured log file, only if it is inside the app data root."""
    root = user_data_dir().resolve()
    configured = Path(request.app.settings.log_file)
    path = (configured if configured.is_absolute() else root / configured).resolve()
    return path if path.is_relative_to(root) else None


def _read_entries(path: Path) -> list[dict[str, str]]:
    """Read the end of the log file and group continuation lines by entry."""
    size = path.stat().st_size
    with path.open("rb") as handle:
        handle.seek(max(0, size - _MAX_TAIL_BYTES))
        raw = handle.read(_MAX_TAIL_BYTES)
    lines = raw.decode("utf-8", errors="replace").splitlines()
    if size > _MAX_TAIL_BYTES and lines:
        lines = lines[1:]  # the first line may have been cut in half
    entries: list[dict[str, str]] = []
    for line in lines:
        match = _LINE.match(line)
        if match:
            entries.append(
                {
                    "time": match.group(1),
                    "level": match.group(2).upper(),
                    "logger": match.group(3),
                    "message": match.group(4),
                }
            )
        elif entries:
            entries[-1]["message"] += "\n" + line
    return entries


def get_logs(request: Request) -> dict[str, Any]:
    """Return the newest log entries filtered by level and search text."""
    level = (_one_query_value(request, "level") or "DEBUG").upper()
    if level not in _LEVELS:
        raise HTTPError(422, "level must be DEBUG, INFO, WARNING, ERROR or CRITICAL")
    query = (_one_query_value(request, "q") or "").strip()
    if len(query) > _MAX_QUERY_LENGTH:
        raise HTTPError(422, f"q must be at most {_MAX_QUERY_LENGTH} characters")
    raw_limit = _one_query_value(request, "limit")
    try:
        limit = int(raw_limit) if raw_limit is not None else _DEFAULT_LIMIT
    except ValueError as error:
        raise HTTPError(422, "limit must be a whole number") from error
    if not 1 <= limit <= _MAX_LIMIT:
        raise HTTPError(422, f"limit must be between 1 and {_MAX_LIMIT}")

    empty: dict[str, Any] = {
        "available": False,
        "message": None,
        "log_file": None,
        "entries": [],
        "total_matching": 0,
        "limit": limit,
    }
    path = _log_path(request)
    if path is None:
        return {**empty, "message": "The configured log file is outside the app data folder."}
    empty["log_file"] = str(path)
    try:
        entries = _read_entries(path)
    except FileNotFoundError:
        return {**empty, "message": "The log file does not exist yet."}
    except OSError as error:
        return {**empty, "message": f"Could not read the log file: {error}"}

    minimum = _LEVELS[level]
    needle = query.casefold()
    matching = [
        entry
        for entry in entries
        if _LEVELS.get(entry["level"], 0) >= minimum
        and (
            not needle
            or needle in entry["message"].casefold()
            or needle in entry["logger"].casefold()
        )
    ]
    return {
        **empty,
        "available": True,
        "entries": matching[-limit:],
        "total_matching": len(matching),
    }


def get_diagnostics(request: Request) -> dict[str, Any]:
    """Summarise sensor states, providers and history health without writing."""
    scheduler = request.app.monitoring_scheduler
    status = scheduler.status()
    sensors = scheduler.registry.list()

    counts = {state.value: 0 for state in SensorState}
    problems: list[dict[str, str | None]] = []
    detail_sensor_count = 0
    for sensor in sensors:
        if is_detail_sensor(sensor.id):
            detail_sensor_count += 1
            continue
        counts[sensor.state.value] += 1
        if sensor.state != SensorState.AVAILABLE:
            problems.append(
                {
                    "id": sensor.id,
                    "name": sensor.name,
                    "category": sensor.category,
                    "state": sensor.state.value,
                    "message": sensor.error_message,
                }
            )
    problems.sort(key=lambda item: (str(item["state"]), str(item["name"])))

    providers = [
        {
            "name": row.get("name"),
            "state": row.get("state"),
            "last_error": row.get("last_error"),
            "backoff_s": row.get("backoff_s"),
            "sensor_count": row.get("sensor_count"),
        }
        for row in status.get("providers", [])
    ]

    try:
        history = request.app.history_store.status()
    except HistoryStorageError as error:
        history = {"enabled": None, "available": False, "warning": str(error)}

    path = _log_path(request)
    log_size: int | None = None
    if path is not None:
        try:
            log_size = path.stat().st_size
        except OSError:
            log_size = None

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "app": f"{APP_NAME} {APP_VERSION}",
        "python_version": platform.python_version(),
        "system": f"{platform.system()} {platform.release()} ({platform.version()})",
        "monitoring_state": status.get("state"),
        "session_duration_s": status.get("session_duration_s"),
        "sensor_counts": counts,
        "detail_sensor_count": detail_sensor_count,
        "problems": problems,
        "providers": providers,
        "history": history,
        "log_file_size_bytes": log_size,
    }
