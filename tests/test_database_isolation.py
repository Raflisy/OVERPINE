"""Tests that guard the unittest suite from real SQLite databases."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests.database_guard import _guarded_connect


class DatabaseIsolationTests(unittest.TestCase):
    def test_sqlite_guard_rejects_paths_outside_temporary_storage(self) -> None:
        self.assertIs(sqlite3.connect, _guarded_connect)
        outside_path = (
            Path(__file__).resolve().parents[1]
            / "data"
            / "must-not-be-opened-by-tests.db"
        )

        with self.assertRaisesRegex(AssertionError, "only open SQLite databases"):
            sqlite3.connect(outside_path)

    def test_sqlite_guard_allows_temporary_databases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "test.db"
            connection = sqlite3.connect(database_path)
            try:
                connection.execute("CREATE TABLE sample (value INTEGER)")
            finally:
                connection.close()
            connection = sqlite3.connect(
                f"{database_path.resolve().as_uri()}?mode=ro",
                uri=True,
            )
            try:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM sample").fetchone()[0],
                    0,
                )
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
