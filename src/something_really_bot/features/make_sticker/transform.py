"""Pillow-based sticker transform (#44).

Telegram sticker requirements that matter for v1:

* PNG output (alpha-capable).
* Max 512×512 px on either side.
* Aspect ratio preserved.
* Transparent background where the source already has alpha.

The transform does NOT perform automatic background removal — sources
without alpha keep their existing background. Filed as backlog if/when
we want rembg / OpenAI image edit involvement.
"""

import asyncio
import io
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

from something_really_bot.logging import get_logger

_logger = get_logger(__name__)

MAX_DIMENSION = 512


class StickerTransformError(Exception):
    """Raised when an input image can't be turned into a sticker PNG."""


@dataclass(frozen=True)
class StickerImage:
    """Output of :func:`transform`."""

    png_bytes: bytes
    width: int
    height: int


async def transform(source_bytes: bytes) -> StickerImage:
    """Resize ``source_bytes`` into a Telegram-sticker-shaped PNG.

    Runs Pillow on an executor thread so the FastAPI event loop stays
    free (Pillow is a sync C library under the hood).

    Raises:
        StickerTransformError: source can't be decoded as an image, or
            any other Pillow-level failure.
    """
    try:
        return await asyncio.to_thread(_transform_sync, source_bytes)
    except StickerTransformError:
        raise
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "sticker_transform_failed",
            extra={"exception_type": type(exc).__name__},
        )
        raise StickerTransformError(str(exc)) from exc


def _transform_sync(source_bytes: bytes) -> StickerImage:
    try:
        image = Image.open(io.BytesIO(source_bytes))
        image.load()
    except UnidentifiedImageError as exc:
        raise StickerTransformError("Input is not a recognized image format.") from exc

    # Convert to RGBA so the output PNG carries an alpha channel.
    # Sources with existing transparency (RGBA, LA, palette-with-index)
    # keep it; opaque sources get an opaque alpha channel.
    rgba = image.convert("RGBA")

    # Resize so the longer edge equals MAX_DIMENSION; aspect ratio
    # preserved. Pillow's ``thumbnail`` is in-place and only shrinks
    # (never upscales), which matches what we want for stickers — a
    # tiny source remains tiny rather than getting pixelated up.
    rgba.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)

    buffer = io.BytesIO()
    rgba.save(buffer, format="PNG", optimize=True)
    return StickerImage(
        png_bytes=buffer.getvalue(),
        width=rgba.width,
        height=rgba.height,
    )
