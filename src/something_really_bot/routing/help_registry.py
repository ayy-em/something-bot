"""Auto-generated ``/help`` text from the command registry.

Reads from ``commands.yaml`` via :class:`CommandRegistry` to render
the help body.  Display order is defined by the YAML, not by handler
registration order.  Output is split into two sections: slash commands
first, then passive features.

When ``user_id`` and ``trusted_user_ids`` are provided, entries with
``trusted_users_only: true`` are hidden from unauthorized users.
"""

from collections.abc import Set

from something_really_bot.routing.command_registry import CommandRegistry, FeatureEntry

_COMMANDS_HEADER = "Commands:"
_FEATURES_DIVIDER = "\n🤖 Apart from commands, I can also help you in other ways:\n"


class HelpRegistry:
    """Renders the ``/help`` body from the command registry."""

    HEADER = "👋 Here's what I can do:"

    def __init__(self, command_registry: CommandRegistry) -> None:
        self._registry = command_registry

    def render(
        self,
        *,
        header: str | None = None,
        user_id: int | None = None,
        trusted_user_ids: Set[int] | None = None,
    ) -> str:
        """Return the rendered help text, with an optional custom header.

        Args:
            header: Override the default header line.
            user_id: Telegram user ID of the requester. When provided
                together with ``trusted_user_ids``, entries marked
                ``trusted_users_only`` are hidden for non-trusted users.
            trusted_user_ids: Set of trusted Telegram user IDs.
        """
        is_trusted = (
            user_id is not None and trusted_user_ids is not None and (user_id in trusted_user_ids)
        )

        visible = [
            e
            for e in self._registry.entries
            if e.show_in_help and e.description.strip() and (not e.trusted_users_only or is_trusted)
        ]

        commands = [e for e in visible if e.command]
        features = [e for e in visible if not e.command]

        lines: list[str] = [header or self.HEADER, ""]

        if commands:
            lines.append(_COMMANDS_HEADER)
            for entry in commands:
                lines.append(_format_command(entry))

        if features:
            lines.append(_FEATURES_DIVIDER)
            for entry in features:
                lines.append(f"• {entry.description}")

        if not commands and not features:
            lines.append("• (no documented features yet)")

        return "\n".join(lines)


def _format_command(entry: FeatureEntry) -> str:
    """Render a single command bullet."""
    label = entry.help_usage or entry.command or ""
    if entry.description:
        return f"• {label} — {entry.description}"
    return f"• {label}"
