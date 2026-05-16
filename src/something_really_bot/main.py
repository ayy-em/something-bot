"""FastAPI application entrypoint.

This module wires the FastAPI app shell that Cloud Run runs via ``uvicorn``.
``/webhook`` accepts any POST and returns a static 200 — Telegram secret-header
validation, update parsing, routing, and persistence land in subsequent issues
(#12 onward).
"""

from typing import Any

from fastapi import FastAPI, Request

from something_really_bot import __version__

app = FastAPI(
    title="Something Dashboard Telegram bot",
    version=__version__,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe used by Cloud Run and local smoke tests.

    Returns:
        A small JSON payload signalling the process is up.
    """
    return {"status": "healthy"}


@app.post("/webhook")
async def webhook(_request: Request) -> dict[str, Any]:
    """Telegram webhook target.

    Intentionally minimal: accepts any payload and returns a static 200 so the
    end-to-end deployment path (GitHub Actions → Cloud Run → public URL) can be
    proven before security and parsing land in #12 and #13. The request body is
    discarded.

    Returns:
        A small JSON payload acknowledging receipt.
    """
    return {"status": "ok"}
