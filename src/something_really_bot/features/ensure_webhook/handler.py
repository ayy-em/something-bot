"""Self-healing webhook job.

Cloud Scheduler fires ``POST /jobs/ensure-webhook`` every 15 minutes.
The job checks whether the Telegram webhook is set to the expected URL
and, if not, calls ``setWebhook`` to restore it.

This guards against Telegram silently dropping the webhook after
consecutive delivery failures (e.g. during a Cloud Run revision swap).
"""

from something_really_bot.logging import get_logger
from something_really_bot.routing.types import BotContext

_logger = get_logger(__name__)


class EnsureWebhookJob:
    """Scheduled job: verify and restore the Telegram webhook."""

    name = "ensure-webhook"

    async def run(self, ctx: BotContext) -> None:
        """Check the webhook and fix it if missing or wrong."""
        cloud_run_url = ctx.settings.cloud_run_url
        if not cloud_run_url:
            _logger.warning("ensure_webhook_no_cloud_run_url")
            return

        expected_url = f"{cloud_run_url.rstrip('/')}/webhook"

        client = ctx.telegram_client
        if client is None:
            _logger.warning("ensure_webhook_no_telegram_client")
            return

        info = await client.get_webhook_info()
        current_url = info.get("url", "")

        if current_url == expected_url:
            return

        _logger.warning(
            "ensure_webhook_fixing",
            extra={"current_url": current_url or "(empty)", "expected_url": expected_url},
        )

        webhook_secret = ctx.settings.telegram_webhook_secret.get_secret_value()
        await client.set_webhook(expected_url, secret_token=webhook_secret)
        _logger.info("ensure_webhook_restored", extra={"url": expected_url})
