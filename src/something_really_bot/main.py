"""FastAPI application entrypoint.

``GET /health`` is an unauthenticated liveness probe used by Cloud Run.
(``/healthz`` is intercepted by Google Frontend on ``*.run.app`` domains
and never reaches the container, so we use ``/health``.)
``POST /webhook`` is the Telegram target: requests must carry the secret
header validated by :func:`verify_telegram_webhook_secret`; the body is
parsed into a :class:`ParsedUpdate` and routed to the matching handler via
the module-level :class:`Dispatcher`. The endpoint always returns 200 to
Telegram, even when parsing or a handler fails — failures are caught and
logged so Telegram doesn't retry-storm us (SPEC §6.9).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request

from something_really_bot import __version__
from something_really_bot.config import Settings, get_settings
from something_really_bot.features.example.handler import PingHandler
from something_really_bot.features.hello_world.handler import HelloWorldHandler
from something_really_bot.logging import get_logger
from something_really_bot.routing.dispatcher import Dispatcher
from something_really_bot.routing.types import BotContext
from something_really_bot.telegram.client import get_telegram_client
from something_really_bot.telegram.parser import MalformedUpdateError, parse_update
from something_really_bot.telegram.security import verify_telegram_webhook_secret

_logger = get_logger(__name__)


def build_default_dispatcher() -> Dispatcher:
    """Construct the production dispatcher with all enabled handlers.

    Adding a new feature: implement a :class:`~routing.types.Handler` under
    ``features/<name>/``, import it here, and ``register`` it. No edits to
    the webhook route below.
    """
    dispatcher = Dispatcher()
    dispatcher.register(HelloWorldHandler())
    dispatcher.register(PingHandler())
    return dispatcher


dispatcher: Dispatcher = build_default_dispatcher()


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


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe used by Cloud Run and local smoke tests."""
    return {"status": "healthy"}


@app.post("/webhook", dependencies=[Depends(verify_telegram_webhook_secret)])
async def webhook(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Receive, classify, and dispatch a Telegram update.

    Always returns 200 — parse failures and handler exceptions are logged
    and persisted (#18) but never propagate to Telegram so we don't trigger
    delivery retries. The Telegram secret header is validated upstream by
    :func:`verify_telegram_webhook_secret`.
    """
    raw = await _safe_json(request)
    ctx = BotContext(settings=settings, telegram_client=get_telegram_client())

    try:
        parsed = parse_update(raw)
    except MalformedUpdateError as exc:
        _logger.warning("malformed_update", extra={"error": str(exc), "bot_id": ctx.bot_id})
        return {"status": "ok"}

    result = await dispatcher.dispatch(parsed, ctx)
    if not result.handled:
        _logger.info(
            "unhandled_update",
            extra={"update_id": parsed.update_id, "bot_id": ctx.bot_id},
        )

    return {"status": "ok"}


async def _safe_json(request: Request) -> Any:
    """Read the request body as JSON; return ``{}`` on parse failures."""
    try:
        return await request.json()
    except Exception:
        body = await request.body()
        _logger.warning("non_json_webhook_body", extra={"body_bytes": len(body)})
        return {}
