"""Unit tests for the GA4 + GSC source wrappers (#25, #51).

Both wrappers run the synchronous Google SDK in a thread; tests inject
a fake client/service to avoid hitting the network and the SDK.
"""

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest
from pydantic import SecretStr

from something_really_bot.config import Settings
from something_really_bot.features.daily_digest.source.google_analytics import (
    GoogleAnalyticsError,
    fetch_site_metrics,
)
from something_really_bot.features.daily_digest.source.google_search_console import (
    GoogleSearchConsoleError,
    fetch_site_search_metrics,
)


@dataclass
class _MetricValue:
    value: str


@dataclass
class _DimensionValue:
    value: str


@dataclass
class _Row:
    metric_values: list[_MetricValue] = field(default_factory=list)
    dimension_values: list[_DimensionValue] = field(default_factory=list)


@dataclass
class _Response:
    rows: list[_Row] = field(default_factory=list)


class _FakeGA4Client:
    """Replays a configured sequence of responses to ``run_report``."""

    def __init__(self, responses: list[_Response]) -> None:
        self._responses = list(responses)
        self.calls: list[Any] = []

    def run_report(self, request):  # type: ignore[no-untyped-def]
        self.calls.append(request)
        return self._responses.pop(0)


async def test_fetch_site_metrics_parses_two_run_report_responses() -> None:
    # First call returns one row per dateRange: day, then 7-day trailing window.
    # GA4 auto-adds a ``dateRange`` dimension when multiple ranges are queried —
    # match that here so the parser identifies rows by dimension value, not position.
    totals = _Response(
        rows=[
            _Row(
                metric_values=[_MetricValue("1234"), _MetricValue("412")],
                dimension_values=[_DimensionValue("date_range_0")],
            ),
            _Row(
                metric_values=[_MetricValue("8765"), _MetricValue("3000")],
                dimension_values=[_DimensionValue("date_range_1")],
            ),
        ]
    )
    pages = _Response(
        rows=[
            _Row(
                metric_values=[_MetricValue("312")],
                dimension_values=[_DimensionValue("/pricing")],
            ),
            _Row(
                metric_values=[_MetricValue("220")],
                dimension_values=[_DimensionValue("/about")],
            ),
        ]
    )
    client = _FakeGA4Client(responses=[totals, pages])

    result = await fetch_site_metrics(
        "280078425", date(2026, 5, 16), date(2026, 5, 16), client=client
    )

    assert result.total_users == 1234
    assert result.new_users == 412
    assert result.total_users_7d == 8765
    assert [p.page_path for p in result.top_pages] == ["/pricing", "/about"]
    assert [p.views for p in result.top_pages] == [312, 220]
    assert len(client.calls) == 2

    # The totals call must pass both date ranges; the 7-day range ends at
    # end_date and spans 7 days inclusive.
    totals_call = client.calls[0]
    assert len(totals_call.date_ranges) == 2
    assert totals_call.date_ranges[0].start_date == "2026-05-16"
    assert totals_call.date_ranges[0].end_date == "2026-05-16"
    assert totals_call.date_ranges[1].start_date == "2026-05-10"
    assert totals_call.date_ranges[1].end_date == "2026-05-16"


async def test_fetch_site_metrics_resolves_day_and_week_by_dimension_when_rows_swapped() -> None:
    # GA4 doesn't guarantee row order when only ``dateRange`` is the dimension;
    # the parser must use the dimension value, not the row index.
    totals = _Response(
        rows=[
            _Row(
                metric_values=[_MetricValue("8765"), _MetricValue("3000")],
                dimension_values=[_DimensionValue("date_range_1")],
            ),
            _Row(
                metric_values=[_MetricValue("1234"), _MetricValue("412")],
                dimension_values=[_DimensionValue("date_range_0")],
            ),
        ]
    )
    pages = _Response(rows=[])
    client = _FakeGA4Client(responses=[totals, pages])

    result = await fetch_site_metrics(
        "280078425", date(2026, 5, 16), date(2026, 5, 16), client=client
    )

    assert result.total_users == 1234
    assert result.new_users == 412
    assert result.total_users_7d == 8765


async def test_fetch_site_metrics_handles_empty_responses() -> None:
    client = _FakeGA4Client(responses=[_Response(rows=[]), _Response(rows=[])])

    result = await fetch_site_metrics(
        "280078425", date(2026, 5, 16), date(2026, 5, 16), client=client
    )

    assert result.total_users == 0
    assert result.new_users == 0
    assert result.total_users_7d == 0
    assert result.top_pages == ()


async def test_fetch_site_metrics_funnels_client_failures_to_typed_error() -> None:
    class _Boom:
        def run_report(self, _r):  # type: ignore[no-untyped-def]
            raise RuntimeError("transport down")

    with pytest.raises(GoogleAnalyticsError) as excinfo:
        await fetch_site_metrics("280078425", date(2026, 5, 16), date(2026, 5, 16), client=_Boom())
    assert "transport down" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Google Search Console (#51)
# --------------------------------------------------------------------------- #


class _FakeGSCQuery:
    def __init__(self, response: Any) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    def query(self, *, siteUrl: str, body: dict[str, Any]):  # noqa: N803
        self.calls.append({"siteUrl": siteUrl, "body": body})
        return self

    def execute(self) -> Any:
        if isinstance(self._response, BaseException):
            raise self._response
        return self._response


class _FakeGSCService:
    """Stub `googleapiclient` resource: ``service.searchanalytics().query(...).execute()``."""

    def __init__(self, response: Any) -> None:
        self._query = _FakeGSCQuery(response)

    def searchanalytics(self) -> _FakeGSCQuery:
        return self._query


async def test_fetch_site_search_metrics_parses_clicks_and_impressions() -> None:
    service = _FakeGSCService(response={"rows": [{"clicks": 45.0, "impressions": 1234.0}]})

    result = await fetch_site_search_metrics(
        "sc-domain:somethingreally.fun",
        date(2026, 5, 16),
        date(2026, 5, 16),
        service=service,
    )

    assert result.clicks == 45
    assert result.impressions == 1234
    # The query body matches what GSC expects for whole-property totals.
    call = service._query.calls[0]
    assert call["siteUrl"] == "sc-domain:somethingreally.fun"
    assert call["body"]["startDate"] == "2026-05-16"
    assert call["body"]["endDate"] == "2026-05-16"
    assert "dimensions" not in call["body"]
    # ``dataState=all`` includes the unfinalized last ~2-3 days so the
    # digest sees real numbers instead of zeros.
    assert call["body"]["dataState"] == "all"


async def test_fetch_site_search_metrics_treats_empty_rows_as_zero() -> None:
    service = _FakeGSCService(response={"rows": []})

    result = await fetch_site_search_metrics(
        "sc-domain:fintechcompass.net",
        date(2026, 5, 16),
        date(2026, 5, 16),
        service=service,
    )

    assert result.clicks == 0
    assert result.impressions == 0


async def test_fetch_site_search_metrics_funnels_service_errors() -> None:
    service = _FakeGSCService(response=RuntimeError("403 Forbidden"))

    with pytest.raises(GoogleSearchConsoleError) as excinfo:
        await fetch_site_search_metrics(
            "sc-domain:fintechcompass.net",
            date(2026, 5, 16),
            date(2026, 5, 16),
            service=service,
        )
    assert "403 Forbidden" in str(excinfo.value)


async def test_fetch_site_search_metrics_raises_when_secrets_missing() -> None:
    """No ``service`` passed and ``GOOGLE_OAUTH_SECRET_JSON`` unset → typed error."""
    settings = Settings.model_construct(
        telegram_webhook_secret=SecretStr("x"),
        telegram_bot_token=SecretStr("tok"),
        telegram_qa_user_ids=frozenset(),
        google_oauth_secret_json=None,
        gsc_oauth_refresh_token=None,
    )

    with pytest.raises(GoogleSearchConsoleError) as excinfo:
        await fetch_site_search_metrics(
            "sc-domain:fintechcompass.net",
            date(2026, 5, 16),
            date(2026, 5, 16),
            settings=settings,
        )
    assert "GOOGLE_OAUTH_SECRET_JSON" in str(excinfo.value)


async def test_fetch_site_search_metrics_raises_on_malformed_client_json() -> None:
    settings = Settings.model_construct(
        telegram_webhook_secret=SecretStr("x"),
        telegram_bot_token=SecretStr("tok"),
        telegram_qa_user_ids=frozenset(),
        google_oauth_secret_json=SecretStr("not json"),
        gsc_oauth_refresh_token=SecretStr("1//refresh"),
    )

    with pytest.raises(GoogleSearchConsoleError) as excinfo:
        await fetch_site_search_metrics(
            "sc-domain:fintechcompass.net",
            date(2026, 5, 16),
            date(2026, 5, 16),
            settings=settings,
        )
    assert "not valid JSON" in str(excinfo.value)


async def test_fetch_site_search_metrics_raises_when_client_json_missing_keys() -> None:
    settings = Settings.model_construct(
        telegram_webhook_secret=SecretStr("x"),
        telegram_bot_token=SecretStr("tok"),
        telegram_qa_user_ids=frozenset(),
        google_oauth_secret_json=SecretStr(json.dumps({"installed": {"client_id": "only-id"}})),
        gsc_oauth_refresh_token=SecretStr("1//refresh"),
    )

    with pytest.raises(GoogleSearchConsoleError) as excinfo:
        await fetch_site_search_metrics(
            "sc-domain:fintechcompass.net",
            date(2026, 5, 16),
            date(2026, 5, 16),
            settings=settings,
        )
    assert "missing client_id/client_secret" in str(excinfo.value)
