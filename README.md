# SomethingBot for Telegram

Talk to me at https://t.me/SomethingReallyBot at any time, day and night, baby, day and night.

## Status

Live. The bot is a Python 3.12 / FastAPI service on Google Cloud Run. The authoritative target state is [`SPEC.md`](./SPEC.md); incremental progress is tracked via GitHub issues.

Pre-May 2026 the bot was a Python 3.9 / Flask app on Google App Engine with cron jobs; that implementation has been fully removed.

## Tech stack

- Python 3.12
- FastAPI + Uvicorn
- Google Cloud:
  - Cloud Run (region `europe-west4`) via Docker
  - Terraform for IaC
  - GitHub Actions CI/CD (OIDC, no long-lived keys)
  - Google Cloud Storage for received files
  - BigQuery for raw + normalized message persistence
  - Google Secret Manager for secrets
- [`uv`](https://docs.astral.sh/uv/) for dependency management
- [`ruff`](https://docs.astral.sh/ruff/) for lint + format
- [`pytest`](https://docs.pytest.org/) for tests

## Local development

```bash
uv sync                       # install runtime + dev deps from uv.lock
uv run pytest                 # run the test suite
uv run ruff check             # lint
uv run ruff format            # auto-format
uv run ruff format --check    # CI-friendly format check
uv run uvicorn something_really_bot.main:app --reload   # serve locally on :8000
```

The FastAPI shell currently exposes two routes:

- `GET /health` → `{"status": "healthy"}` (Cloud Run liveness probe; `/healthz` is reserved by Google Frontend on `*.run.app`).
- `POST /webhook` → `{"status": "ok"}` (hello-world; accepts any payload, no
  validation — Telegram secret-header check + parsing land in #12 / #13).

The deployed Cloud Run URL is whatever `gcloud run services list --region=europe-west4`
reports for `something-really-bot-cloudrun`.

## Docker

`TELEGRAM_WEBHOOK_SECRET` is required at startup; the app crashes with a clear Pydantic `ValidationError` if it's missing. Any value works for local smoke tests.

```bash
docker build -t something-really-bot:dev .
docker run --rm -p 8080:8080 \
  -e PORT=8080 \
  -e TELEGRAM_WEBHOOK_SECRET=local-dev-secret \
  something-really-bot:dev

# Liveness probe (no header needed):
curl http://localhost:8080/health                                           # -> {"status":"healthy"}

# Webhook with correct secret:
curl -X POST http://localhost:8080/webhook \
  -H 'content-type: application/json' \
  -H 'X-Telegram-Bot-Api-Secret-Token: local-dev-secret' \
  -d '{}'                                                                   # -> 200 {"status":"ok"}

# Without the header / with wrong header:
curl -X POST http://localhost:8080/webhook -d '{}'                          # -> 401
curl -X POST http://localhost:8080/webhook -H 'X-Telegram-Bot-Api-Secret-Token: nope' -d '{}'  # -> 403
```

## Layout

See [`docs/architecture.md`](./docs/architecture.md) for the package tree and which issue introduces real logic into each layer.

## CI/CD

Three GitHub Actions workflows live under `.github/workflows/`:

| Workflow | Trigger | What it does |
| --- | --- | --- |
| `ci.yml` | PR to `master` | Runs `ruff format --check`, `ruff check`, `pytest`, `terraform fmt -check`, `terraform validate` via the reusable `_checks.yml`. |
| `deploy.yml` | Push to `master` (markdown/docs paths ignored) | Re-runs the checks, then builds the Docker image, pushes both `${{ github.sha }}` and `latest` tags to Artifact Registry, then `gcloud run deploy` to Cloud Run. Auth via OIDC / Workload Identity Federation only. |
| `set-telegram-webhook.yml` | `workflow_dispatch` only | Manually points the chosen bot's Telegram webhook at the currently-deployed Cloud Run service. Bot token + webhook secret read from Secret Manager at runtime and masked. |
| `daily-weather-qa.yml` | `workflow_dispatch` only | Triggers the `daily-message-qa` job to send the daily message as a DM to JM for QA. Authenticates via WIF as the deployer SA. |

### Required GitHub repo secrets

| Secret | Value (from `infra/terraform/` outputs) |
| --- | --- |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `terraform output -raw workload_identity_provider` |
| `GCP_DEPLOYER_SERVICE_ACCOUNT` | `terraform output -raw deployer_service_account_email` |

No long-lived JSON service-account keys. WIF-only.

### One-time setup checklist (manual)

Before the first deploy can succeed, you need to have already done the steps below — the workflow files themselves are inert until then.

1. Apply the Terraform from `infra/terraform/` (see [`infra/terraform/README.md`](./infra/terraform/README.md) for the bootstrap commands).
2. Read the two outputs above and set them as repo secrets in GitHub.
3. Push to `master` (or merge a PR) to trigger the first deploy.
4. Once Cloud Run reports the new image deployed, run the `Set Telegram webhook` workflow from the Actions tab.

## Bot features

### Commands (DM / group)

| Command | Description |
| --- | --- |
| `/start` | Welcome message + feature list |
| `/help` | Show available commands |
| `/next-reunion [YYYY-MM-DD]` | Set or view the next reunion date |
| `/dutch` | Translate Dutch text to English |
| `/make-sticker` | Convert an image to sticker-ready PNG |
| `/ocr` | Extract text from an image |
| `/summarize` | TL;DR a document |

### Scheduled jobs

| Job | Schedule | Description |
| --- | --- | --- |
| `daily-message` | 05:05 UTC daily | Weather (Amsterdam + Moscow), reunion countdown, EUR/RUB rate, "this day in history"; weekly website stats on Fridays |
| `daily-message-qa` | On demand (GitHub Actions) | Same as above, sent as DM to JM for QA |
| `tiktok-reminder` | Fridays 11:00 CET | Friday TikTok reminder |

### Passive features

- **Video downloader** — auto-downloads TikTok/Reels links shared in chat
- **Voice transcription** — transcribes voice messages via OpenAI Whisper
- **File storage** — uploads photos/documents to GCS
- **OpenAI fallback** — replies to unmatched text via GPT-4o-mini

## Channels you should totally check out

- https://t.me/maymays_unlimited - English - Three memes a day, every day
- https://t.me/vice_news - English - This bot scrapes Vice news and posts a story every day
- https://t.me/rfn_didyouknow - English - "Did you know?" fresh fun facts and interesting trivia
- https://t.me/adam24live - Russian - News, short stories and funny pics about Amsterdam
- https://t.me/ayy_maps - Russian - Are you a fan of maps or a data geek?
