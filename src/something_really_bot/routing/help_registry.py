"""Auto-generated ``/help`` text from the command registry.

Reads from ``commands.yaml`` via :class:`CommandRegistry` to render
the help body.  Display order is defined by the YAML, not by handler
registration order.  Output is split into two sections: slash commands
first, then passive features.
"""

from something_really_bot.routing.command_registry import CommandRegistry, FeatureEntry

_COMMANDS_HEADER = "Commands:"
_FEATURES_DIVIDER = "\n🤖 Apart from commands, I can also help you in other ways:\n"


class HelpRegistry:
    """Renders the ``/help`` body from the command registry."""

    HEADER = "👋 Here's what I can do:"

    def __init__(self, command_registry: CommandRegistry) -> None:
        self._registry = command_registry

    def render(self, *, header: str | None = None) -> str:
        """Return the rendered help text, with an optional custom header."""
        visible = [e for e in self._registry.entries if e.show_in_help and e.description.strip()]

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
