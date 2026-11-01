"""Local stress-test API with host-checked session token protection."""
from __future__ import annotations

import ipaddress
import logging
import secrets
import threading
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from backend.core.privileges import is_admin, restart_as_admin
from backend.stress.manager import StressRunConflictError, StressWorkerStartError

router = APIRouter(prefix="/stress", tags=["stress"])
admin_router = APIRouter(prefix="/admin", tags=["admin"])


class StressStartRequest(BaseModel):
    """Validated options for starting one bounded stress run."""

    targets: list[
        Literal["cpu", "memory", "disk", "network", "gpu", "all"]
    ] = Field(..., description="One or more stress targets, or only 'all'")
    duration_s: int = Field(..., ge=5, le=300)
    intensity_percent: int = Field(..., ge=10, le=100)


def _require_local_client(request: Request) -> None:
    """Reject token requests from non-loopback client addresses."""
    host = request.client.host if request.client else ""
    try:
        if not ipaddress.ip_address(host).is_loopback:
            raise HTTPException(status_code=403, detail="Loopback access only")
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Loopback access only") from exc


def _authorize(request: Request, token: str | None) -> None:
    """Compare the supplied per-app token without timing-sensitive equality."""
    expected = request.app.state.stress_token
    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Valid X-Overpine-Token required")


def _restart_elevated(request: Request) -> None:
    """Restart after the HTTP response, releasing a desktop instance lock."""
    def prepare_restart() -> None:
        try:
            request.app.state.stress_manager.shutdown()
        except Exception:
            logging.getLogger("overpine").exception(
                "Could not stop stress workers before administrator restart"
            )
        release_lock = getattr(request.app.state, "release_instance_lock", None)
        if release_lock is not None:
            release_lock()

    try:
        restart_as_admin(on_launch=prepare_restart)
    except OSError:
        logging.getLogger("overpine").exception("Administrator restart failed")


@router.get("/token")
def get_session_token(request: Request) -> dict[str, str]:
    """Expose the current app token only to a loopback browser client."""
    _require_local_client(request)
    return {"token": request.app.state.stress_token}


@router.post("/start")
def start_stress(
    payload: StressStartRequest,
    request: Request,
    x_overpine_token: str | None = Header(default=None, alias="X-Overpine-Token"),
) -> dict:
    """Start selected workers after validating token and target combination."""
    _authorize(request, x_overpine_token)
    if not payload.targets:
        raise HTTPException(status_code=422, detail="Select at least one stress target")
    if "all" in payload.targets and len(payload.targets) != 1:
        raise HTTPException(
            status_code=422,
            detail="'all' cannot be combined with individual targets",
        )
    manager = request.app.state.stress_manager
    try:
        return manager.start(
            payload.targets,
            payload.duration_s,
            payload.intensity_percent,
        )
    except StressRunConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except StressWorkerStartError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/stop")
def stop_stress(
    request: Request,
    x_overpine_token: str | None = Header(default=None, alias="X-Overpine-Token"),
) -> dict:
    """Stop the active run and return the post-cleanup status."""
    _authorize(request, x_overpine_token)
    return request.app.state.stress_manager.stop()


@admin_router.post("/restart-as-admin", status_code=202)
def request_admin_restart(
    request: Request,
    x_overpine_token: str | None = Header(default=None, alias="X-Overpine-Token"),
) -> dict[str, str]:
    """Schedule an explicitly requested, token-authorized UAC restart."""
    _authorize(request, x_overpine_token)
    if is_admin():
        raise HTTPException(status_code=409, detail="OVERPINE is already running as administrator")
    threading.Timer(0.75, _restart_elevated, args=(request,)).start()
    return {"status": "restart_requested"}


@router.get("/status")
def stress_status(request: Request) -> dict:
    """Return the active or most recently finished run's state."""
    return request.app.state.stress_manager.status()


@router.get("/results")
def stress_results(request: Request) -> dict:
    """Return the result summary from the most recently completed run."""
    return {"result": request.app.state.stress_manager.results()}
