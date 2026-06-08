"""Reunion-date storage (Postgres) and countdown formatting."""

from datetime import date, timedelta

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

_ADD_DURATION_COL_SQL = f"""
    ALTER TABLE {TABLE_FQN}
    ADD COLUMN IF NOT EXISTS duration_days INTEGER
"""

MILESTONE_MESSAGES: dict[int, str] = {
    14: "Two weeks to go! Woo! \U0001f389",
    7: "Just one week to go! \U0001f973",
    3: "Only 3 days left! See you soon! \U0001f483",
    2: "The day after tomorrow a.k.a. послезавтра! \U0001f929",
    1: "One of you is coming tomorrow! (and maybe both ayoooo) ✈️",
    0: "TODAY IS THE DAY! Love is in the air!\U0001fac6",
}

ENJOYING_MESSAGE = "You are enjoying time together right now! <3"


async def ensure_table(storage: PostgresStorage) -> None:
    """Create the reunion_date table (and add columns) if needed."""
    await storage.execute(_CREATE_TABLE_SQL)
    await storage.execute(_ADD_DURATION_COL_SQL)


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


async def get_reunion_duration(storage: PostgresStorage) -> int | None:
    """Fetch the stored reunion duration in days, or ``None`` if not set.

    Args:
        storage: Postgres storage instance.

    Returns:
        Duration in days, or ``None``.
    """
    await ensure_table(storage)
    rows = await storage.fetch_all(f"SELECT duration_days FROM {TABLE_FQN} WHERE id = 1")
    if not rows:
        return None
    return rows[0]["duration_days"]


async def set_reunion_duration(storage: PostgresStorage, duration_days: int) -> None:
    """Store the reunion duration in days.

    Requires a reunion date row to already exist.

    Args:
        storage: Postgres storage instance.
        duration_days: Number of days the reunion lasts.
    """
    await ensure_table(storage)
    sql = f"UPDATE {TABLE_FQN} SET duration_days = %s WHERE id = 1"
    await storage.execute(sql, (duration_days,))


def is_during_reunion(target: date, today: date, duration_days: int) -> bool:
    """Check whether today falls within the reunion period.

    Args:
        target: The reunion start date.
        today: Today's date.
        duration_days: How many days the reunion lasts.

    Returns:
        ``True`` if ``target <= today < target + duration_days``.
    """
    return target <= today < target + timedelta(days=duration_days)


def is_reunion_expired(target: date, today: date, duration_days: int) -> bool:
    """Check whether the reunion period has ended.

    Args:
        target: The reunion start date.
        today: Today's date.
        duration_days: How many days the reunion lasts.

    Returns:
        ``True`` if ``today >= target + duration_days``.
    """
    return today >= target + timedelta(days=duration_days)


def format_reunion_line(target: date, today: date) -> str | None:
    """Format the reunion countdown line for the daily message.

    Does **not** account for duration — callers with duration should
    check :func:`is_during_reunion` / :func:`is_reunion_expired` first.

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
        return milestone

    base = f"Your next reunion is in {delta} days."
    if milestone:
        return f"{base} {milestone}"
    return base
