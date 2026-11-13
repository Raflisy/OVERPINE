"""Loopback-only threaded HTTP server for the OVERPINE desktop application."""
from __future__ import annotations

import logging
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from backend.web.router import (
    HTTPError,
    Request,
    Response,
    json_response,
    parse_request_target,
    read_json_body,
)
from backend.web.security import (
    require_session_token,
    trusted_hosts,
    trusted_origins,
    validate_host,
    validate_origin,
)
from backend.web.static import cache_control, resolve_static_file

_LOG = logging.getLogger("overpine.http")
_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "img-src 'self'; "
    "object-src 'none'; "
    "script-src 'self'; "
    "style-src 'self'"
)
_SECURITY_HEADERS = {
    "Content-Security-Policy": _CONTENT_SECURITY_POLICY,
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Content-Type-Options": "nosniff",
}
_NOT_FOUND_PAGE = (
    b"<!doctype html><html lang=\"en\"><meta charset=\"utf-8\">"
    b"<title>Not found</title><h1>404 Not Found</h1></html>"
)


class _RequestHandler(BaseHTTPRequestHandler):
    """Validate incoming requests before dispatching API or static content."""

    server: LocalHTTPServer
    protocol_version = "HTTP/1.1"
    server_version = "OVERPINE"
    sys_version = ""

    def do_GET(self) -> None:
        """Handle GET requests."""
        self._handle_request()

    def do_HEAD(self) -> None:
        """Handle HEAD requests without a response body."""
        self._handle_request()

    def do_POST(self) -> None:
        """Handle POST requests."""
        self._handle_request()

    def do_PUT(self) -> None:
        """Handle PUT requests."""
        self._handle_request()

    def do_DELETE(self) -> None:
        """Handle DELETE requests."""
        self._handle_request()

    def do_PATCH(self) -> None:
        """Handle PATCH requests."""
        self._handle_request()

    def do_OPTIONS(self) -> None:
        """Route OPTIONS requests without enabling cross-origin access."""
        self._handle_request()

    def __getattr__(self, name: str) -> Any:
        """Return JSON method handling for other syntactically valid HTTP verbs."""
        if name.startswith("do_"):
            return self._handle_request
        raise AttributeError(name)

    def log_message(self, format_string: str, *args: Any) -> None:
        """Log each request at DEBUG instead of the default INFO level."""
        _LOG.debug("%s - %s", self.address_string(), format_string % args)

    def end_headers(self) -> None:
        """Add browser security headers to every response."""
        for name, value in _SECURITY_HEADERS.items():
            self.send_header(name, value)
        super().end_headers()

    def _handle_request(self) -> None:
        """Validate security, parse the request, and send one response."""
        started = time.monotonic()
        status = 500
        try:
            validate_host(
                self.headers.get("Host"),
                self.server.allowed_hosts,
            )
            validate_origin(
                self.command,
                self.headers,
                self.server.allowed_origins,
            )
            require_session_token(
                self.command,
                self.headers,
                self.server.application.session_token,
            )
            path, query = parse_request_target(self.path)
            body = read_json_body(self.headers, self.rfile)
            request = Request(
                method=self.command,
                path=path,
                query=query,
                headers=self.headers,
                body=body,
                client_address=(str(self.client_address[0]), int(self.client_address[1])),
                app=self.server.application,
            )
            response = self.server.application.router.dispatch(request)
            if response is None:
                response = self._static_response(path)
            if response is None:
                if path == "/api" or path.startswith("/api/"):
                    response = json_response(
                        HTTPError(404, "API route not found").as_dict(),
                        404,
                    )
                else:
                    response = Response(
                        HTTPStatus.NOT_FOUND,
                        _NOT_FOUND_PAGE,
                        {
                            "Content-Type": "text/html; charset=utf-8",
                            "Cache-Control": "no-store",
                        },
                    )
            self._send_response(response, head=self.command == "HEAD")
            status = response.status
        except HTTPError as exc:
            self.close_connection = True
            headers: dict[str, str] = {}
            headers["Connection"] = "close"
            if exc.headers:
                headers.update(exc.headers)
            response = json_response(exc.as_dict(), exc.status, headers)
            try:
                self._send_response(response, head=self.command == "HEAD")
            except (BrokenPipeError, ConnectionResetError):
                status = 499
            else:
                status = exc.status
        except (BrokenPipeError, ConnectionResetError):
            status = 499
        except Exception:
            _LOG.exception(
                "Unhandled request failure for %s %s",
                self.command,
                self.path,
            )
            response = json_response(
                HTTPError(500, "Internal server error").as_dict(),
                500,
            )
            try:
                self._send_response(response, head=self.command == "HEAD")
            except (BrokenPipeError, ConnectionResetError):
                status = 499
            else:
                status = 500
        finally:
            _LOG.debug(
                "%s %s -> %d (%.1f ms)",
                self.command,
                self.path,
                status,
                (time.monotonic() - started) * 1000,
            )

    def _static_response(self, path: str) -> Response | None:
        """Return a static response for a safe frontend path, if it exists."""
        static_file = resolve_static_file(
            self.server.application.frontend_dir,
            path,
        )
        if static_file is None:
            return None
        try:
            payload = static_file.path.read_bytes()
        except OSError:
            _LOG.exception("Could not read frontend asset %s", static_file.path)
            raise HTTPError(500, "Static resource could not be read") from None
        return Response(
            200,
            payload,
            {
                "Content-Type": static_file.content_type,
                "Cache-Control": cache_control(path),
            },
        )

    def _send_response(self, response: Response, head: bool = False) -> None:
        """Write response headers and its body unless the request was HEAD."""
        self.send_response(response.status)
        self.send_header("Content-Length", str(len(response.body)))
        for name, value in response.headers.items():
            self.send_header(name, value)
        self.end_headers()
        if not head and response.body:
            self.wfile.write(response.body)


class LocalHTTPServer(ThreadingHTTPServer):
    """Serve the application on loopback with daemon request threads."""

    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64

    def __init__(
        self,
        application: Any,
        host: str = "127.0.0.1",
        port: int = 8000,
    ) -> None:
        """Bind the HTTP socket only to IPv4 loopback."""
        if host != "127.0.0.1":
            raise ValueError("OVERPINE must bind to 127.0.0.1")
        self.application = application
        self._serve_thread: threading.Thread | None = None
        self._serving = threading.Event()
        self._startup_error: BaseException | None = None
        super().__init__((host, port), _RequestHandler)
        actual_port = int(self.server_address[1])
        self.allowed_hosts = trusted_hosts(actual_port)
        self.allowed_origins = trusted_origins(actual_port)

    def service_actions(self) -> None:
        """Signal that the server loop is active."""
        self._serving.set()

    def start_in_thread(
        self,
        timeout: float = 5.0,
    ) -> threading.Thread:
        """Start the HTTP loop on a daemon thread and wait until it is serving."""
        if self._serve_thread is not None and self._serve_thread.is_alive():
            return self._serve_thread
        self._startup_error = None
        self._serving.clear()
        thread = threading.Thread(
            target=self._serve,
            name="overpine-http-server",
            daemon=True,
        )
        self._serve_thread = thread
        thread.start()
        if not self._serving.wait(timeout):
            if self._startup_error is not None:
                self.server_close()
                raise RuntimeError(
                    "OVERPINE server startup failed"
                ) from self._startup_error
            if not thread.is_alive():
                self.server_close()
                raise RuntimeError("OVERPINE server exited before becoming ready")
            self.server_close()
            raise TimeoutError("OVERPINE server did not become ready in time")
        return thread

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the HTTP loop, join its thread, and close the listening socket."""
        thread = self._serve_thread
        try:
            if thread is not None and thread.is_alive():
                if self._serving.is_set():
                    self.shutdown()
                else:
                    self.server_close()
                if thread is not threading.current_thread():
                    thread.join(timeout=max(timeout, 0.0))
                if thread.is_alive():
                    raise TimeoutError("OVERPINE server did not stop in time")
        finally:
            self.server_close()
            self._serve_thread = None

    def _serve(self) -> None:
        """Run application hooks around the HTTP serve loop."""
        started = False
        try:
            self.application.start()
            started = True
            self.serve_forever()
        except Exception as exc:
            if not started:
                self._startup_error = exc
                self._serving.set()
            else:
                _LOG.exception("OVERPINE server loop stopped unexpectedly")
        finally:
            if started:
                try:
                    self.application.stop()
                except Exception:
                    _LOG.exception("OVERPINE application shutdown failed")
