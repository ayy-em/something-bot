"""Centralised feature registry loaded from ``commands.yaml``.

The YAML file is the single source of truth for feature descriptions,
help text, Telegram menu visibility, and access gating.  Both the
``/help`` renderer and the ``setMyCommands`` sync job read from it.
The dispatcher uses it to enforce ``trusted_users_only`` gating.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "commands.yaml"


@dataclass(frozen=True)
class FeatureEntry:
    """One entry from ``commands.yaml``."""

    handler_name: str
    description: str
    help_usage: str | None = None
    command: str | None = None
    show_in_menu: bool = True
    show_in_help: bool = True
    trusted_users_only: bool = False


class CommandRegistry:
    """Loads and exposes the feature list from a YAML file."""

    def __init__(self, entries: list[FeatureEntry]) -> None:
        self._entries = entries
        self._by_handler: dict[str, FeatureEntry] = {e.handler_name: e for e in entries}

    @classmethod
    def from_yaml(cls, path: Path | None = None) -> "CommandRegistry":
        """Parse ``commands.yaml`` and return a registry instance."""
        resolved = path or _DEFAULT_PATH
        with open(resolved) as f:
            data = yaml.safe_load(f)
        entries: list[FeatureEntry] = []
        for item in data["features"]:
            entries.append(
                FeatureEntry(
                    handler_name=item["handler_name"],
                    description=item["description"],
                    help_usage=item.get("help_usage"),
                    command=item.get("command"),
                    show_in_menu=item.get("show_in_menu", True),
                    show_in_help=item.get("show_in_help", True),
                    trusted_users_only=item.get("trusted_users_only", False),
                )
            )
        return cls(entries)

    @property
    def entries(self) -> list[FeatureEntry]:
        """All feature entries in display order."""
        return list(self._entries)

    def get(self, handler_name: str) -> FeatureEntry | None:
        """Look up an entry by handler name."""
        return self._by_handler.get(handler_name)

    def menu_commands(self) -> list[FeatureEntry]:
        """Entries that should appear in Telegram's autocomplete menu."""
        return [e for e in self._entries if e.command and e.show_in_menu]

    def help_entries(self) -> list[FeatureEntry]:
        """Entries with a description, in display order."""
        return [e for e in self._entries if e.description.strip()]


@lru_cache(maxsize=1)
def get_command_registry() -> CommandRegistry:
    """Return the process-wide :class:`CommandRegistry`."""
    return CommandRegistry.from_yaml()
