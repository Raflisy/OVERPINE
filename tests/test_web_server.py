"""Standard-library tests for the Stage 1 HTTP server."""
from __future__ import annotations

import http.client
import json
import unittest
from unittest.mock import patch

from backend.monitoring.scheduler import MonitoringScheduler
from backend.winapi.self_process import ProcessMetrics
from backend.web.router import Request
from backend.web.server import LocalHTTPServer
from tests.test_support import create_isolated_app


class LocalHTTPServerTests(unittest.TestCase):
    """Exercise routing and security over a real local HTTP connection."""

    def setUp(self) -> None:
        """Start the application on an ephemeral loopback port."""
        self.application = create_isolated_app(self, configure_logging=False)
        self.application.monitoring_scheduler = MonitoringScheduler(providers=[])
        self.application.router.add(
            "GET",
            "/api/v1/items/{item_id}",
            self._read_item,
            {"item_id": r"[A-Za-z0-9_-]{1,40}"},
        )
        self.application.router.add(
            "POST",
            "/api/v1/test-body",
            self._read_body,
        )
        self.application.router.add(
            "GET",
            "/api/v1/test-error",
            self._raise_error,
        )
        self.server = LocalHTTPServer(self.application, port=0)
        self.port = int(self.server.server_address[1])
        self.server_thread = self.server.start_in_thread(timeout=5)
        self.assertTrue(self.server.daemon_threads)

    def tearDown(self) -> None:
        """Stop the HTTP loop and close its listening socket."""
        self.server.stop(timeout=5)
        self.assertFalse(self.server_thread.is_alive())

    def test_health_route_preserves_json_shape_with_unavailable_metrics(self) -> None:
        """The health route returns its public schema when metrics are unavailable."""
        with (
            patch(
                "backend.api.routes.health.current_process_metrics",
                return_value=ProcessMetrics(None, None),
            ),
            patch(
                "backend.api.routes.health.system_uptime_seconds",
                return_value=None,
            ),
        ):
            status, headers, body = self._request("GET", "/api/v1/health")
        payload = json.loads(body)

        self.assertEqual(status, 200)
        self.assertEqual(
            set(payload),
            {"status", "app", "version", "is_admin", "uptime_seconds", "self"},
        )
        self.assertEqual(set(payload["self"]), {"cpu_percent", "ram_mb"})
        self.assertIsNone(payload["self"]["cpu_percent"])
        self.assertIsNone(payload["self"]["ram_mb"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(
            headers["Referrer-Policy"],
            "strict-origin-when-cross-origin",
        )
        self.assertEqual(
            headers["Content-Security-Policy"],
            "default-src 'self'; base-uri 'self'; connect-src 'self'; "
            "font-src 'self'; form-action 'self'; frame-ancestors 'none'; "
            "img-src 'self'; object-src 'none'; script-src 'self'; "
            "style-src 'self'",
        )
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertNotEqual(
            self.application.session_token,
            create_isolated_app(self, configure_logging=False).session_token,
        )

    def test_router_matches_parameters_and_returns_json_404_and_405(self) -> None:
        """Dispatch parameterized routes and report normalized method/path errors."""
        status, _headers, body = self._request("GET", "/api/v1/items/disk_1")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"item_id": "disk_1"})

        status, _headers, _body = self._request(
            "GET",
            "/api/v1/items/invalid.dot",
        )
        self.assertEqual(status, 404)

        status, _headers, body = self._request("GET", "/api/v1/missing")
        self.assertEqual(status, 404)
        self.assertEqual(
            json.loads(body)["error"],
            {"code": "not_found", "message": "API route not found"},
        )

        status, headers, body = self._request(
            "POST",
            "/api/v1/health",
            token=self.application.session_token,
        )
        self.assertEqual(status, 405)
        self.assertEqual(headers["Allow"], "GET, HEAD")
        self.assertEqual(
            json.loads(body)["error"]["code"],
            "method_not_allowed",
        )

    def test_unhandled_route_error_hides_traceback_from_client(self) -> None:
        """Log handler exceptions and return a generic server error object."""
        with self.assertLogs("overpine.http", level="ERROR"):
            status, _headers, body = self._request("GET", "/api/v1/test-error")

        self.assertEqual(status, 500)
        self.assertEqual(
            json.loads(body),
            {
                "error": {
                    "code": "internal_server_error",
                    "message": "Internal server error",
                }
            },
        )
        self.assertNotIn(b"Traceback", body)

    def test_host_allowlist_rejects_foreign_host(self) -> None:
        """Reject a non-local Host header before dispatch."""
        status, _headers, body = self._request(
            "GET",
            "/api/v1/health",
            extra_headers={"Host": "attacker.example"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"]["code"], "bad_request")

    def test_state_changes_require_token_and_same_origin(self) -> None:
        """Reject missing tokens and foreign origins, while allowing local origin."""
        status, _headers, body = self._request(
            "POST",
            "/api/v1/test-body",
            json_body={"value": 1},
        )
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(body)["error"]["code"], "unauthorized")

        status, _headers, body = self._request(
            "POST",
            "/api/v1/test-body",
            json_body={"value": 1},
            token=self.application.session_token,
            extra_headers={"Origin": "https://attacker.example"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"]["code"], "forbidden")

        status, _headers, body = self._request(
            "POST",
            "/api/v1/test-body",
            json_body={"value": 2},
            token=self.application.session_token,
            extra_headers={"Origin": f"http://127.0.0.1:{self.port}"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"received": {"value": 2}})

    def test_no_cors_headers_are_returned(self) -> None:
        origin = f"http://127.0.0.1:{self.port}"
        status, headers, _body = self._request(
            "GET",
            "/api/v1/health",
            extra_headers={"Origin": origin},
        )
        self.assertEqual(status, 200)
        self.assertNotIn("Access-Control-Allow-Origin", headers)

        status, headers, _body = self._request(
            "GET",
            "/api/v1/health",
            extra_headers={"Origin": "https://attacker.example"},
        )
        self.assertEqual(status, 200)
        self.assertNotIn("Access-Control-Allow-Origin", headers)

        status, headers, body = self._request(
            "OPTIONS",
            "/api/v1/test-body",
            extra_headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": (
                    "Content-Type, X-Overpine-Token"
                ),
            },
        )
        self.assertEqual(status, 405)
        self.assertEqual(json.loads(body)["error"]["code"], "method_not_allowed")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertNotIn("Access-Control-Allow-Methods", headers)

    def test_json_body_errors_and_64_kib_limit(self) -> None:
        """Reject malformed JSON and requests exceeding the bounded body size."""
        status, _headers, body = self._request(
            "POST",
            "/api/v1/test-body",
            raw_body=b"{",
            token=self.application.session_token,
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"]["code"], "bad_request")

        status, _headers, body = self._request(
            "POST",
            "/api/v1/test-body",
            raw_body=b"",
            token=self.application.session_token,
            extra_headers={"Content-Length": str(64 * 1024 + 1)},
        )
        self.assertEqual(status, 413)
        self.assertEqual(
            json.loads(body)["error"]["code"],
            "payload_too_large",
        )

    def test_static_paths_are_contained_and_head_is_supported(self) -> None:
        """Serve static files with MIME/cache headers and reject traversal."""
        status, headers, body = self._request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertEqual(headers["Cache-Control"], "no-cache")
        self.assertIn(b"<!doctype html>", body.lower())

        status, headers, body = self._request("HEAD", "/")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertGreater(int(headers["Content-Length"]), 0)

        status, _headers, _body = self._request(
            "GET",
            "/%2e%2e/README.md",
        )
        self.assertEqual(status, 404)

    @staticmethod
    def _read_item(request: Request) -> dict[str, str]:
        """Return the captured identifier for the parameterized-route test."""
        return {"item_id": request.path_params["item_id"]}

    @staticmethod
    def _read_body(request: Request) -> dict[str, object]:
        """Return parsed JSON from the state-changing-route test."""
        return {"received": request.body}

    @staticmethod
    def _raise_error(_request: Request) -> dict[str, object]:
        """Raise an exception to verify safe server-error responses."""
        raise RuntimeError("private traceback detail")

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: object | None = None,
        raw_body: bytes | None = None,
        token: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, http.client.HTTPMessage, bytes]:
        """Send one request with http.client and close its socket."""
        headers = dict(extra_headers or {})
        if token is not None:
            headers["X-Overpine-Token"] = token
        body = raw_body
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.port,
            timeout=5,
        )
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, response.headers, response.read()
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
