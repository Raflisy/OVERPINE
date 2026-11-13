"""Small request validators for the standard-library HTTP API."""
from __future__ import annotations

from dataclasses import dataclass

from backend.monitoring.models import SensorState
from backend.web.router import HTTPError, Request

_STRESS_TARGETS = frozenset({"cpu", "memory", "disk", "network", "gpu", "all"})


@dataclass(frozen=True, slots=True)
class StressStartPayload:
    """Contain validated options for one bounded stress run."""

    targets: list[str]
    duration_s: int
    intensity_percent: int


def _request_object(body: object) -> dict[str, object]:
    """Require a JSON object before reading named fields."""
    if not isinstance(body, dict):
        raise HTTPError(422, "Request body must be a JSON object")
    return body


def _required_integer(
    payload: dict[str, object],
    name: str,
    minimum: int,
    maximum: int,
) -> int:
    """Validate a required integer field and its inclusive bounds."""
    value = payload.get(name)
    if type(value) is not int:
        raise HTTPError(422, f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise HTTPError(422, f"{name} must be between {minimum} and {maximum}")
    return value


def validate_stress_start(body: object) -> StressStartPayload:
    """Validate stress targets, duration, and intensity."""
    payload = _request_object(body)
    targets = payload.get("targets")
    if not isinstance(targets, list) or any(
        not isinstance(target, str) or target not in _STRESS_TARGETS
        for target in targets
    ):
        raise HTTPError(422, "targets must be a list of supported stress targets")
    return StressStartPayload(
        targets=list(targets),
        duration_s=_required_integer(payload, "duration_s", 5, 300),
        intensity_percent=_required_integer(
            payload,
            "intensity_percent",
            10,
            100,
        ),
    )


def query_value(request: Request, name: str) -> str | None:
    """Return one query parameter value, rejecting ambiguous duplicates."""
    values = request.query.get(name, [])
    if len(values) > 1:
        raise HTTPError(422, f"{name} must be provided at most once")
    return values[0] if values else None


def query_integer(
    request: Request,
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    """Parse and bound one integer query parameter."""
    raw_value = query_value(request, name)
    if raw_value is None:
        return default
    if not raw_value.isascii() or not raw_value.isdecimal():
        raise HTTPError(422, f"{name} must be an integer")
    value = int(raw_value)
    if not minimum <= value <= maximum:
        raise HTTPError(422, f"{name} must be between {minimum} and {maximum}")
    return value


def query_sensor_state(request: Request) -> SensorState | None:
    """Parse an optional SensorState query value."""
    raw_value = query_value(request, "state")
    if raw_value is None:
        return None
    try:
        return SensorState(raw_value)
    except ValueError as exc:
        allowed = ", ".join(state.value for state in SensorState)
        raise HTTPError(
            422,
            f"state must be one of: {allowed}",
        ) from exc
