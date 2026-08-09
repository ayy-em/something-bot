"""Self-healing webhook and command-menu job.

The job checks whether the Telegram webhook is set to the expected URL
and, if not, calls ``setWebhook`` to restore it.  It also syncs the
bot's autocomplete command menu via ``setMyCommands``, reading the
canonical list from ``commands.yaml``.

This guards against Telegram silently dropping the webhook after
consecutive delivery failures (e.g. during a Cloud Run revision swap).

Deliberately **not** on a Cloud Scheduler cadence: the deploy workflow
already calls ``setWebhook`` after every revision, and a 15-minute ping
kept an instance billable 24/7. Invoke it by hand when something looks
broken — ``GET /jobs/ensure-webhook?token=…``. See
``docs/decisions/0003-manual-ensure-webhook.md``.
"""

from something_really_bot.logging import get_logger
from something_really_bot.routing.command_registry import get_command_registry
from something_really_bot.routing.types import BotContext

_logger = get_logger(__name__)


class EnsureWebhookJob:
    """Scheduled job: verify webhook and sync the command menu."""

    name = "ensure-webhook"

    async def run(self, ctx: BotContext) -> None:
        """Check the webhook and fix it if missing or wrong, then sync commands."""
        await self._ensure_webhook(ctx)
        await self._sync_commands(ctx)

    async def _ensure_webhook(self, ctx: BotContext) -> None:
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

    async def _sync_commands(self, ctx: BotContext) -> None:
        client = ctx.telegram_client
        if client is None:
            _logger.warning("sync_commands_no_telegram_client")
            return

        registry = get_command_registry()
        bot_commands = [
            {
                "command": entry.command.lstrip("/").replace("-", "_"),
                "description": entry.description,
            }
            for entry in registry.menu_commands()
        ]

        try:
            await client.set_my_commands(bot_commands)
        except Exception:  # noqa: BLE001
            _logger.exception("sync_commands_set_my_commands_failed")
