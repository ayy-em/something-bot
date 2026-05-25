"""Reunion-date storage (Postgres) and countdown formatting."""

from datetime import date

from something_really_bot.logging import get_logger
from something_really_bot.persistence.postgres import PostgresStorage

_logger = get_logger(__name__)

TABLE_FQN = "public.reunion_date"

_CREATE_TABLE_SQL = f"""
    CREATE TABLE IF NOT EXISTS {TABLE_FQN} (
        id INTEGER PRIMARY KEY DEFAULT 1,
        target_date DATE NOT NULL,
        CHECK (id = 1)
    )
"""

MILESTONE_MESSAGES: dict[int, str] = {
    14: "Two weeks to go! Woo! \U0001f389",
    7: "Just one week to go! \U0001f973",
    3: "Only 3 days left! See you soon! \U0001f483",
    2: "The day after tomorrow a.k.a. послезавтра! \U0001f929",
    1: "One of you is coming tomorrow! (and maybe both ayoooo) ✈️",
    0: "TODAY IS THE DAY! Love is in the air!\U0001fac6",
}


async def ensure_table(storage: PostgresStorage) -> None:
    """Create the reunion_date table if it doesn't exist."""
    await storage.execute(_CREATE_TABLE_SQL)


async def get_reunion_date(storage: PostgresStorage) -> date | None:
    """Fetch the stored reunion date, or ``None`` if not set.

    Args:
        storage: Postgres storage instance.

    Returns:
        The target reunion date, or ``None``.
    """
    await ensure_table(storage)
    rows = await storage.fetch_all(f"SELECT target_date FROM {TABLE_FQN} WHERE id = 1")
    if not rows:
        return None
    return rows[0]["target_date"]


async def set_reunion_date(storage: PostgresStorage, target: date) -> None:
    """Upsert the reunion date (single-row table).

    Args:
        storage: Postgres storage instance.
        target: The new reunion date to store.
    """
    await ensure_table(storage)
    sql = (
        f"INSERT INTO {TABLE_FQN} (id, target_date) VALUES (1, %s) "
        "ON CONFLICT (id) DO UPDATE SET target_date = EXCLUDED.target_date"
    )
    await storage.execute(sql, (target,))


def format_reunion_line(target: date, today: date) -> str | None:
    """Format the reunion countdown line for the daily message.

    Args:
        target: The stored reunion date.
        today: Today's date.

    Returns:
        A formatted string like ``"Your next reunion is in 13 days."``,
        or ``None`` if the reunion date is in the past.
    """
    delta = (target - today).days
    if delta < 0:
        return None

    milestone = MILESTONE_MESSAGES.get(delta)
    if delta == 0:
        return f"❤️ {milestone}"

    base = f"❤️ Your next reunion is in {delta} days."
    if milestone:
        return f"{base} {milestone}"
    return base
