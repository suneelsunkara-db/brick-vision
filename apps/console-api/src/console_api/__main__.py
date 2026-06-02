"""``python -m console_api`` entry point.

Lets operators run the sidecar directly without remembering the
Uvicorn invocation: ``python -m console_api`` is equivalent to
``uvicorn console_api.main:app``.
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    """Boot the FastAPI sidecar via uvicorn.

    Honors the standard env vars:

    - ``CONSOLE_API_HOST`` (default ``127.0.0.1``)
    - ``CONSOLE_API_PORT`` (default ``8000``)
    - ``CONSOLE_API_RELOAD`` (default ``0``; set to ``1`` for dev)
    """

    host = os.environ.get("CONSOLE_API_HOST", "127.0.0.1")
    port = int(os.environ.get("CONSOLE_API_PORT", "8000"))
    reload = os.environ.get("CONSOLE_API_RELOAD", "0") == "1"
    uvicorn.run(
        "console_api.main:app",
        host=host,
        port=port,
        reload=reload,
        log_level=os.environ.get("CONSOLE_API_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
