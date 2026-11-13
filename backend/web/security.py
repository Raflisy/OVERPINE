"""Loopback host, origin, token, and browser security-header checks."""
from __future__ import annotations

import hmac
from collections.abc import Mapping

from backend.web.router import HTTPError


def trusted_hosts(port: int) -> frozenset[str]:
    """Return allowed Host header values for the active local port."""
    return frozenset(
        {
            "127.0.0.1",
            "localhost",
            f"127.0.0.1:{port}",
            f"localhost:{port}",
        }
    )


def trusted_origins(port: int) -> frozenset[str]:
    """Return same-origin browser values for the active local port."""
    return frozenset(
        {
            f"http://127.0.0.1:{port}",
            f"http://localhost:{port}",
        }
    )


def validate_host(host: str | None, allowed_hosts: frozenset[str]) -> None:
    """Reject requests without an allowed local Host header."""
    if host is None or host.casefold() not in {
        item.casefold() for item in allowed_hosts
    }:
        raise HTTPError(400, "Host header is not allowed")


def validate_origin(
    method: str,
    headers: Mapping[str, str],
    allowed_origins: frozenset[str],
) -> None:
    """Reject cross-origin state-changing requests without enabling CORS."""
    if method.upper() not in {"POST", "PUT", "DELETE"}:
        return
    origin = _header(headers, "Origin")
    if origin is not None and origin not in allowed_origins:
        raise HTTPError(403, "Origin is not allowed")


def require_session_token(
    method: str,
    headers: Mapping[str, str],
    expected_token: str,
) -> None:
    """Require a constant-time checked per-session token for state changes."""
    if method.upper() not in {"POST", "PUT", "DELETE"}:
        return
    supplied = _header(headers, "X-Overpine-Token")
    if supplied is None or not hmac.compare_digest(supplied, expected_token):
        raise HTTPError(401, "Valid X-Overpine-Token required")


def _header(headers: Mapping[str, str], name: str) -> str | None:
    """Read a header without assuming its key casing."""
    folded = name.casefold()
    return next(
        (value for key, value in headers.items() if key.casefold() == folded),
        None,
    )
