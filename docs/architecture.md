# Architecture

> Stub. Fleshed out incrementally as features land. The authoritative target
> state lives in [`SPEC.md`](../SPEC.md); this document describes what is
> *implemented* in the repo right now.

## Current state

What exists in the repo today:

- FastAPI app at `src/something_really_bot/main.py`:
  - `GET /health` returns `{"status": "healthy"}` for Cloud Run probes
    (`/healthz` is reserved by Google Frontend on `*.run.app`).
  - `POST /webhook` validates the Telegram secret header (#12), parses
    the body via `telegram.parser.parse_update` (#13), then dispatches
    to a matching handler via the routing `Dispatcher` (#14). Always
    acks 200, including on parse failures or handler exceptions.
- Routing layer (`routing/`): `Dispatcher` class + `Handler` protocol +
  `BotContext` / `HandlerResult` types. First-match-wins; handler
  exceptions are captured, not raised. Multi-bot identifier flows through
  the context.
- Telegram parser (`telegram/`): typed Pydantic models, two-level
  discriminated union (chat type → content kind). 14 unsupported update
  types short-circuit to `UnsupportedUpdate`.
- Example handler (`features/example/`): `PingHandler` responds `pong` to
  `/ping`. Will be deleted once real handlers exist.
- Config (`config.py`): pydantic-settings `Settings` reading env vars.
  `TELEGRAM_WEBHOOK_SECRET` is the only required field today; others are
  declared but optional pending their respective issues.
- Full Terraform foundation under `infra/terraform/` for the GCP side.
- GitHub Actions CI + OIDC-authenticated deploy workflow under
  `.github/workflows/`. The deploy workflow injects
  `TELEGRAM_WEBHOOK_SECRET` into Cloud Run via Secret Manager.

The pre-May 2026 Python 3.9 / Flask / App Engine implementation is in git history (everything pre-#11) if migration reference is needed.

## Layer boundaries (target)

The package tree under `src/something_really_bot/` reserves these boundaries
ahead of implementation. Each layer is replaced as its issue lands.

| Layer | Module | Issue introducing real logic |
| --- | --- | --- |
| Web / API | `main.py` | #10 (hello-world), #12 (secret header) |
| Telegram client + parser | `telegram/` | #12, #13 |
| Routing / dispatcher | `routing/` | #14 |
| Feature handlers | `features/` | #15, #16, #20, #23 … |
| BigQuery persistence | `persistence/` | #17 (RFC — [0001-bigquery-schema](decisions/0001-bigquery-schema.md)), #18 (implementation) |
| GCS file storage | `file_storage/` | #19 (RFC — [0002-async-file-processing](decisions/0002-async-file-processing.md)), #20 (implementation) |
| Cross-feature services | `services/` | as needed |
| Config / secrets | `config.py` | #12 onward |
| Logging | `logging.py` | TBD |

## Infrastructure

Terraform-managed Cloud Run deployment lands in #8. CI/CD via GitHub Actions
OIDC lands in #9. Until then, the Dockerfile alone proves the image builds.

## Persistence

BigQuery dataset `something_bot` (location `EU`) holds five tables:
`telegram_updates_raw`, `telegram_messages`, `telegram_files`,
`bot_responses`, `processing_events`. Time-partitioned by event date,
clustered by `bot_id` + the most-selective discriminator per table. Full
column list and migration policy: [decisions/0001-bigquery-schema.md](decisions/0001-bigquery-schema.md).

Resources are defined in `infra/terraform/bigquery.tf`. The Cloud Run
runtime SA holds `roles/bigquery.dataEditor` on the dataset.

The Python interface is `persistence.PersistenceService` (Protocol).
Concrete implementation: `persistence.bigquery.BigQueryPersistence`, which
streams rows via `insert_rows_json`. Every persistence call is best-effort
— exceptions and per-row partial failures are logged but never propagate,
so the webhook keeps returning 200 to Telegram regardless of BigQuery
health (SPEC §6.9).

### Webhook flow

Per request, `POST /webhook` runs:

1. Parse the JSON body (`parse_update`). Malformed payloads short-circuit
   with a `malformed_update` event row.
2. Persist the raw payload to `telegram_updates_raw`.
3. Build and persist a `telegram_messages` row (and, for photo / document
   / voice content, a `telegram_files` row with `download_status="pending"`).
4. Dispatch to the matching handler. Handlers are pure — they return a
   `HandlerResult` with optional `reply_text` and never call out to
   Telegram or BigQuery directly.
5. If `reply_text` is set, the webhook sends it via `TelegramClient` and
   writes a row to `bot_responses` (success or failure either way).
6. Errored or unhandled outcomes emit a `processing_events` row.
7. Return `200 {"status": "ok"}`.

## OpenAI fallback (#23)

For private text messages from QA users that no other handler claimed,
`OpenAIFallbackHandler` (`features/openai_fallback/`) calls the OpenAI
chat completions API and replies with the response. The system prompt
lives in `services/openai_client.py::SYSTEM_PROMPT` and is intentionally
short and neutral; persistent conversation context is post-MVP (#26).

Defaults: `gpt-4o-mini`, 25s timeout (the Cloud Run request timeout is
60s, leaving headroom for the orchestrator's persistence work).
Failures (timeouts, rate limits, no API key) degrade gracefully — the
handler returns a deterministic apology reply and sets `HandlerResult.error`
so the webhook records a `handler_errored` event in `processing_events`.

### Handler precedence

`HelloWorldHandler` (#15) is still registered but is **gated** behind
`settings.hello_world_mode` (env var `HELLO_WORLD_MODE`, default
`false`). With the default, HelloWorld silently doesn't match and the
OpenAI fallback runs. Flipping `HELLO_WORLD_MODE=true` in a Cloud Run
revision restores the old parrot behaviour — useful as a quick
degraded-mode escape if the OpenAI API is broken.

## Scheduled jobs (#22)

Cron-style jobs run via Cloud Scheduler hitting `POST /jobs/{name}` on
Cloud Run. The route is OIDC-protected:
`services/scheduler_auth.py` verifies that the incoming `Authorization:
Bearer <jwt>` token is a Google-issued OIDC token whose `email` claim
matches `Settings.scheduler_service_account_email`. Anything else is
401/403. App-level enforcement is the trust anchor because the service
itself is publicly invocable (Telegram webhook needs unauth POST).

### Adding a new scheduled job

Two changes, both small:

1. **Python.** Implement a class that satisfies
   `services.jobs.JobHandler` and register it on the module-level
   `job_registry` in `main.py` (or via `build_default_job_registry`).
2. **Terraform.** Add one entry to `local.scheduled_jobs` in
   `infra/terraform/scheduler.tf` with `schedule`, `timezone`,
   `target_path`, and `description`. `terraform apply` creates the
   `google_cloud_scheduler_job` and points it at the right Cloud Run
   URL with the right OIDC SA.

Issues #24 (tiktok-reminder) and #25 (finco-daily-stats) are the
first two consumers of this pattern. As of #24, the registry contains
the `TikTokReminderJob` (Friday 11:00 Europe/Amsterdam → Irindica).

## File storage (#20)

Private-chat photo / document / voice uploads are mirrored into GCS bucket
`something-bot-telegram-files`. The `FileStorageHandler` matches those
updates and hands them to a `FileFetcher`; the default `InlineFileFetcher`
runs the download as an `asyncio.create_task` after the webhook has
already returned 200. Decision record:
[decisions/0002-async-file-processing.md](decisions/0002-async-file-processing.md).

Background task per upload:

1. `getFile` → resolve Telegram's file path.
2. Download bytes from Telegram's CDN.
3. Upload to GCS under key
   `telegram/{bot_id}/{chat_id}/{YYYY-MM-DD}/{file_unique_id}__{filename}`.
4. Append a `telegram_files` completion row with `download_status="success"`
   and the `gs://...` URI — or `"failed"` plus the captured error.

Group / supergroup / channel file uploads get the same intake `telegram_files`
pending row, but no download (SPEC §6.3 — bot only acts in private chats).

For background tasks to outlive the 200 response, the Cloud Run container
runs with `cpu_idle = false` (declared in `infra/terraform/main.tf`).

## Conventions

- Python 3.12, pinned via `.python-version` and `requires-python` in
  `pyproject.toml`.
- Dependency management: `uv` (lockfile committed).
- Lint / format: `ruff` (configured in `pyproject.toml`).
- Tests: `pytest`. External services (Telegram, BigQuery, GCS, Secret Manager)
  must be mocked.
- Enforcement: CI workflow gates merges on `ruff format --check`,
  `ruff check`, and `pytest`. Pre-commit hooks are intentionally not used.
