"""Section protocol for the modular daily message."""

from datetime import date
from typing import Protocol, runtime_checkable


@runtime_checkable
class Section(Protocol):
    """Contract every daily-message section implements.

    Each section fetches its own data and returns pre-escaped MarkdownV2
    text. Returning ``None`` omits the section from the message.
    """

    name: str

    async def render(self, today: date) -> str | None:
        """Return MarkdownV2 text for this section, or ``None`` to omit."""
