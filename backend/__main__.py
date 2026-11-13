"""Start OVERPINE with ``python -m backend`` using only the standard library."""
from __future__ import annotations

import logging
import webbrowser

from backend.core.config import load_settings
from backend.main import create_app
from backend.web.server import LocalHTTPServer

_LOG = logging.getLogger("overpine")


def main() -> int:
    """Run on port 8000, falling back to an OS-assigned free loopback port."""
    settings = load_settings()
    application = create_app(settings)
    try:
        server = LocalHTTPServer(application, host="127.0.0.1", port=settings.port)
    except OSError as exc:
        _LOG.warning(
            "Could not bind to 127.0.0.1:%d (%s); selecting a free local port",
            settings.port,
            exc,
        )
        server = LocalHTTPServer(application, host="127.0.0.1", port=0)

    address, port = server.server_address
    url = f"http://{address}:{port}"
    try:
        thread = server.start_in_thread()
        print(f"OVERPINE is available at {url}")
        webbrowser.open(url)
        thread.join()
    except KeyboardInterrupt:
        _LOG.info("OVERPINE shutdown requested")
    except Exception:
        _LOG.exception("OVERPINE server failed")
        return 1
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
