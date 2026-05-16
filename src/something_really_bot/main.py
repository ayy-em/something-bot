"""FastAPI application entrypoint.

``GET /healthz`` is an unauthenticated liveness probe used by Cloud Run.
``POST /webhook`` is the Telegram target; requests must carry the secret
header validated by :func:`verify_telegram_webhook_secret`. Payload parsing,
routing, and persistence land in subsequent issues.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Request

from something_really_bot import __version__
from something_really_bot.config import get_settings
from something_really_bot.telegram.security import verify_telegram_webhook_secret


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Eagerly build :class:`Settings` so missing required config crashes the boot."""
    get_settings()
    yield


app = FastAPI(
    title="Something Dashboard Telegram bot",
    version=__version__,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe used by Cloud Run and local smoke tests.

    Returns:
        A small JSON payload signalling the process is up.
    """
    return {"status": "healthy"}


@app.post("/webhook", dependencies=[Depends(verify_telegram_webhook_secret)])
async def webhook(_request: Request) -> dict[str, Any]:
    """Telegram webhook target.

    Requests must carry a valid ``X-Telegram-Bot-Api-Secret-Token`` header.
    Payload parsing/routing lands in #13 / #14; for now we acknowledge with a
    static 200.

    Returns:
        A small JSON payload acknowledging receipt.
    """
    return {"status": "ok"}
