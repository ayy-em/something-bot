# syntax=docker/dockerfile:1.7

# ---------- builder ----------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---------- runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PORT=8080

# ffmpeg is required by yt-dlp post-processing for the video downloader
# (#42): Reels/TikToks often arrive as separate audio + video streams
# that ffmpeg muxes into a single MP4.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 1001 app \
 && useradd  --system --uid 1001 --gid app --home /app --shell /usr/sbin/nologin app

WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv

# Background-removal model for /make_sticker (#44). rembg would otherwise
# download it from GitHub on the first sticker after every cold start, and
# a GitHub hiccup would break the feature. Must match REMBG_MODEL in
# features/make_sticker/transform.py (a unit test checks). pooch writes
# the file 0600 as root, hence the chown.
ENV U2NET_HOME=/app/.u2net
RUN python -c "from rembg.sessions.u2netp import U2netpSession; U2netpSession.download_models()" \
 && chown -R app:app /app/.u2net

COPY --chown=app:app src ./src

USER app

EXPOSE 8080

CMD ["sh", "-c", "exec uvicorn something_really_bot.main:app --host 0.0.0.0 --port ${PORT}"]
