"""Google Analytics 4 Data API wrapper (#25).

The GA4 SDK (``google-analytics-data``) is synchronous; we run blocking
calls in a thread so the FastAPI event loop is never blocked. Handlers
should treat this module as the only place that touches GA4.

The metrics surfaced in the daily digest:
- ``totalUsers``  -> site visitors over the report date range
- ``newUsers``    -> new visitors over the report date range
- ``totalUsers`` over the 7 days ending at ``end_date`` (trailing window)
- ``pagePath`` x ``screenPageViews`` -> top-N pages by views over the
  report date range

If the GA4 call fails, :func:`fetch_site_metrics` raises
:class:`GoogleAnalyticsError`; the caller logs and omits this source's
lines from the digest rather than failing the whole job.
"""

import asyncio
from dataclasses import dataclass
from datetime import date, timedelta

from something_really_bot.logging import get_logger

_logger = get_logger(__name__)


class GoogleAnalyticsError(Exception):
    """Raised on any GA4 SDK failure (auth, transport, parse)."""


@dataclass(frozen=True)
class TopPage:
    page_path: str
    views: int


@dataclass(frozen=True)
class SiteMetrics:
    """Per-site GA4 numbers for the digest."""

    total_users: int
    new_users: int
    total_users_7d: int
    top_pages: tuple[TopPage, ...]


async def fetch_site_metrics(
    property_id: str,
    start_date: date,
    end_date: date,
    *,
    top_n: int = 5,
    client: object | None = None,
) -> SiteMetrics:
    """Return :class:`SiteMetrics` for the GA4 property between the dates.

    ``client`` accepts a pre-built ``BetaAnalyticsDataClient`` (or
    test double); when ``None`` we construct one with application
    default credentials.
    """
    try:
        return await asyncio.to_thread(
            _fetch_site_metrics_sync, property_id, start_date, end_date, top_n, client
        )
    except GoogleAnalyticsError:
        raise
    except Exception as exc:  # noqa: BLE001 — funnel all GA4 errors to one type
        _logger.warning(
            "ga4_fetch_failed",
            extra={
                "property_id": property_id,
                "exception_type": type(exc).__name__,
            },
        )
        raise GoogleAnalyticsError(str(exc)) from exc


def _fetch_site_metrics_sync(
    property_id: str,
    start_date: date,
    end_date: date,
    top_n: int,
    client: object | None,
) -> SiteMetrics:
    # Imports are deferred so test doubles need not have the SDK
    # installed and so the production import cost is paid once per cold start.
    from google.analytics.data_v1beta import (
        BetaAnalyticsDataClient,
        DateRange,
        Dimension,
        Metric,
        OrderBy,
        RunReportRequest,
    )

    ga4 = client or BetaAnalyticsDataClient()
    day_range = DateRange(start_date=start_date.isoformat(), end_date=end_date.isoformat())
    seven_day_range = DateRange(
        start_date=(end_date - timedelta(days=6)).isoformat(),
        end_date=end_date.isoformat(),
    )
    property_resource = f"properties/{property_id}"

    # Two date ranges in one call: GA4 implicitly adds a `dateRange` dimension
    # and returns one row per range in order ("date_range_0", "date_range_1").
    totals = ga4.run_report(
        RunReportRequest(
            property=property_resource,
            metrics=[Metric(name="totalUsers"), Metric(name="newUsers")],
            date_ranges=[day_range, seven_day_range],
        )
    )
    total_users, new_users, total_users_7d = _extract_totals(totals)

    pages_order = OrderBy(
        metric=OrderBy.MetricOrderBy(metric_name="screenPageViews"),
        desc=True,
    )
    pages_response = ga4.run_report(
        RunReportRequest(
            property=property_resource,
            dimensions=[Dimension(name="pagePath")],
            metrics=[Metric(name="screenPageViews")],
            date_ranges=[day_range],
            order_bys=[pages_order],
            limit=top_n,
        )
    )
    top_pages = _extract_top_pages(pages_response)

    return SiteMetrics(
        total_users=total_users,
        new_users=new_users,
        total_users_7d=total_users_7d,
        top_pages=top_pages,
    )


def _extract_totals(response: object) -> tuple[int, int, int]:
    # GA4 auto-adds a ``dateRange`` dimension when more than one date range
    # is supplied. The row order is not guaranteed, so look the day row
    # ("date_range_0") and the 7-day row ("date_range_1") up by dimension
    # value instead of by position.
    rows = getattr(response, "rows", None) or []
    by_range = _rows_by_date_range(rows)
    day_row = by_range.get("date_range_0")
    week_row = by_range.get("date_range_1")
    day_total, day_new = _row_totals(day_row) if day_row is not None else (0, 0)
    week_total, _ = _row_totals(week_row) if week_row is not None else (0, 0)
    return day_total, day_new, week_total


def _rows_by_date_range(rows: list[object]) -> dict[str, object]:
    out: dict[str, object] = {}
    for idx, row in enumerate(rows):
        dim_values = getattr(row, "dimension_values", None) or []
        key = dim_values[0].value if dim_values else f"date_range_{idx}"
        out[key] = row
    return out


def _row_totals(row: object) -> tuple[int, int]:
    values = [_to_int(mv.value) for mv in getattr(row, "metric_values", []) or []]
    total = values[0] if len(values) > 0 else 0
    new = values[1] if len(values) > 1 else 0
    return total, new


def _extract_top_pages(response: object) -> tuple[TopPage, ...]:
    rows = getattr(response, "rows", None) or []
    out: list[TopPage] = []
    for row in rows:
        path = row.dimension_values[0].value if row.dimension_values else "(unknown)"
        views = _to_int(row.metric_values[0].value) if row.metric_values else 0
        out.append(TopPage(page_path=path, views=views))
    return tuple(out)


def _to_int(raw: object) -> int:
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return 0
