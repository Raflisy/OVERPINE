"""Shared temporary app/config paths for tests that construct the application."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from backend.main import create_app


def create_isolated_app(
    test_case: TestCase,
    *args: object,
    **kwargs: object,
):
    """Create an app with isolated data/config paths and register cleanup."""
    temporary_directory = tempfile.TemporaryDirectory(prefix="overpine-test-")
    root = Path(temporary_directory.name).resolve()
    test_case.addCleanup(temporary_directory.cleanup)
    patchers = (
        patch("backend.main.DATA_DIR", root / "data"),
        patch(
            "backend.core.config.CONFIG_FILE",
            root / "config" / "default_config.toml",
        ),
        patch("backend.core.config.user_data_dir", return_value=root),
        patch("backend.stress.manager.DATA_DIR", root / "data" / "stress"),
    )
    for patcher in patchers:
        patcher.start()
        test_case.addCleanup(patcher.stop)
    app = create_app(*args, **kwargs)
    test_case.addCleanup(app.stop)
    return app
