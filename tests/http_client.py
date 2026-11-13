"""In-process HTTP test client backed by http.client and an ephemeral port."""
from __future__ import annotations

import http.client
import json
from typing import Any

from backend.main import Application
from backend.web.server import LocalHTTPServer


class HTTPTestResponse:
    """Expose the response properties used by the API tests."""

    def __init__(
        self,
        status_code: int,
        headers: http.client.HTTPMessage,
        body: bytes,
    ) -> None:
        self.status_code = status_code
        self.headers = headers
        self._body = body

    @property
    def text(self) -> str:
        """Decode a response body as UTF-8 text."""
        return self._body.decode("utf-8")

    def json(self) -> Any:
        """Decode a JSON response body."""
        return json.loads(self._body.decode("utf-8"))


class LocalHTTPTestClient:
    """Run one application on loopback and issue real HTTP requests."""

    def __init__(self, application: Application) -> None:
        self.server = LocalHTTPServer(application, host="127.0.0.1", port=0)
        self.port = int(self.server.server_address[1])
        self._thread = self.server.start_in_thread(timeout=5)

    def close(self) -> None:
        """Stop the server and its application lifecycle hooks."""
        self.server.stop(timeout=5)

    def __enter__(self) -> LocalHTTPTestClient:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: object | None = None,
    ) -> HTTPTestResponse:
        """Send a request over a new loopback HTTP connection."""
        request_headers = dict(headers or {})
        body = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.port,
            timeout=10,
        )
        try:
            connection.request(
                method,
                path,
                body=body,
                headers=request_headers,
            )
            response = connection.getresponse()
            return HTTPTestResponse(
                response.status,
                response.headers,
                response.read(),
            )
        finally:
            connection.close()

    def get(
        self,
        path: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> HTTPTestResponse:
        """Send a GET request."""
        return self.request("GET", path, headers=headers)

    def post(
        self,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: object | None = None,
    ) -> HTTPTestResponse:
        """Send a POST request."""
        return self.request(
            "POST",
            path,
            headers=headers,
            json_body=json_body,
        )
