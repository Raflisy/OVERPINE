"""Small standard-library router and HTTP request/response helpers."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, BinaryIO, Callable, Mapping
from urllib.parse import parse_qs, unquote, urlsplit

MAX_BODY_BYTES = 64 * 1024


class HTTPError(Exception):
    """Represent a client-facing HTTP error with a stable JSON response."""

    def __init__(
        self,
        status: int,
        message: str,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = _ERROR_CODES.get(status, "http_error")
        self.message = message
        self.headers = dict(headers or {})

    def as_dict(self) -> dict[str, dict[str, str]]:
        """Return the public error response body."""
        return {
            "error": {
                "code": self.code,
                "message": self.message,
            }
        }


@dataclass(slots=True)
class Request:
    """Expose parsed HTTP request data to a plain route handler."""

    method: str
    path: str
    query: dict[str, list[str]]
    headers: Mapping[str, str]
    body: Any
    client_address: tuple[str, int]
    app: Any
    path_params: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Response:
    """Contain an HTTP response body and its headers."""

    status: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)


RouteHandler = Callable[[Request], Any]

_ERROR_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
    500: "internal_server_error",
}


@dataclass(frozen=True, slots=True)
class _Route:
    method: str
    template: str
    pattern: re.Pattern[str]
    handler: RouteHandler


class Router:
    """Match exact route templates and strictly constrained path parameters."""

    def __init__(self) -> None:
        self._routes: list[_Route] = []

    def add(
        self,
        method: str,
        template: str,
        handler: RouteHandler,
        parameter_patterns: Mapping[str, str] | None = None,
    ) -> None:
        """Register a route, compiling each named parameter's expression."""
        if not template.startswith("/") or "?" in template or "#" in template:
            raise ValueError("Route templates must be absolute URL paths")
        patterns = dict(parameter_patterns or {})
        names: set[str] = set()
        parts: list[str] = []
        cursor = 0
        for match in re.finditer(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", template):
            name = match.group(1)
            if name in names:
                raise ValueError(f"Duplicate route parameter: {name}")
            names.add(name)
            parts.append(re.escape(template[cursor:match.start()]))
            expression = patterns.pop(name, r"[^/]+")
            parts.append(f"(?P<{name}>{expression})")
            cursor = match.end()
        parts.append(re.escape(template[cursor:]))
        if patterns:
            raise ValueError(
                f"Patterns do not match route parameters: {', '.join(sorted(patterns))}"
            )
        try:
            compiled = re.compile("^" + "".join(parts) + "$")
        except re.error as exc:
            raise ValueError(f"Invalid route parameter expression: {exc}") from exc
        self._routes.append(
            _Route(method.upper(), template, compiled, handler)
        )

    def dispatch(self, request: Request) -> Response | None:
        """Dispatch a matching route, or return None for static-file handling."""
        path_matches: list[tuple[_Route, dict[str, str]]] = []
        for route in self._routes:
            match = route.pattern.fullmatch(request.path)
            if match is not None:
                path_matches.append(
                    (
                        route,
                        match.groupdict(),
                    )
                )
        if not path_matches:
            if request.path == "/api" or request.path.startswith("/api/"):
                raise HTTPError(404, "API route not found")
            return None

        method = "GET" if request.method == "HEAD" else request.method
        selected = next(
            (route for route, _params in path_matches if route.method == method),
            None,
        )
        if selected is None:
            allowed = {route.method for route, _params in path_matches}
            if "GET" in allowed:
                allowed.add("HEAD")
            raise HTTPError(
                405,
                "Method not allowed for this path",
                {"Allow": ", ".join(sorted(allowed))},
            )

        params = next(
            params
            for route, params in path_matches
            if route is selected
        )
        request.path_params = params
        result = selected.handler(request)
        if isinstance(result, Response):
            return result
        return json_response(result)


def parse_request_target(target: str) -> tuple[str, dict[str, list[str]]]:
    """Split a request target into a decoded path and repeated query values."""
    split = urlsplit(target)
    if split.scheme or split.netloc or not split.path.startswith("/"):
        raise HTTPError(400, "Invalid request target")
    path = unquote(split.path)
    if "\x00" in path:
        raise HTTPError(400, "Invalid request path")
    return path, parse_qs(split.query, keep_blank_values=True)


def read_json_body(headers: Mapping[str, str], stream: BinaryIO) -> Any:
    """Read one bounded UTF-8 JSON request body."""
    transfer_encoding = _header(headers, "Transfer-Encoding")
    if transfer_encoding is not None:
        raise HTTPError(400, "Transfer-Encoding is not supported")
    lengths = _header_values(headers, "Content-Length")
    if len(lengths) > 1:
        raise HTTPError(400, "Multiple Content-Length headers are not allowed")
    if not lengths:
        return None
    raw_length = lengths[0]
    if not raw_length.isascii() or not raw_length.isdecimal():
        raise HTTPError(400, "Content-Length must be a non-negative integer")
    if len(raw_length) > 20:
        raise HTTPError(400, "Content-Length is too long")
    length = int(raw_length)
    if length > MAX_BODY_BYTES:
        raise HTTPError(413, "Request body exceeds the 64 KiB limit")
    if length == 0:
        return None
    payload = stream.read(length)
    if len(payload) != length:
        raise HTTPError(400, "Request body is shorter than Content-Length")
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPError(400, "Request body must be valid UTF-8 JSON") from exc


def json_response(
    body: Any,
    status: int = 200,
    headers: Mapping[str, str] | None = None,
) -> Response:
    """Serialize a response object as compact UTF-8 JSON."""
    try:
        payload = json.dumps(
            body,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise HTTPError(500, "Response could not be serialized") from exc
    response_headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
    }
    response_headers.update(headers or {})
    return Response(status, payload, response_headers)


def _header(headers: Mapping[str, str], name: str) -> str | None:
    """Return a case-insensitive header value when present."""
    values = _header_values(headers, name)
    return values[0] if values else None


def _header_values(headers: Mapping[str, str], name: str) -> list[str]:
    """Return all case-insensitive values for a header."""
    get_all = getattr(headers, "get_all", None)
    if callable(get_all):
        values = get_all(name)
        if values:
            return [str(value) for value in values]
    folded = name.casefold()
    return [
        str(value)
        for key, value in headers.items()
        if key.casefold() == folded
    ]
