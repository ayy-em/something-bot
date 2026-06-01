"""Tests for the ``/help`` rendering backed by :class:`CommandRegistry`."""

from something_really_bot.routing.command_registry import (
    CommandRegistry,
    FeatureEntry,
    get_command_registry,
)
from something_really_bot.routing.help_registry import HelpRegistry


def _registry(*entries: FeatureEntry) -> CommandRegistry:
    return CommandRegistry(list(entries))


def test_render_splits_commands_and_features() -> None:
    reg = _registry(
        FeatureEntry(
            handler_name="a", description="Sticker.", help_usage="/make_sticker", command="/ms"
        ),
        FeatureEntry(handler_name="b", description="Voice memo transcription."),
    )
    hr = HelpRegistry(reg)

    body = hr.render()

    assert "Commands:" in body
    assert "/make_sticker — Sticker." in body
    assert "Apart from commands" in body
    assert "Voice memo transcription." in body


def test_render_skips_entries_with_show_in_help_false() -> None:
    reg = _registry(
        FeatureEntry(handler_name="visible", description="Visible.", command="/v", help_usage="/v"),
        FeatureEntry(
            handler_name="hidden", description="Hidden.", command="/h", show_in_help=False
        ),
    )
    hr = HelpRegistry(reg)

    body = hr.render()

    assert "Visible." in body
    assert "Hidden." not in body


def test_render_skips_entries_with_empty_description() -> None:
    reg = _registry(
        FeatureEntry(handler_name="visible", description="Visible.", command="/d", help_usage="/d"),
        FeatureEntry(handler_name="silent", description="", command="/s", help_usage="/s"),
    )
    hr = HelpRegistry(reg)

    body = hr.render()

    assert "Visible." in body
    assert "silent" not in body


def test_render_returns_placeholder_when_nothing_documented() -> None:
    hr = HelpRegistry(_registry())

    body = hr.render()

    assert "(no documented features yet)" in body


def test_render_custom_header() -> None:
    reg = _registry(
        FeatureEntry(handler_name="a", description="Hi.", command="/a", help_usage="/a"),
    )
    hr = HelpRegistry(reg)

    body = hr.render(header="Welcome!")

    assert body.startswith("Welcome!")


def test_production_commands_yaml_loads_without_error() -> None:
    registry = get_command_registry()
    assert len(registry.entries) > 0


def test_every_default_handler_has_a_registry_entry() -> None:
    from something_really_bot.main import build_default_dispatcher

    registry = get_command_registry()
    registered_names = {e.handler_name for e in registry.entries}
    dispatcher = build_default_dispatcher()
    handler_names = [h.name for h in dispatcher.handlers]

    missing = [n for n in handler_names if n not in registered_names]

    assert not missing, (
        f"Handlers missing from commands.yaml: {missing}. Add an entry for each handler."
    )


def test_production_help_has_two_sections() -> None:
    registry = get_command_registry()
    hr = HelpRegistry(registry)
    body = hr.render()

    assert "Commands:" in body
    assert "Apart from commands" in body
