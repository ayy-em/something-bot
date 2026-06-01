"""Tests for :mod:`something_really_bot.routing.command_registry`."""

from something_really_bot.routing.command_registry import (
    CommandRegistry,
    FeatureEntry,
)


def _registry(*entries: FeatureEntry) -> CommandRegistry:
    return CommandRegistry(list(entries))


def test_entries_returns_copy() -> None:
    reg = _registry(FeatureEntry(handler_name="a", description="A"))
    entries = reg.entries
    entries.clear()
    assert len(reg.entries) == 1


def test_menu_commands_includes_only_visible_commands() -> None:
    reg = _registry(
        FeatureEntry(handler_name="a", description="A", command="/a", show_in_menu=True),
        FeatureEntry(handler_name="b", description="B", command="/b", show_in_menu=False),
        FeatureEntry(handler_name="c", description="C"),
    )

    menu = reg.menu_commands()

    assert len(menu) == 1
    assert menu[0].handler_name == "a"


def test_help_entries_skips_empty_descriptions() -> None:
    reg = _registry(
        FeatureEntry(handler_name="a", description="Visible"),
        FeatureEntry(handler_name="b", description=""),
        FeatureEntry(handler_name="c", description="  "),
    )

    help_items = reg.help_entries()

    assert len(help_items) == 1
    assert help_items[0].handler_name == "a"


def test_from_yaml_loads_production_file() -> None:
    reg = CommandRegistry.from_yaml()

    assert len(reg.entries) > 0
    names = [e.handler_name for e in reg.entries]
    assert "commands.help" in names
    assert "example.ping" in names


def test_menu_commands_from_production_yaml() -> None:
    reg = CommandRegistry.from_yaml()
    menu = reg.menu_commands()

    menu_names = {e.handler_name for e in menu}
    assert "commands.help" in menu_names
    assert "commands.start" in menu_names
    assert "example.ping" in menu_names
    assert "next_reunion" not in menu_names
