"""Google Search Console wrapper for the daily digest (#51).

The Cloud Run runtime SA can't be added as a GSC user (Google's UI
rejects non-Google-account emails and there's no Admin API), so GSC
rides on a personal-OAuth refresh token issued by a user who already
has property access. The operator runs
``scripts/grant_gsc_refresh_token.py`` once to mint that token and
stores the result in three Secret Manager secrets:

* ``GOOGLE_OAUTH_SECRET_JSON`` — Desktop OAuth client JSON
  (contains ``client_id`` + ``client_secret`` under ``installed``).
* ``GOOGLE_OAUTH_CLIENT_ID`` — the same ``client_id`` as a standalone
  secret. Not read by the runtime; kept for operator convenience.
* ``GSC_OAUTH_REFRESH_TOKEN`` — the long-lived refresh token from the
  one-off flow.

At call time we mint a short-lived access token from those, drive the
``webmasters.searchanalytics.query`` endpoint, and return a frozen
:class:`SiteSearchMetrics`. Per-source / per-site degradation lives in
the caller: any failure here raises :class:`GoogleSearchConsoleError`
and the caller drops just the GSC line for that site.

The Google API client is synchronous; we run it in a thread via
``asyncio.to_thread`` so the FastAPI loop stays free, matching the GA4
wrapper.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from something_really_bot.config import Settings, get_settings
from something_really_bot.logging import get_logger

_logger = get_logger(__name__)

GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"


class GoogleSearchConsoleError(Exception):
    """Raised on any GSC SDK / auth / parse failure."""


@dataclass(frozen=True)
class SiteSearchMetrics:
    """Per-site Search Console numbers for the digest."""

    clicks: int
    impressions: int


async def fetch_site_search_metrics(
    site_url: str,
    start_date: date,
    end_date: date,
    *,
    service: object | None = None,
    settings: Settings | None = None,
) -> SiteSearchMetrics:
    """Return :class:`SiteSearchMetrics` for ``site_url`` between the dates.

    ``service`` accepts a pre-built ``googleapiclient`` resource (or
    test double) so unit tests don't need real creds. When ``None``,
    the wrapper builds credentials from ``settings`` (or ``get_settings()``
    if not passed) and stands up a fresh service.
    """
    try:
        return await asyncio.to_thread(
            _fetch_site_search_metrics_sync,
            site_url,
            start_date,
            end_date,
            service,
            settings,
        )
    except GoogleSearchConsoleError:
        raise
    except Exception as exc:  # noqa: BLE001 — funnel all errors to one type
        _logger.warning(
            "gsc_fetch_failed",
            extra={
                "site_url": site_url,
                "exception_type": type(exc).__name__,
            },
        )
        raise GoogleSearchConsoleError(str(exc)) from exc


def _fetch_site_search_metrics_sync(
    site_url: str,
    start_date: date,
    end_date: date,
    service: object | None,
    settings: Settings | None,
) -> SiteSearchMetrics:
    resolved_service = service or _build_service(settings or get_settings())
    request_body = {
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        # No ``dimensions`` → API aggregates over the whole property,
        # which is exactly the per-site totals we want. Adding
        # ``dimensions=["query"]`` later (issue follow-up) lets us
        # surface top queries without a second call.
        "rowLimit": 1,
        # GSC's default ``dataState=final`` excludes the last ~2-3 days
        # (data isn't yet stable). The digest runs the morning after, so
        # without "all" we routinely see 0 clicks / 0 impressions even
        # when GSC's web UI shows real numbers.
        "dataState": "all",
    }
    response = (
        resolved_service.searchanalytics()  # type: ignore[attr-defined]
        .query(siteUrl=site_url, body=request_body)
        .execute()
    )
    return _parse_search_analytics_response(response)


def _parse_search_analytics_response(response: Any) -> SiteSearchMetrics:
    """Pull clicks + impressions out of the (aggregated) response."""
    if not isinstance(response, dict):
        raise GoogleSearchConsoleError(
            f"GSC response was not a dict (got {type(response).__name__})"
        )
    rows = response.get("rows") or []
    if not rows:
        # GSC returns no rows when there were zero clicks/impressions
        # for the window — treat as a valid zero, not as an error.
        return SiteSearchMetrics(clicks=0, impressions=0)
    row = rows[0]
    clicks = _to_int(row.get("clicks", 0))
    impressions = _to_int(row.get("impressions", 0))
    return SiteSearchMetrics(clicks=clicks, impressions=impressions)


def _build_service(settings: Settings) -> Any:
    """Build a credentialed ``webmasters`` v3 client.

    Raises :class:`GoogleSearchConsoleError` when any of the three
    secrets is missing or unparseable — the caller's per-source
    degradation drops the GSC line and lets the rest of the digest send.
    """
    if settings.google_oauth_secret_json is None:
        raise GoogleSearchConsoleError("GOOGLE_OAUTH_SECRET_JSON is not configured")
    if settings.gsc_oauth_refresh_token is None:
        raise GoogleSearchConsoleError("GSC_OAUTH_REFRESH_TOKEN is not configured")

    client_id, client_secret = _parse_client_secrets(
        settings.google_oauth_secret_json.get_secret_value()
    )
    credentials = _build_user_credentials(
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=settings.gsc_oauth_refresh_token.get_secret_value(),
    )

    # Imports deferred so test doubles don't need googleapiclient
    # installed and we don't pay the discovery-doc cost at import time.
    from googleapiclient.discovery import build

    return build(
        "webmasters",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )


def _parse_client_secrets(raw_json: str) -> tuple[str, str]:
    try:
        blob = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise GoogleSearchConsoleError(
            f"GOOGLE_OAUTH_SECRET_JSON is not valid JSON: {exc}"
        ) from exc
    inner = blob.get("installed") or blob.get("web") or {}
    client_id = inner.get("client_id")
    client_secret = inner.get("client_secret")
    if not client_id or not client_secret:
        raise GoogleSearchConsoleError(
            "GOOGLE_OAUTH_SECRET_JSON missing client_id/client_secret under 'installed' (or 'web')"
        )
    return client_id, client_secret


def _build_user_credentials(*, client_id: str, client_secret: str, refresh_token: str) -> Any:
    """Mint OAuth user creds from the stored client + refresh token."""
    from google.oauth2.credentials import Credentials

    return Credentials.from_authorized_user_info(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        },
        scopes=[GSC_SCOPE],
    )


def _to_int(raw: object) -> int:
    try:
        return int(float(raw))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
