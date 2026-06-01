"""Auto-generated ``/help`` text from the command registry.

Reads from ``commands.yaml`` via :class:`CommandRegistry` to render
the help body.  Display order is defined by the YAML, not by handler
registration order.
"""

from something_really_bot.routing.command_registry import CommandRegistry


class HelpRegistry:
    """Renders the ``/help`` body from the command registry."""

    HEADER = "Here's what I can do:"

    def __init__(self, command_registry: CommandRegistry) -> None:
        self._registry = command_registry

    def render(self, *, header: str | None = None) -> str:
        """Return the rendered help text, with an optional custom header."""
        lines: list[str] = [header or self.HEADER, ""]
        for entry in self._registry.help_entries():
            if entry.help_usage:
                lines.append(f"• {entry.help_usage} — {entry.description}")
            else:
                lines.append(f"• {entry.description}")
        if len(lines) == 2:
            lines.append("• (no documented features yet)")
        return "\n".join(lines)
