# SomethingBot for Telegram

Talk to me at https://t.me/SomethingReallyBot at any time, day and night, baby, day and night.

## Status

Mid-rebuild. The bot is being re-implemented from scratch as a Python 3.12 / FastAPI service running on Google Cloud Run. The authoritative target state is [`SPEC.md`](./SPEC.md); incremental progress is tracked via GitHub issues.

The legacy Python 3.9 / Flask / App Engine implementation has been removed; references to specific legacy features live in the migration issues (#21–#27). Anything not yet re-implemented is broken on the live bot until the corresponding feature issue lands.

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

- `GET /healthz` → `{"status": "healthy"}` (Cloud Run liveness probe).
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
curl http://localhost:8080/healthz                                          # -> {"status":"healthy"}

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

### Required GitHub repo secrets

| Secret | Value (from `infra/terraform/` outputs) |
| --- | --- |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `terraform output -raw workload_identity_provider` |
| `GCP_DEPLOYER_SERVICE_ACCOUNT` | `terraform output -raw deployer_service_account_email` |

No long-lived JSON service-account keys. The legacy `GOOGLE_APPLICATION_CREDENTIALS` secret retires after the first successful OIDC deploy.

### One-time setup checklist (manual)

Before the first deploy can succeed, you need to have already done the steps below — the workflow files themselves are inert until then.

1. Apply the Terraform from `infra/terraform/` (see [`infra/terraform/README.md`](./infra/terraform/README.md) for the bootstrap commands).
2. Read the two outputs above and set them as repo secrets in GitHub.
3. Push to `master` (or merge a PR) to trigger the first deploy.
4. Once Cloud Run reports the new image deployed, run the `Set Telegram webhook` workflow from the Actions tab — but **only after** the `/webhook` route exists (lands in #10), otherwise Telegram will be pointed at a 404.
5. Retire `GOOGLE_APPLICATION_CREDENTIALS` from repo secrets and rotate the underlying GCP service account key.

## Channels you should totally check out

- https://t.me/maymays_unlimited - English - Three memes a day, every day
- https://t.me/vice_news - English - This bot scrapes Vice news and posts a story every day
- https://t.me/rfn_didyouknow - English - "Did you know?" fresh fun facts and interesting trivia
- https://t.me/adam24live - Russian - News, short stories and funny pics about Amsterdam
- https://t.me/ayy_maps - Russian - Are you a fan of maps or a data geek?
