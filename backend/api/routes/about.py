"""Local application, device, and import-check information."""
from __future__ import annotations

import platform
from datetime import datetime, timezone
from typing import Any

from backend.core.config import APP_NAME, APP_VERSION, PROJECT_ROOT
from backend.services.system_info import get_system_info
from backend.storage.history import HistoryStorageError
from backend.web.router import Request
from tools.check_imports import check_python_imports, frontend_external_urls


def get_about(request: Request) -> dict[str, Any]:
    """Return local app details and a live result from the import checker."""
    system_info = get_system_info()
    history_status = request.app.history_store.status()
    try:
        first_sample_timestamp = (
            request.app.history_store.first_sample_timestamp()
        )
        history_warning = history_status["warning"]
    except HistoryStorageError as error:
        first_sample_timestamp = None
        history_warning = str(error)

    import_findings = check_python_imports(PROJECT_ROOT)
    external_urls = frontend_external_urls(PROJECT_ROOT)
    windows_version = (
        f"Windows {platform.release()} (build {platform.version()})"
        if platform.system() == "Windows"
        else f"{platform.system()} {platform.release()} ({platform.version()})"
    )
    return {
        "app_name": APP_NAME,
        "app_version": APP_VERSION,
        "python_version": platform.python_version(),
        "windows_version": windows_version,
        "machine_model": system_info["device_model"],
        "data_directory": str(request.app.data_dir.resolve()),
        "database_size_bytes": history_status["database_size_bytes"],
        "first_sample_timestamp": first_sample_timestamp,
        "first_sample_date": (
            datetime.fromtimestamp(
                first_sample_timestamp,
                tz=timezone.utc,
            ).isoformat()
            if first_sample_timestamp is not None
            else None
        ),
        "history_warning": history_warning,
        "credits": request.app.settings.about_credits,
        "course_university": request.app.settings.about_course_university,
        "standard_library_statement": (
            "Built with the Python standard library only"
        ),
        "import_check": {
            "standard_library_only": not import_findings,
            "imports_found": import_findings,
            "external_frontend_urls": external_urls,
        },
    }
