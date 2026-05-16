# SomethingBot for Telegram

Talk to me at https://t.me/SomethingReallyBot at any time, day and night, baby, day and night.

## Status

Mid-rebuild. The bot is being re-implemented from scratch as a Python 3.12 /
FastAPI service running on Google Cloud Run. The authoritative target state is
[`SPEC.md`](./SPEC.md); incremental progress is tracked via GitHub issues.

Legacy Python 3.9 / Flask / App Engine code still lives at the repo root for
reference and is removed in issue #11.

## Tech stack (target)

- Python 3.12
- FastAPI + Uvicorn
- Google Cloud Run (region `europe-west4`)
- Docker
- Terraform
- GitHub Actions CI/CD (OIDC, no long-lived keys)
- [`uv`](https://docs.astral.sh/uv/) for dependency management
- [`ruff`](https://docs.astral.sh/ruff/) for lint + format
- [`pytest`](https://docs.pytest.org/) for tests
- BigQuery for raw + normalized message persistence
- Google Cloud Storage for received files
- Google Secret Manager for secrets

## Local development

```bash
uv sync                       # install runtime + dev deps from uv.lock
uv run pytest                 # run the test suite
uv run ruff check             # lint
uv run ruff format            # auto-format
uv run ruff format --check    # CI-friendly format check
uv run uvicorn something_really_bot.main:app --reload   # serve locally on :8000
```

The FastAPI shell currently exposes one route: `GET /healthz` → `{"status": "ok"}`.
Business logic (Telegram webhook, routing, BigQuery, GCS) is introduced in
subsequent issues.

## Docker

```bash
docker build -t something-really-bot:dev .
docker run --rm -p 8080:8080 -e PORT=8080 something-really-bot:dev
curl http://localhost:8080/healthz   # -> {"status":"ok"}
```

## Layout

See [`docs/architecture.md`](./docs/architecture.md) for the package tree and
which issue introduces real logic into each layer.

## Channels you should totally check out

- https://t.me/maymays_unlimited - English - Three memes a day, every day
- https://t.me/vice_news - English - This bot scrapes Vice news and posts a story every day
- https://t.me/rfn_didyouknow - English - "Did you know?" fresh fun facts and interesting trivia
- https://t.me/adam24live - Russian - News, short stories and funny pics about Amsterdam
- https://t.me/ayy_maps - Russian - Are you a fan of maps or a data geek?
