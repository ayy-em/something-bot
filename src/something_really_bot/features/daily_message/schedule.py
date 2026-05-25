"""YAML-driven section schedule for the daily message."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

_DAY_MAP = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

_DEFAULT_PATH = Path(__file__).parent / "sections.yaml"


@dataclass(frozen=True)
class SectionEntry:
    """One entry from sections.yaml: a section name and its active weekdays."""

    name: str
    days: frozenset[int]


class Schedule:
    """Resolves which sections to include for a given date.

    Args:
        entries: Ordered list of section entries (order = display order).
    """

    def __init__(self, entries: list[SectionEntry]) -> None:
        self._entries = entries

    def sections_for_day(self, today: date) -> list[str]:
        """Return ordered section names active on ``today``'s weekday."""
        weekday = today.weekday()
        return [e.name for e in self._entries if weekday in e.days]

    @classmethod
    def from_yaml(cls, path: Path | None = None) -> "Schedule":
        """Load schedule from a YAML file.

        Args:
            path: Path to the YAML file. Defaults to ``sections.yaml``
                  next to this module.

        Returns:
            A populated :class:`Schedule`.
        """
        resolved = path or _DEFAULT_PATH
        with open(resolved) as f:
            data = yaml.safe_load(f)
        entries = []
        for item in data["sections"]:
            days = frozenset(_DAY_MAP[d] for d in item["days"])
            entries.append(SectionEntry(name=item["name"], days=days))
        return cls(entries)
