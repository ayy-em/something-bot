"""Tests for the ``/help`` rendering backed by :class:`CommandRegistry`."""

from something_really_bot.routing.command_registry import (
    CommandRegistry,
    FeatureEntry,
    get_command_registry,
)
from something_really_bot.routing.help_registry import HelpRegistry


def _registry(*entries: FeatureEntry) -> CommandRegistry:
    return CommandRegistry(list(entries))


def test_render_lists_entries_in_order() -> None:
    reg = _registry(
        FeatureEntry(handler_name="a", description="Greeting.", help_usage="/a"),
        FeatureEntry(handler_name="b", description="Help body.", help_usage="/b"),
        FeatureEntry(handler_name="c", description="Send a thing.", help_usage="Upload"),
        FeatureEntry(handler_name="d", description="Just chat."),
    )
    hr = HelpRegistry(reg)

    body = hr.render()

    lines = body.splitlines()
    assert lines[0] == "Here's what I can do:"
    assert lines[1] == ""
    assert lines[2] == "• /a — Greeting."
    assert lines[3] == "• /b — Help body."
    assert lines[4] == "• Upload — Send a thing."
    assert lines[5] == "• Just chat."


def test_render_skips_entries_with_empty_description() -> None:
    reg = _registry(
        FeatureEntry(handler_name="visible", description="visible", help_usage="/d"),
        FeatureEntry(handler_name="silent", description="", help_usage="/s"),
    )
    hr = HelpRegistry(reg)

    body = hr.render()

    assert "visible" in body
    assert "silent" not in body
    assert "/s" not in body


def test_render_returns_placeholder_when_nothing_documented() -> None:
    hr = HelpRegistry(_registry())

    body = hr.render()

    assert "(no documented features yet)" in body


def test_render_custom_header() -> None:
    reg = _registry(
        FeatureEntry(handler_name="a", description="Hi.", help_usage="/a"),
    )
    hr = HelpRegistry(reg)

    body = hr.render(header="Welcome!")

    assert body.startswith("Welcome!")


def test_production_commands_yaml_loads_without_error() -> None:
    """Smoke-test that the real commands.yaml parses correctly."""
    registry = get_command_registry()
    assert len(registry.entries) > 0


def test_every_default_handler_has_a_registry_entry() -> None:
    """Every handler in the production dispatcher must have a corresponding
    entry in commands.yaml.
    """
    from something_really_bot.main import build_default_dispatcher

    registry = get_command_registry()
    registered_names = {e.handler_name for e in registry.entries}
    dispatcher = build_default_dispatcher()
    handler_names = [h.name for h in dispatcher.handlers]

    missing = [n for n in handler_names if n not in registered_names]

    assert not missing, (
        f"Handlers missing from commands.yaml: {missing}. Add an entry for each handler."
    )
