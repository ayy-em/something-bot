"""yt-dlp wrapper for the video downloader (#42).

Runs yt-dlp in a thread (the SDK is synchronous) and constrains its
behavior to the v1 scope: a single best-quality MP4 under
``MAX_FILE_SIZE_BYTES``, no playlists, no live streams, no IG cookies.

If yt-dlp fails for any reason — TikTok rate-limit, IG private post,
expired link, network — we funnel everything to
:class:`VideoDownloadError` so the caller can map error classes to a
user-facing message without poking inside yt-dlp internals.
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path

from something_really_bot.features.video_downloader.http_headers import get_random_headers
from something_really_bot.logging import get_logger

_logger = get_logger(__name__)

# Telegram bot API caps `sendVideo` uploads at 50 MiB. We hand yt-dlp a
# filter that drops formats above the cap so we don't even download a
# video we couldn't send.
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024


class VideoDownloadError(Exception):
    """Raised on any yt-dlp failure (auth, transport, parse, size limit)."""


class VideoTooLargeError(VideoDownloadError):
    """Best available format exceeds :data:`MAX_FILE_SIZE_BYTES`."""


@dataclass(frozen=True)
class DownloadedVideo:
    """File + metadata returned by the downloader."""

    path: Path
    size_bytes: int
    duration_seconds: float | None
    width: int | None
    height: int | None
    ext: str
    title: str | None


async def download(url: str, *, output_dir: Path) -> DownloadedVideo:
    """Download ``url`` into ``output_dir`` and return a :class:`DownloadedVideo`.

    Raises:
        VideoTooLargeError: best format above the Telegram 50 MiB ceiling.
        VideoDownloadError: any other yt-dlp / network failure.
    """
    try:
        return await asyncio.to_thread(_download_sync, url, output_dir)
    except VideoDownloadError:
        raise
    except Exception as exc:  # noqa: BLE001 — funnel everything yt-dlp raises
        _logger.warning(
            "yt_dlp_failed",
            extra={"url": url, "exception_type": type(exc).__name__},
        )
        raise VideoDownloadError(str(exc)) from exc


def _download_sync(url: str, output_dir: Path) -> DownloadedVideo:
    # Deferred import: yt-dlp loads a fair amount at import time; we only
    # want that cost on the cold path that actually runs a download.
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError

    output_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(output_dir / "%(id)s.%(ext)s")

    headers = get_random_headers()

    ydl_opts = {
        "outtmpl": outtmpl,
        "format": (
            # Prefer a self-contained MP4 under the size cap; fall back to
            # bestvideo+bestaudio merged into MP4 if no single stream fits.
            f"best[ext=mp4][filesize<{MAX_FILE_SIZE_BYTES}]"
            f"/best[filesize<{MAX_FILE_SIZE_BYTES}]"
            "/bestvideo+bestaudio/best"
        ),
        "merge_output_format": "mp4",
        "max_filesize": MAX_FILE_SIZE_BYTES,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "no_color": True,
        "restrictfilenames": True,
        "http_headers": headers,
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except DownloadError as exc:
        raise VideoDownloadError(str(exc)) from exc

    if info is None:
        raise VideoDownloadError("yt-dlp returned no info dict")

    # When yt-dlp resolves a playlist-like URL we asked it to treat as a
    # single video, it may still return a list under `entries`. v1 is
    # single-video only.
    if "entries" in info and info["entries"]:
        info = info["entries"][0]

    filepath_str = info.get("filepath") or info.get("_filename")
    if not filepath_str:
        # yt-dlp's `requested_downloads` is the canonical place to find the
        # output path after merge.
        downloads = info.get("requested_downloads") or []
        if downloads and "filepath" in downloads[0]:
            filepath_str = downloads[0]["filepath"]
    if not filepath_str:
        raise VideoDownloadError("yt-dlp completed but no output path was reported")

    path = Path(filepath_str)
    if not path.exists():
        raise VideoDownloadError(f"yt-dlp reported {filepath_str} but file is missing")

    size = path.stat().st_size
    if size > MAX_FILE_SIZE_BYTES:
        # yt-dlp respects max_filesize during download but post-merge can
        # exceed it; reject up front so the caller can send a clean error.
        path.unlink(missing_ok=True)
        raise VideoTooLargeError(
            f"video is {size / (1024 * 1024):.1f} MiB; Telegram cap is "
            f"{MAX_FILE_SIZE_BYTES / (1024 * 1024):.0f} MiB"
        )

    return DownloadedVideo(
        path=path,
        size_bytes=size,
        duration_seconds=_to_float(info.get("duration")),
        width=_to_int(info.get("width")),
        height=_to_int(info.get("height")),
        ext=path.suffix.lstrip(".") or "mp4",
        title=info.get("title"),
    )


def _to_float(raw: object) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _to_int(raw: object) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None
