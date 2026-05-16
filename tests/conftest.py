"""Shared test configuration.

Sets the minimum required environment variable so that ``Settings`` can be
built when the FastAPI app or its dependencies are imported in tests.
"""

import os

os.environ.setdefault("TELEGRAM_WEBHOOK_SECRET", "test-secret")
