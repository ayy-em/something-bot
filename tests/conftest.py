"""Shared test configuration.

Sets the minimum required environment variables so that ``Settings`` can be
built when the FastAPI app or its dependencies are imported in tests.
``TELEGRAM_QA_USERS`` is intentionally left unset — the empty allowlist
makes :class:`HelloWorldHandler` a no-op for the webhook integration tests,
which don't want to trigger real Telegram API calls.
"""

import os

os.environ.setdefault("TELEGRAM_WEBHOOK_SECRET", "test-secret")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-bot-token")
