"""Prevent the unittest suite from opening SQLite files outside temp storage."""
from __future__ import annotations

import atexit
import os
import sqlite3
import tempfile
from pathlib import Path
from urllib.parse import unquote

_TEMP_ROOT = Path(tempfile.gettempdir()).resolve()
_ORIGINAL_CONNECT = sqlite3.connect


def _guarded_connect(database, *args, **kwargs):
    if isinstance(database, (str, bytes, os.PathLike)):
        raw_path = os.fsdecode(os.fspath(database))
        if raw_path != ":memory:" and not raw_path.startswith("file::memory:"):
            if raw_path.startswith("file:"):
                raw_path = unquote(raw_path[5:].split("?", 1)[0])
                if os.name == "nt":
                    windows_path = raw_path.lstrip("/")
                    if (
                        len(windows_path) > 2
                        and windows_path[1] == ":"
                        and windows_path[2] in "\\/"
                    ):
                        raw_path = windows_path
            path = Path(raw_path)
            if not path.is_absolute():
                path = Path.cwd() / path
            resolved_path = path.resolve()
            try:
                resolved_path.relative_to(_TEMP_ROOT)
            except ValueError as error:
                raise AssertionError(
                    "Tests may only open SQLite databases under "
                    f"{_TEMP_ROOT}; rejected {resolved_path}"
                ) from error
    return _ORIGINAL_CONNECT(database, *args, **kwargs)


def install_sqlite_path_guard() -> None:
    """Guard all SQLite connections for the lifetime of the test process."""
    if sqlite3.connect is _guarded_connect:
        return
    sqlite3.connect = _guarded_connect
    atexit.register(_restore_sqlite_connect)


def _restore_sqlite_connect() -> None:
    if sqlite3.connect is _guarded_connect:
        sqlite3.connect = _ORIGINAL_CONNECT
