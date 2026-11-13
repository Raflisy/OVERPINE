"""Loopback-only TCP load generator."""
from __future__ import annotations

import socket
import threading
import time
from threading import Event, Thread
from typing import Any

from backend.stress.base import StressWorker

_LOOPBACK = "127.0.0.1"
_PAYLOAD = b"OVERPINE loopback stress payload" * 2048


class NetworkStressWorker(StressWorker):
    """Move bytes between a local TCP server and client on 127.0.0.1 only."""

    def __init__(self) -> None:
        self._thread: Thread | None = None
        self._server_thread: Thread | None = None
        self._stop_event: Event | None = None
        self._state = "idle"
        self._error: str | None = None
        self._bytes_sent = 0
        self._bytes_received = 0
        self._listener: socket.socket | None = None
        self._connection: socket.socket | None = None

    def start(self, intensity: int, stop_event: Event) -> None:
        """Start a local server/client pair without resolving external hosts."""
        if self._state != "idle":
            raise RuntimeError("Network stress worker has already been started")
        self._stop_event = stop_event
        self._state = "running"
        self._thread = Thread(
            target=self._run,
            args=(stop_event, intensity),
            name="overpine-network-stress",
            daemon=True,
        )
        self._thread.start()

    def _run(self, stop_event: Event, intensity: int) -> None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                self._listener = listener
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind((_LOOPBACK, 0))
                listener.listen(1)
                listener.settimeout(0.2)
                port = listener.getsockname()[1]
                server_ready = threading.Event()

                def receive() -> None:
                    try:
                        while not stop_event.is_set():
                            try:
                                connection, address = listener.accept()
                            except TimeoutError:
                                continue
                            if address[0] != _LOOPBACK:
                                connection.close()
                                self._error = "Rejected non-loopback stress connection"
                                return
                            with connection:
                                self._connection = connection
                                connection.settimeout(0.2)
                                server_ready.set()
                                while not stop_event.is_set():
                                    try:
                                        data = connection.recv(65536)
                                    except TimeoutError:
                                        continue
                                    if not data:
                                        break
                                    self._bytes_received += len(data)
                    except OSError as exc:
                        if not stop_event.is_set():
                            self._error = f"Loopback server failed: {exc}"

                self._server_thread = Thread(
                    target=receive,
                    name="overpine-network-server",
                    daemon=True,
                )
                self._server_thread.start()
                with socket.create_connection((_LOOPBACK, port), timeout=2) as client:
                    self._connection = client
                    client.settimeout(0.2)
                    if not server_ready.wait(timeout=2):
                        raise OSError("Loopback stress server did not accept its client")
                    target_bytes_per_second = intensity * 10 * 1024**2 / 100
                    started_at = time.monotonic()
                    while not stop_event.is_set():
                        try:
                            client.sendall(_PAYLOAD)
                            self._bytes_sent += len(_PAYLOAD)
                        except TimeoutError:
                            continue
                        expected_elapsed = self._bytes_sent / target_bytes_per_second
                        stop_event.wait(
                            min(0.05, max(0.0, expected_elapsed - (time.monotonic() - started_at)))
                        )
                    try:
                        client.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                self._server_thread.join(timeout=2)
        except OSError as exc:
            if not stop_event.is_set():
                self._error = f"Loopback network workload failed: {exc}"
                self._state = "error"
        finally:
            self._connection = None
            self._listener = None
            if self._state != "error":
                self._state = "stopped"

    def stop(self) -> None:
        """Signal the loopback workload and close sockets to unblock I/O."""
        if self._stop_event is not None:
            self._stop_event.set()
        for connection in (self._connection, self._listener):
            if connection is not None:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    connection.close()
                except OSError:
                    pass
        for thread in (self._thread, self._server_thread):
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=2)

    def status(self) -> dict[str, Any]:
        """Report loopback transfer progress and worker errors."""
        return {
            "state": self._state,
            "bytes_sent": self._bytes_sent,
            "bytes_received": self._bytes_received,
            "local_address": _LOOPBACK,
            "error": self._error,
        }

    def result_summary(self) -> dict[str, Any]:
        """Return local transfer totals."""
        return {"bytes_sent": self._bytes_sent, "bytes_received": self._bytes_received}
