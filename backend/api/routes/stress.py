"""Local stress-test API handlers with loopback and token protection."""
from __future__ import annotations

import ipaddress
import logging
import secrets
import threading
from typing import Any

from backend.core.privileges import is_admin, restart_as_admin
from backend.stress.manager import StressRunConflictError, StressWorkerStartError
from backend.web.router import HTTPError, Request, Response, json_response
from backend.web.validation import validate_stress_start


def _require_local_client(request: Request) -> None:
    """Reject token requests from non-loopback client addresses."""
    try:
        is_loopback = ipaddress.ip_address(request.client_address[0]).is_loopback
    except ValueError as exc:
        raise HTTPError(403, "Loopback access only") from exc
    if not is_loopback:
        raise HTTPError(403, "Loopback access only")


def _authorize(request: Request) -> None:
    """Compare the supplied per-app token without timing-sensitive equality."""
    supplied = request.headers.get("X-Overpine-Token")
    if not supplied or not secrets.compare_digest(
        supplied,
        request.app.session_token,
    ):
        raise HTTPError(401, "Valid X-Overpine-Token required")


def _restart_elevated(request: Request) -> None:
    """Restart after the HTTP response, releasing a desktop instance lock."""
    def prepare_restart() -> None:
        try:
            request.app.stress_manager.shutdown()
        except (OSError, RuntimeError):
            logging.getLogger("overpine").exception(
                "Could not stop stress workers before administrator restart"
            )
        release_lock = request.app.release_instance_lock
        if release_lock is not None:
            release_lock()

    try:
        restart_as_admin(on_launch=prepare_restart)
    except OSError:
        logging.getLogger("overpine").exception("Administrator restart failed")


def get_session_token(request: Request) -> dict[str, str]:
    """Expose the current app token only to a loopback browser client."""
    _require_local_client(request)
    return {"token": request.app.session_token}


def start_stress(request: Request) -> dict[str, Any]:
    """Start selected workers after validating token and target combination."""
    _authorize(request)
    payload = validate_stress_start(request.body)
    if not payload.targets:
        raise HTTPError(422, "Select at least one stress target")
    if "all" in payload.targets and len(payload.targets) != 1:
        raise HTTPError(422, "'all' cannot be combined with individual targets")

    try:
        return request.app.stress_manager.start(
            payload.targets,
            payload.duration_s,
            payload.intensity_percent,
        )
    except StressRunConflictError as exc:
        raise HTTPError(409, str(exc)) from exc
    except StressWorkerStartError as exc:
        raise HTTPError(500, str(exc)) from exc
    except ValueError as exc:
        raise HTTPError(422, str(exc)) from exc


def stop_stress(request: Request) -> dict[str, Any]:
    """Stop the active run and return the post-cleanup status."""
    _authorize(request)
    return request.app.stress_manager.stop()


def request_admin_restart(request: Request) -> Response:
    """Schedule an explicitly requested, token-authorized UAC restart."""
    _authorize(request)
    if is_admin():
        raise HTTPError(409, "OVERPINE is already running as administrator")
    threading.Timer(0.75, _restart_elevated, args=(request,)).start()
    return json_response({"status": "restart_requested"}, status=202)


def stress_status(request: Request) -> dict[str, Any]:
    """Return the active or most recently finished run's state."""
    return request.app.stress_manager.status()


def stress_results(request: Request) -> dict[str, Any]:
    """Return the result summary from the most recently completed run."""
    return {"result": request.app.stress_manager.results()}
