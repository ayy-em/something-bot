"""Daily message composer: reads the YAML schedule, resolves active
sections for the current day, fetches all in parallel, and joins
the results into a single MarkdownV2 message.
"""

import asyncio
from datetime import date

from something_really_bot.features.daily_message.markdown import md
from something_really_bot.features.daily_message.schedule import Schedule
from something_really_bot.features.daily_message.section import Section
from something_really_bot.logging import get_logger

_logger = get_logger(__name__)


class DailyMessageComposer:
    """Orchestrates section rendering for one daily message.

    Args:
        sections: All available section instances (order irrelevant;
            the schedule determines which run and in what order).
        schedule: Loaded :class:`Schedule` controlling day-of-week routing.
    """

    def __init__(self, sections: list[Section], schedule: Schedule) -> None:
        self._sections = {s.name: s for s in sections}
        self._schedule = schedule

    async def compose(self, today: date) -> str:
        """Build the full MarkdownV2 message for ``today``.

        Returns:
            A ready-to-send MarkdownV2 string.
        """
        header = f"*Today \\({md(today.isoformat())}\\)*"

        active_names = self._schedule.sections_for_day(today)
        active = [self._sections[n] for n in active_names if n in self._sections]

        if not active:
            return f"{header}\n\nNo data available today\\."

        results = await asyncio.gather(*(s.render(today) for s in active))
        body_parts = [r for r in results if r is not None]

        if not body_parts:
            return f"{header}\n\nNo data available today\\."

        return "\n\n".join([header, *body_parts])
