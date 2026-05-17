"""URL detection for the Reels & TikToks downloader (#42).

Matches the supported short-video URL shapes from the body of a Telegram
message. Returns the first match per message; multi-URL messages aren't
supported in v1 (the feature issue lists it as an open question — start
narrow). yt-dlp itself is the source of truth for whether a URL is
actually downloadable; this regex is only the trigger gate so we don't
spin up a yt-dlp invocation for every unrelated message.
"""

import re
from dataclasses import dataclass
from typing import Literal

Platform = Literal["instagram", "tiktok"]


@dataclass(frozen=True)
class DetectedVideo:
    """One supported video URL extracted from message text."""

    url: str
    platform: Platform


_INSTAGRAM_PATTERN = re.compile(
    r"https?://(?:www\.)?instagram\.com/reels?/[A-Za-z0-9_-]+/?(?:\?[^\s]*)?",
    re.IGNORECASE,
)

# TikTok has three URL shapes in the wild:
#   1. tiktok.com/@<user>/video/<id>            — full-form share
#   2. (vm|vt).tiktok.com/<short>               — share-sheet short link
#   3. tiktok.com/t/<short>                     — alternate short link
_TIKTOK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"https?://(?:www\.)?tiktok\.com/@[A-Za-z0-9_.-]+/video/\d+(?:\?[^\s]*)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"https?://(?:vm|vt)\.tiktok\.com/[A-Za-z0-9]+/?(?:\?[^\s]*)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"https?://(?:www\.)?tiktok\.com/t/[A-Za-z0-9]+/?(?:\?[^\s]*)?",
        re.IGNORECASE,
    ),
)


def detect(text: str) -> DetectedVideo | None:
    """Return the first supported video URL found in ``text``, or ``None``."""
    ig = _INSTAGRAM_PATTERN.search(text)
    if ig is not None:
        return DetectedVideo(url=ig.group(0), platform="instagram")
    for pattern in _TIKTOK_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            return DetectedVideo(url=match.group(0), platform="tiktok")
    return None
