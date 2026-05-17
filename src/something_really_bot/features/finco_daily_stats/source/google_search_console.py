"""Google Search Console wrapper (#25).

GSC's ``webmasters`` v3 ``searchanalytics.query`` returns aggregated
search-traffic numbers. We only surface total clicks in the daily
digest; pages/queries break-down is intentionally out of scope.

Like the GA4 wrapper, the SDK is synchronous, so calls run in a thread.
"""

import asyncio
from dataclasses import dataclass
from datetime import date

from something_really_bot.logging import get_logger

_logger = get_logger(__name__)


class GoogleSearchConsoleError(Exception):
    """Raised on any GSC API failure (auth, transport, parse)."""


@dataclass(frozen=True)
class SearchConsoleMetrics:
    """Per-site GSC numbers for the digest."""

    clicks: int


async def fetch_site_clicks(
    site_url: str,
    start_date: date,
    end_date: date,
    *,
    service: object | None = None,
) -> SearchConsoleMetrics:
    """Return total Google search clicks for the property between the dates.

    ``service`` accepts a pre-built Google API discovery service (or
    test double); when ``None`` we build one using application default
    credentials with the readonly webmasters scope.
    """
    try:
        return await asyncio.to_thread(
            _fetch_site_clicks_sync, site_url, start_date, end_date, service
        )
    except GoogleSearchConsoleError:
        raise
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "gsc_fetch_failed",
            extra={"site_url": site_url, "exception_type": type(exc).__name__},
        )
        raise GoogleSearchConsoleError(str(exc)) from exc


def _fetch_site_clicks_sync(
    site_url: str,
    start_date: date,
    end_date: date,
    service: object | None,
) -> SearchConsoleMetrics:
    svc = service or _build_default_service()
    body = {
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "dimensions": [],
        "rowLimit": 1,
    }
    response = svc.searchanalytics().query(siteUrl=site_url, body=body).execute()
    rows = response.get("rows") or []
    clicks = int(rows[0].get("clicks", 0)) if rows else 0
    return SearchConsoleMetrics(clicks=clicks)


def _build_default_service() -> object:
    # Deferred so the SDK is not required for tests that inject a fake service.
    import google.auth
    from googleapiclient.discovery import build

    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/webmasters.readonly"]
    )
    return build("webmasters", "v3", credentials=credentials, cache_discovery=False)
