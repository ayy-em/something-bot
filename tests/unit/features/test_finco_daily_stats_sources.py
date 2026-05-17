"""Unit tests for the GA4 and GSC source wrappers (#25).

The wrappers run synchronous Google SDK calls in a thread; tests inject
fake clients/services to avoid hitting the network and the SDKs.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest

from something_really_bot.features.finco_daily_stats.source.google_analytics import (
    GoogleAnalyticsError,
    fetch_site_metrics,
)
from something_really_bot.features.finco_daily_stats.source.google_search_console import (
    GoogleSearchConsoleError,
    fetch_site_clicks,
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
    totals = _Response(rows=[_Row(metric_values=[_MetricValue("1234"), _MetricValue("412")])])
    pages = _Response(rows=[
        _Row(
            metric_values=[_MetricValue("312")],
            dimension_values=[_DimensionValue("/pricing")],
        ),
        _Row(
            metric_values=[_MetricValue("220")],
            dimension_values=[_DimensionValue("/about")],
        ),
    ])
    client = _FakeGA4Client(responses=[totals, pages])

    result = await fetch_site_metrics(
        "280078425", date(2026, 5, 16), date(2026, 5, 16), client=client
    )

    assert result.total_users == 1234
    assert result.new_users == 412
    assert [p.page_path for p in result.top_pages] == ["/pricing", "/about"]
    assert [p.views for p in result.top_pages] == [312, 220]
    assert len(client.calls) == 2


async def test_fetch_site_metrics_handles_empty_responses() -> None:
    client = _FakeGA4Client(responses=[_Response(rows=[]), _Response(rows=[])])

    result = await fetch_site_metrics(
        "280078425", date(2026, 5, 16), date(2026, 5, 16), client=client
    )

    assert result.total_users == 0
    assert result.new_users == 0
    assert result.top_pages == ()


async def test_fetch_site_metrics_funnels_client_failures_to_typed_error() -> None:
    class _Boom:
        def run_report(self, _r):  # type: ignore[no-untyped-def]
            raise RuntimeError("transport down")

    with pytest.raises(GoogleAnalyticsError) as excinfo:
        await fetch_site_metrics(
            "280078425", date(2026, 5, 16), date(2026, 5, 16), client=_Boom()
        )
    assert "transport down" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# GSC
# --------------------------------------------------------------------------- #


class _FakeGSCQuery:
    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response
        self.last_kwargs: dict[str, Any] | None = None

    def query(self, **kwargs):  # type: ignore[no-untyped-def]
        self.last_kwargs = kwargs
        return self

    def execute(self):  # type: ignore[no-untyped-def]
        return self._response


@dataclass
class _FakeGSCService:
    response: dict[str, Any]
    captured: _FakeGSCQuery | None = None

    def searchanalytics(self):  # type: ignore[no-untyped-def]
        self.captured = _FakeGSCQuery(self.response)
        return self.captured


async def test_fetch_site_clicks_returns_total_clicks() -> None:
    svc = _FakeGSCService(response={"rows": [{"clicks": 87.0}]})

    result = await fetch_site_clicks(
        "sc-domain:fintechcompass.net",
        date(2026, 5, 16),
        date(2026, 5, 16),
        service=svc,
    )

    assert result.clicks == 87


async def test_fetch_site_clicks_returns_zero_on_empty_rows() -> None:
    svc = _FakeGSCService(response={"rows": []})

    result = await fetch_site_clicks(
        "sc-domain:fintechcompass.net",
        date(2026, 5, 16),
        date(2026, 5, 16),
        service=svc,
    )

    assert result.clicks == 0


async def test_fetch_site_clicks_funnels_failures_to_typed_error() -> None:
    class _BoomQuery:
        def query(self, **_):  # type: ignore[no-untyped-def]
            return self

        def execute(self):  # type: ignore[no-untyped-def]
            raise RuntimeError("oops")

    class _BoomService:
        def searchanalytics(self):  # type: ignore[no-untyped-def]
            return _BoomQuery()

    with pytest.raises(GoogleSearchConsoleError) as excinfo:
        await fetch_site_clicks(
            "sc-domain:fintechcompass.net",
            date(2026, 5, 16),
            date(2026, 5, 16),
            service=_BoomService(),
        )
    assert "oops" in str(excinfo.value)
