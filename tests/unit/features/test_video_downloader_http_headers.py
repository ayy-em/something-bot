"""Tests for the browser-like HTTP headers module."""

from something_really_bot.features.video_downloader.http_headers import (
    USER_AGENTS,
    get_random_headers,
)


def test_user_agent_pool_is_large() -> None:
    assert len(USER_AGENTS) >= 60


def test_all_user_agents_are_non_empty_strings() -> None:
    for ua in USER_AGENTS:
        assert isinstance(ua, str)
        assert len(ua) > 20


def test_no_duplicate_user_agents() -> None:
    assert len(USER_AGENTS) == len(set(USER_AGENTS))


def test_get_random_headers_returns_required_keys() -> None:
    headers = get_random_headers()
    assert "User-Agent" in headers
    assert "Accept" in headers
    assert "Accept-Language" in headers
    assert "Referer" in headers
    assert headers["User-Agent"] in USER_AGENTS


def test_get_random_headers_varies_user_agent() -> None:
    seen = {get_random_headers()["User-Agent"] for _ in range(200)}
    assert len(seen) > 1


def test_firefox_headers_omit_sec_ch_ua() -> None:
    for _ in range(500):
        headers = get_random_headers()
        if "Firefox" in headers["User-Agent"]:
            assert "sec-ch-ua-mobile" not in headers
            assert "sec-ch-ua-platform" not in headers
            return
    pytest.skip("Firefox UA not selected in 500 draws")


def test_mobile_headers_set_mobile_flag() -> None:
    for _ in range(500):
        headers = get_random_headers()
        ua = headers["User-Agent"]
        if "Mobile" in ua and "Firefox" not in ua:
            assert headers.get("sec-ch-ua-mobile") == "?1"
            return
    pytest.skip("Mobile non-Firefox UA not selected in 500 draws")


def test_desktop_chrome_headers_set_platform() -> None:
    for _ in range(500):
        headers = get_random_headers()
        ua = headers["User-Agent"]
        if "Windows" in ua and "Firefox" not in ua and "Mobile" not in ua:
            assert headers.get("sec-ch-ua-platform") == '"Windows"'
            return
    pytest.skip("Desktop Windows Chrome/Edge UA not selected in 500 draws")


import pytest  # noqa: E402 — used only by skip sentinel above
