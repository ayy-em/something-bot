"""FastAPI application entrypoint.

This module wires the FastAPI app shell that Cloud Run runs via ``uvicorn``.
Business logic (webhook, routing, persistence) is intentionally absent at this
stage; only a health endpoint is exposed so deploys can be probed.
"""

from fastapi import FastAPI

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
    return {"status": "ok"}
