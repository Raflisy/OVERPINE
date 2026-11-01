"""Shared pytest setup."""
import logging

# backend.main calls create_app() when it is imported, and the tests call it again.
# Mark logging as already configured so test runs never write into the real
# logs/overpine.log file. (conftest.py is loaded before any test module.)
logging.getLogger()._overpine_configured = True  # type: ignore[attr-defined]
