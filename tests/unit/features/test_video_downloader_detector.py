"""URL detection tests for the video downloader (#42)."""

import pytest

from something_really_bot.features.video_downloader.detector import detect


@pytest.mark.parametrize(
    "text",
    [
        "https://www.instagram.com/reel/CxYzAbC1234/",
        "https://instagram.com/reel/CxYzAbC1234",
        "https://www.instagram.com/reels/CxYzAbC1234/",
        "Check this out: https://www.instagram.com/reel/CxYzAbC1234/?igsh=abc trailing",
    ],
)
def test_detects_instagram_reel(text: str) -> None:
    result = detect(text)
    assert result is not None
    assert result.platform == "instagram"
    assert "instagram.com" in result.url


@pytest.mark.parametrize(
    "text",
    [
        "https://www.tiktok.com/@somebody/video/7392842828234",
        "https://vm.tiktok.com/ZGabc12/",
        "https://vt.tiktok.com/ZGabc12",
        "https://www.tiktok.com/t/abcd1234/",
        "look at this https://www.tiktok.com/@user.name/video/7392842828234?_t=xyz fire 🔥",
    ],
)
def test_detects_tiktok(text: str) -> None:
    result = detect(text)
    assert result is not None
    assert result.platform == "tiktok"
    assert "tiktok.com" in result.url


@pytest.mark.parametrize(
    "text",
    [
        "",
        "hello there",
        "https://example.com/something",
        "https://twitter.com/x/status/123",
        "https://instagram.com/profile/user/",  # profile, not a reel
        "https://tiktok.com/discover/funny",  # not a video URL
    ],
)
def test_no_match(text: str) -> None:
    assert detect(text) is None


def test_instagram_wins_when_both_present() -> None:
    text = "https://www.instagram.com/reel/CxYzAbC1234/ https://www.tiktok.com/@user/video/123"
    result = detect(text)
    assert result is not None
    assert result.platform == "instagram"
