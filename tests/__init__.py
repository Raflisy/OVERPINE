"""Test package with a guard against SQLite access outside temporary storage."""
from tests.database_guard import install_sqlite_path_guard

install_sqlite_path_guard()
