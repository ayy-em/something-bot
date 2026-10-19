"""Sticker transform: fit → cut out → outline (#44).

Pipeline, in order:

1. **Fit.** Scale the source (up *or* down, aspect ratio preserved) so
   its longer edge is ``MAX_DIMENSION - 2 * OUTLINE_PX``. The outline in
   step 3 adds ``OUTLINE_PX`` on every side, so the final PNG lands at
   exactly ``MAX_DIMENSION`` on its longer edge, which is what Telegram's
   sticker uploader wants (one side 512 px, the other ≤ 512 px).
2. **Cut out.** rembg with :data:`REMBG_MODEL` replaces the background
   with transparency.
3. **Outline.** The alpha mask is dilated by a disk of ``OUTLINE_PX``
   radius and filled white underneath the cut-out — the classic sticker
   border.

rembg is imported lazily: it drags in onnxruntime, scipy, scikit-image
and OpenCV (~240 MB resident), and only this feature needs it.
"""

import asyncio
import io
import threading
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np
from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError

from something_really_bot.logging import get_logger

_logger = get_logger(__name__)

MAX_DIMENSION = 512
OUTLINE_PX = 4
FIT_DIMENSION = MAX_DIMENSION - 2 * OUTLINE_PX
_OUTLINE_ALPHA_THRESHOLD = 128

# ``u2netp`` rather than rembg's default ``u2net``: the full model peaks
# at ~950 MB RSS on its own (measured, CPU arena off), which does not fit
# next to the app on the 1 GiB Cloud Run instance. ``u2netp`` peaks at
# ~550 MB with visibly similar cut-outs on portraits and objects. Pinned,
# not a setting: the Dockerfile bakes exactly this model into the image,
# so changing it is a code + image change either way.
REMBG_MODEL = "u2netp"

# One inference at a time. Each run allocates a few hundred MB of
# activations; two concurrent stickers on a 1 GiB instance is an OOM.
_REMBG_LOCK = threading.Lock()

BackgroundRemover = Callable[[Image.Image], Image.Image]


class StickerTransformError(Exception):
    """Raised when an input image can't be turned into a sticker PNG."""


class NoSubjectError(StickerTransformError):
    """Raised when background removal leaves nothing behind."""


@dataclass(frozen=True)
class StickerImage:
    """Output of :func:`transform`."""

    png_bytes: bytes
    width: int
    height: int


async def transform(
    source_bytes: bytes,
    *,
    remove_background: BackgroundRemover | None = None,
) -> StickerImage:
    """Turn ``source_bytes`` into a 512 px outlined sticker PNG.

    Runs on an executor thread so the FastAPI event loop stays free
    (Pillow and onnxruntime are both sync C/C++ under the hood).

    Args:
        source_bytes: Any image Pillow can decode.
        remove_background: Override for the rembg call; tests pass a
            stub so they don't need the ONNX model on disk.

    Raises:
        NoSubjectError: rembg found no foreground at all.
        StickerTransformError: source can't be decoded as an image, or
            any other failure along the pipeline.
    """
    remover = remove_background or _rembg_remove
    try:
        return await asyncio.to_thread(_transform_sync, source_bytes, remover)
    except StickerTransformError:
        raise
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "sticker_transform_failed",
            extra={"exception_type": type(exc).__name__},
        )
        raise StickerTransformError(str(exc)) from exc


def _transform_sync(source_bytes: bytes, remove_background: BackgroundRemover) -> StickerImage:
    try:
        image = Image.open(io.BytesIO(source_bytes))
        # JPEG-only shortcut: decode at a reduced scale when the source is
        # far bigger than we need, so a 50 MP upload doesn't cost 200 MB
        # of RGBA before we shrink it anyway. No-op for other formats.
        image.draft(None, (FIT_DIMENSION * 2, FIT_DIMENSION * 2))
        image.load()
    except UnidentifiedImageError as exc:
        raise StickerTransformError("Input is not a recognized image format.") from exc

    # Uncompressed uploads (sent as a file) keep the camera's EXIF
    # orientation; without this, phone portraits come out sideways.
    image = ImageOps.exif_transpose(image)

    fitted = _fit(image.convert("RGBA"), FIT_DIMENSION)
    with _REMBG_LOCK:
        cutout = remove_background(fitted).convert("RGBA")
    if cutout.getchannel("A").getbbox() is None:
        raise NoSubjectError("Background removal left no foreground.")

    sticker = _add_outline(cutout, OUTLINE_PX)

    buffer = io.BytesIO()
    sticker.save(buffer, format="PNG", optimize=True)
    return StickerImage(
        png_bytes=buffer.getvalue(),
        width=sticker.width,
        height=sticker.height,
    )


def _fit(image: Image.Image, target: int) -> Image.Image:
    """Scale so the longer edge is exactly ``target``; aspect ratio kept."""
    width, height = image.size
    scale = target / max(width, height)
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    if size == image.size:
        return image
    return image.resize(size, Image.Resampling.LANCZOS)


def _add_outline(image: Image.Image, radius: int) -> Image.Image:
    """Pad by ``radius`` and lay a white, ``radius``-px border under the subject."""
    canvas = Image.new("RGBA", (image.width + 2 * radius, image.height + 2 * radius))
    canvas.paste(image, (radius, radius))

    # Threshold first: rembg's masks are feathered, and dilating the soft
    # alpha as-is yields a grey halo rather than a crisp white border.
    alpha = np.asarray(canvas.getchannel("A"))
    mask = np.where(alpha >= _OUTLINE_ALPHA_THRESHOLD, 255, 0).astype(np.uint8)
    height, width = mask.shape
    padded = np.pad(mask, radius)
    dilated = np.zeros_like(mask)
    # Max over every offset inside a disk = morphological dilation with a
    # round brush, so corners of the outline come out round, not square.
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx * dx + dy * dy > radius * radius:
                continue
            window = padded[radius + dy : radius + dy + height, radius + dx : radius + dx + width]
            np.maximum(dilated, window, out=dilated)

    outline = Image.new("RGBA", canvas.size, (255, 255, 255, 0))
    # A sub-pixel blur anti-aliases the stair-stepped binary edge.
    outline.putalpha(Image.fromarray(dilated).filter(ImageFilter.GaussianBlur(0.75)))
    outline.alpha_composite(canvas)
    return outline


def _rembg_remove(image: Image.Image) -> Image.Image:
    from rembg import remove

    return remove(image, session=_rembg_session())


@lru_cache(maxsize=1)
def _rembg_session() -> Any:
    """Load :data:`REMBG_MODEL` once per process.

    Built by hand rather than via ``rembg.new_session`` because that
    helper doesn't let us pass our own ``SessionOptions``, and onnxruntime's
    CPU memory arena is what pushes peak RSS past the instance limit: it
    keeps every activation buffer it ever allocated. Off, steady-state
    RSS stays flat across runs.
    """
    import onnxruntime as ort
    from rembg.sessions import sessions_class

    session_class = next(c for c in sessions_class if c.name() == REMBG_MODEL)
    options = ort.SessionOptions()
    options.enable_cpu_mem_arena = False
    return session_class(REMBG_MODEL, options)
