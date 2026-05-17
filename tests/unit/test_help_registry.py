"""Tests for the auto-generated ``/help`` rendering (#27).

Covers:
- HelpRegistry.render() against a stubbed handler list.
- Adding a new handler with a description surfaces in /help with no
  command-handler changes.
- Every handler registered by ``build_default_dispatcher`` has a
  non-empty description (CI enforcement for the contract).
"""

from typing import Any

from something_really_bot.main import build_default_dispatcher
from something_really_bot.routing.help_registry import (
    HelpRegistry,
    collect_descriptions,
)
from something_really_bot.routing.types import BotContext, HandlerResult


class _DummyHandler:
    def __init__(
        self, *, name: str, description: str, help_usage: str | None = None
    ) -> None:
        self.name = name
        self.description = description
        self.help_usage = help_usage

    def matches(self, _u: Any, _c: BotContext) -> bool:
        return False

    async def handle(self, _u: Any, _c: BotContext) -> HandlerResult:
        return HandlerResult(handled=False)


def test_render_lists_documented_handlers_in_registration_order() -> None:
    handlers = [
        _DummyHandler(name="a", description="Greeting.", help_usage="/a"),
        _DummyHandler(name="b", description="Help body.", help_usage="/b"),
        _DummyHandler(name="c", description="Send a thing.", help_usage="Upload"),
        _DummyHandler(name="d", description="Just chat."),  # no usage
    ]
    registry = HelpRegistry(lambda: handlers)

    body = registry.render()

    lines = body.splitlines()
    assert lines[0] == "Here's what I can do:"
    assert lines[1] == ""
    assert lines[2] == "• /a — Greeting."
    assert lines[3] == "• /b — Help body."
    assert lines[4] == "• Upload — Send a thing."
    assert lines[5] == "• Just chat."


def test_render_skips_handlers_with_empty_description() -> None:
    handlers = [
        _DummyHandler(name="documented", description="visible", help_usage="/d"),
        _DummyHandler(name="silent", description="", help_usage="/s"),
    ]
    registry = HelpRegistry(lambda: handlers)

    body = registry.render()

    assert "visible" in body
    assert "silent" not in body
    assert "/s" not in body


def test_render_pulls_fresh_handlers_each_call() -> None:
    """A handler added after construction shows up on the next render."""
    handlers: list[_DummyHandler] = [
        _DummyHandler(name="a", description="first", help_usage="/a"),
    ]
    registry = HelpRegistry(lambda: handlers)

    before = registry.render()
    handlers.append(
        _DummyHandler(name="b", description="second", help_usage="/b")
    )
    after = registry.render()

    assert "second" not in before
    assert "second" in after


def test_render_returns_placeholder_when_nothing_documented() -> None:
    registry = HelpRegistry(lambda: [])

    body = registry.render()

    assert "(no documented features yet)" in body


# --------------------------------------------------------------------------- #
# Production-assembly enforcement: every registered handler has a description.
# This is the CI gate that closes the loop on #27's acceptance criterion.
# --------------------------------------------------------------------------- #


def test_every_default_handler_has_a_non_empty_description() -> None:
    dispatcher = build_default_dispatcher()
    descriptions = collect_descriptions(dispatcher.handlers)

    missing = [name for name, desc in descriptions.items() if not desc.strip()]

    assert not missing, (
        f"Handlers missing a description (required by #27): {missing}. "
        "Add `description: str = ...` to the handler class."
    )
