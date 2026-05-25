"""EUR/RUB exchange rate fetcher.

Uses the free Open Exchange Rates API (open.er-api.com) instead of the
ECB daily feed because the ECB suspended EUR/RUB publication in March 2022.
The API returns the last available rate, which satisfies the requirement
to always show a rate even on weekends and holidays.
"""

import httpx

EXCHANGE_RATE_URL = "https://open.er-api.com/v6/latest/EUR"


class FXRateError(Exception):
    """Raised when the exchange rate cannot be fetched or parsed."""


async def fetch_eur_rub_rate(
    *,
    http_client: httpx.AsyncClient | None = None,
) -> float:
    """Fetch the latest EUR/RUB exchange rate.

    Args:
        http_client: Optional pre-built client for connection reuse / testing.

    Returns:
        The EUR → RUB rate as a float (e.g. ``89.27``).

    Raises:
        FXRateError: API unreachable, returned an error, or RUB missing.
    """
    try:
        if http_client is not None:
            resp = await http_client.get(EXCHANGE_RATE_URL)
        else:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(EXCHANGE_RATE_URL)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise FXRateError(f"API request failed: {exc}") from exc

    if data.get("result") != "success":
        raise FXRateError(f"API returned error: {data.get('error-type', 'unknown')}")

    rates = data.get("rates", {})
    rub = rates.get("RUB")
    if rub is None:
        raise FXRateError("RUB rate not found in response")
    return float(rub)
