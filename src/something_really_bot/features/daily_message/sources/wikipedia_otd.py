"""\"On This Day\" fact from the Wikimedia REST API."""

import random
from datetime import date

import httpx

WIKIMEDIA_OTD_URL = "https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/selected"
OTD_TEXT_MAX_LENGTH = 200


class WikipediaOTDError(Exception):
    """Raised when the On This Day API call fails or returns no events."""


async def fetch_on_this_day(
    today: date,
    *,
    rng: random.Random | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> str:
    """Fetch a random \"On This Day\" historical event for the given date.

    Args:
        today: The date to look up events for.
        rng: Optional RNG for deterministic event selection in tests.
        http_client: Optional pre-built client for connection reuse / testing.

    Returns:
        A string like ``"1955 -- The Austrian State Treaty is signed..."``.

    Raises:
        WikipediaOTDError: API call failed or returned no events.
    """
    url = f"{WIKIMEDIA_OTD_URL}/{today.month}/{today.day}"
    headers = {"User-Agent": "SomethingReallyBot/1.0"}

    try:
        if http_client is not None:
            resp = await http_client.get(url, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise WikipediaOTDError(f"API request failed: {exc}") from exc

    events = data.get("selected", []) or data.get("events", [])
    if not events:
        raise WikipediaOTDError("No events found for this date")

    pick = (rng or random.SystemRandom()).choice(events)
    year = pick.get("year", "")
    text = pick.get("text", "")

    fact = f"{year} — {text}" if year else text

    if len(fact) > OTD_TEXT_MAX_LENGTH:
        fact = fact[: OTD_TEXT_MAX_LENGTH - 1] + "…"

    return fact
