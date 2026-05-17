"""Auto-generated ``/help`` text from the feature registry (#27).

Each registered :class:`Handler` exposes a ``description`` and an
optional ``help_usage``. :class:`HelpRegistry` walks the
:class:`Dispatcher`'s handlers in registration order and renders one
bullet per handler.

A handler with an empty ``description`` is omitted (and flagged by the
production-assembly test in ``tests/unit/test_help_registry.py`` —
that's the CI enforcement). This keeps debug-only handlers used in
unit tests from breaking when they don't bother documenting
themselves.
"""

from collections.abc import Callable, Iterable

from something_really_bot.routing.types import Handler


class HelpRegistry:
    """Renders the ``/help`` body from a live handler list."""

    HEADER = "Here's what I can do:"

    def __init__(self, get_handlers: Callable[[], Iterable[Handler]]) -> None:
        self._get_handlers = get_handlers

    def render(self, *, header: str | None = None) -> str:
        """Return the rendered help text, with an optional custom header."""
        lines: list[str] = [header or self.HEADER, ""]
        for handler in self._get_handlers():
            description = getattr(handler, "description", "").strip()
            if not description:
                continue
            usage = getattr(handler, "help_usage", None)
            if usage:
                lines.append(f"• {usage} — {description}")
            else:
                lines.append(f"• {description}")
        if len(lines) == 2:
            # No documented handlers — give a non-empty body so users still
            # see something while a fresh deploy registers features.
            lines.append("• (no documented features yet)")
        return "\n".join(lines)


def collect_descriptions(handlers: Iterable[Handler]) -> dict[str, str]:
    """Return ``name → description`` for every handler.

    Used by the assembly test to enforce that production handlers all
    document themselves.
    """
    return {h.name: getattr(h, "description", "") for h in handlers}
