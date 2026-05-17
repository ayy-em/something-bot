# Something Dashboard Telegram Bot Revamp — Specification & AI Agent Execution Brief

## 1. Purpose

This document defines the specification for **Something Really Bot**, a Telegram personal assistant bot. Pre-May 2026 the bot was a Python 3.9 / Flask app on Google App Engine with Cloud Scheduler-style cron jobs; it has been rebuilt as a Python 3.12 service on Google Cloud Run. The legacy implementation is no longer referenced.

The intended reader is an AI coding agent or maintainer working on the current implementation.

---

## 2. High-Level Goal

Rebuild the existing Telegram bot as a production-ready Python 3.12 Cloud Run service using FastAPI, with a clean repository structure, test coverage for key behavior, Terraform-defined infrastructure, Docker-based deployment, GitHub Actions CI/CD, Telegram webhook support, BigQuery persistence, Google Cloud Storage file storage, and an extensible feature/plugin structure.

The first deploy should be intentionally minimal but structurally sound:

* Receive Telegram webhook updates at `/webhook`.
* Validate Telegram webhook secret header.
* Parse incoming updates.
* Route updates based on type.
* Store incoming updates/messages in BigQuery.
* For authorized QA users only, respond with a simple Hello World-style response and echo/parrot the received text.
* Support `/start` and `/help` with placeholder responses.
* Store files sent directly to the bot in Google Cloud Storage and record metadata in BigQuery.
* Provide a foundation for migrating existing bot features later.

---

## 3. History

The pre-rebuild bot was a Python 3.9 / Flask app on Google App Engine with cron jobs; it was deleted in PR #11. Feature-by-feature migration into the new Cloud Run service is tracked in the umbrella issue #21.

---

## 4. Target State Summary

### 4.1 Platform

* Runtime: Python 3.12
* Web framework: FastAPI
* Hosting: Google Cloud Run service
* Region: `europe-west4`
* Containerization: Docker
* Dependency management: `uv`
* Linting/formatting: `ruff`
* Testing: `pytest`
* CI/CD: GitHub Actions
* Infrastructure as Code: Terraform (all cloud resources defined in code!)
* Secrets: Google Secret Manager
* Persistence: BigQuery
* File storage: Google Cloud Storage

### 4.2 Deployment Model

Cloud Run is the only deployment target. The rebuild lives in this repository; the legacy implementation is gone.

### 4.3 Bot Model

The first implementation must support one Telegram bot token, but the architecture must be designed so that multiple bots can be supported later.

Each future bot may have:

* Its own Telegram token.
* Its own handler logic.
* Its own routing rules.
* Its own configuration.

The initial version only needs one active bot.

---

## 5. Non-Goals / Explicitly Out of Scope for Initial Version

The following are not required for the first implementation:

* Full migration of all legacy bot features.
* Callback query/button handling.
* Edited message handling.
* Telegram reactions.
* Inline queries.
* Payments.
* Polls.
* Stickers.
* Complex admin UI.
* Local development polish.
* Alerting and monitoring beyond basic structured logs.
* Deduplication of files.
* BigQuery schema finalization beyond the minimum required to store raw updates, normalized message metadata, bot responses, and file metadata.
* Production-grade async orchestration for all message types.
* Multiple active bots in production.

These should be documented as future iterations where relevant.

---

## 6. Functional Requirements

## 6.1 Telegram Webhook Endpoint

The Cloud Run service must expose one public endpoint:

```text
POST /webhook
```

This endpoint receives Telegram webhook updates.

No additional public API endpoints are required for the first version unless needed by Cloud Run health checks or operational constraints. If health endpoints are added, they should be minimal and documented.

### Acceptance Criteria

* The service accepts Telegram webhook payloads at `POST /webhook`.
* Unsupported HTTP methods return appropriate errors.
* Invalid payloads are logged and handled safely.
* The endpoint returns within Telegram webhook timeout constraints.
* The endpoint is compatible with Telegram webhook delivery.

---

## 6.2 Telegram Webhook Security

The service must validate Telegram’s webhook secret header:

```text
X-Telegram-Bot-Api-Secret-Token
```

The expected secret value must be stored in Google Secret Manager and injected into the Cloud Run runtime environment.

The webhook URL itself does not need a secret path or secret query parameter.

### Acceptance Criteria

* Requests missing the Telegram secret header are rejected.
* Requests with an invalid Telegram secret header are rejected.
* Requests with a valid Telegram secret header are processed.
* The expected secret is not hardcoded in source code.
* The expected secret is not stored in GitHub repository files.

---

## 6.3 Supported Telegram Update Types — Initial Version

The initial version must support receiving and classifying the following Telegram update types:

* Text messages
* Photo messages
* Document/file messages
* Voice messages
* Channel posts
* Group/supergroup messages

The bot does not need feature-complete behavior for all of them immediately, but it must not crash when receiving them.

### Required Initial Behavior

| Update Type                        | Initial Behavior                                                                          |
| ---------------------------------- | ----------------------------------------------------------------------------------------- |
| Private text message               | Store in BigQuery, route synchronously, respond to QA users                               |
| `/start` command                   | Store in BigQuery, return placeholder response ("Start command received!")                                      |
| `/help` command                    | Store in BigQuery, return placeholder response  ("Help command received!")                                           |
| Photo sent directly to bot         | Store metadata in BigQuery, download file to GCS asynchronously or via async-capable flow |
| Document/file sent directly to bot | Store metadata in BigQuery, download file to GCS asynchronously or via async-capable flow |
| Voice message sent directly to bot | Store metadata in BigQuery, download file to GCS asynchronously or via async-capable flow |
| Group/channel post without file    | Store in BigQuery, no response unless explicitly routed                                   |
| Group/channel post with file       | Store metadata, process file asynchronously where applicable                              |
| Unsupported update type            | Store raw update if possible, log as unsupported, do not crash                            |


**IMPORTANT**: The initial rebuild implies the bot ONLY EVER responds to direct messages in a 1:1 private chat, NOT posting to groupchats/channels/supergroups. not responding nor reacting in them. 

### Explicitly Out of Scope Initially

* Callback queries
* Inline queries
* Edited messages
* Telegram buttons
* Reactions
* Stickers
* Polls
* Payments
* Any advanced Telegram API feature not listed above

### Acceptance Criteria

* The service can safely receive all supported initial update types.
* The service logs and ignores unsupported update types without crashing.
* Text messages from authorized QA users receive the expected response.
* Text messages from non-QA users are stored but do not receive the Hello World/parrot response unless otherwise configured.

---

## 6.4 QA User Authorization

The first deploy must only respond interactively to users whose Telegram user IDs are included in a configured QA allowlist.

The QA user IDs must be stored as a secret or secret-backed configuration, not hardcoded in source code.

Suggested secret/config name:

```text
QA_TELEGRAM_USER_IDS
```

The value may be a comma-separated list of Telegram numeric user IDs.

Example:

```text
123456789,987654321
```

### Initial Response Behavior

For authorized QA users:

* Regular text message: respond with `Hello World` plus the original message text.
* `/start`: respond with a placeholder start message.
* `/help`: respond with a placeholder help message.

For unauthorized users:

* Store the message/update.
* Do not send the Hello World/parrot response.
* Optionally send no response at all.

### Acceptance Criteria

* QA allowlist is externally configurable.
* Unauthorized users do not receive the Hello World/parrot response.
* Authorized QA users receive the expected response.
* QA authorization logic is covered by tests.

---

## 6.5 Text Message Processing

For the initial version, text message processing should be intentionally simple.

### Behavior

When the bot receives a regular text message from an authorized QA user:

```text
Hello World

You said: <original message text>
```

Exact wording can be adjusted during implementation, but the intent must remain clear.

### Acceptance Criteria

* Text is extracted from the Telegram update.
* The original message text is stored in BigQuery.
* The bot sends a response only to authorized QA users.
* The response includes a Hello World message and the original text.

---

## 6.6 Command Handling

The initial version must support these commands:

```text
/start
/help
```

Both should return placeholder messages.

Suggested placeholder responses:

```text
/start → Something Dashboard bot is online. More features coming soon.
/help  → Help is not implemented yet. This bot is being rebuilt.
```

The exact wording is not important. The command routing structure is important.

### Acceptance Criteria

* `/start` is recognized as a command.
* `/help` is recognized as a command.
* Both commands return deterministic placeholder responses.
* Command handling is covered by tests.

---

## 6.7 File Handling

When users send files directly to the bot, the bot must download those files from Telegram and store them in a Google Cloud Storage bucket.

Supported file-like messages for the initial version:

* Photo
* Document/file
* Voice message

The target GCS bucket does not currently exist and must be provisioned through Terraform.

File deduplication is not required.

### Behavior

When a supported file update is received:

1. Store raw update in BigQuery.
2. Extract file metadata from Telegram payload.
3. Request the file path from Telegram API using `getFile`.
4. Download the file from Telegram.
5. Store the file in GCS.
6. Store file metadata and GCS path in BigQuery.
7. Return a successful webhook response quickly enough to avoid Telegram timeout issues.

### Async Requirement

File processing should use an async-capable design.

For the first implementation, the agent should propose the simplest reliable approach. Options may include:

* Processing inline using async functions if expected files are small and usage is personal-scale.
* Offloading file processing to Pub/Sub.
* Offloading file processing to Cloud Tasks.
* Storing the update first and processing the file in a separate worker endpoint or Cloud Run Job.

Because expected volume is small, the simplest reliable implementation is preferred. However, the code structure must not make future async offloading painful. Because apparently even tiny bots eventually become distributed systems if left unattended.

### Acceptance Criteria

* Photo, document, and voice message metadata is extracted.
* Files sent directly to the bot are stored in GCS.
* File metadata is stored in BigQuery.
* The bot does not crash on file messages.
* File handling logic is covered by unit tests with mocked Telegram API and mocked GCS.

---

## 6.8 BigQuery Persistence

The bot must persist incoming Telegram updates and relevant processed metadata to BigQuery.

The GCP project and BigQuery dataset already exist and will be provided separately. The specification should not hardcode final dataset/table names.

The BigQuery table design should be handled as a separate implementation task/RFC.

### Initial Persistence Requirements

The system should persist at least:

* Raw Telegram update JSON.
* Update ID.
* Bot identifier.
* Source type, such as private chat, group, supergroup, or channel.
* Chat ID.
* Chat type.
* User ID, where available.
* Username, where available.
* Message ID, where available.
* Message type.
* Message text, where applicable.
* File metadata, where applicable.
* GCS object path, where applicable.
* Received timestamp.
* Processed timestamp.
* Processing status.
* Error details, where applicable.

### Suggested Table Concepts

The implementation agent should propose a concrete BigQuery schema, but the following conceptual tables are likely useful:

| Table Concept          | Purpose                                  |
| ---------------------- | ---------------------------------------- |
| `telegram_updates_raw` | Raw webhook payloads, mostly append-only |
| `telegram_messages`    | Normalized message-level data            |
| `telegram_files`       | File metadata and GCS object links       |
| `bot_responses`        | Messages sent by the bot                 |
| `processing_events`    | Optional processing status/error log     |

The final schema should be proposed before implementation.

### Privacy

No special privacy constraints are currently required. Store Telegram payloads and extracted fields plainly.

### Expected Volume

Personal usage / very small scale. Design should be clean but not absurdly over-engineered. The bot does not need Big Tech cosplay.

### Acceptance Criteria

* Every received webhook update is stored or at least attempted to be stored in BigQuery.
* Raw update JSON is preserved.
* Normalized metadata is extracted for supported update types.
* BigQuery writes are isolated behind a persistence interface/service.
* BigQuery behavior is covered by tests using mocks.

---

## 6.8.1 Shared Postgres Persistence

Some bot features require relational primitives that BigQuery is a poor fit for (small mutable state, lookups by primary key, ad-hoc joins). For these cases the bot connects to a **shared Cloud SQL Postgres instance** that lives in a separate GCP project, used by multiple internal tools.

All bot tables live under a dedicated schema (default `something_bot`) so the bot never touches other tenants' tables in the shared database.

### Connection Model

The bot does not whitelist Cloud Run egress IPs. Instead it uses the Cloud SQL Auth Proxy:

* The deploy step passes the Cloud SQL instance connection name to `gcloud run deploy --add-cloudsql-instances`, which causes Cloud Run to mount the Auth Proxy unix socket at `/cloudsql/<instance-connection-name>/` in the container filesystem.
* The runtime service account authenticates to the Auth Proxy via IAM (`roles/cloudsql.client` on the project that owns the instance — granted out-of-band because Terraform here only manages the bot's own project).
* The Postgres wrapper overrides psycopg's `host` connection argument with the socket path whenever the instance connection name is present in the runtime environment, so the same DSN works for local TCP development and for socket-based Cloud Run runs.

No long-lived database credentials traverse the network in plaintext; psycopg connects to the local socket and the Auth Proxy handles TLS to Cloud SQL on the bot's behalf.

### Required Secrets

Two Secret Manager secrets in the bot's GCP project drive the wiring:

* A DSN secret holding the bot's database user, password and database name. Host:port in the DSN is ignored at runtime when the socket override is active and is only used for local TCP development.
* An instance connection name secret holding the canonical `project:region:instance` triple. Consumed both at deploy time (to mount the Auth Proxy socket) and at runtime (to point psycopg at the socket).

The runtime service account is granted `roles/secretmanager.secretAccessor` on both. The deployer service account is granted access only to the instance connection name secret, so the deploy workflow can read it on the runner without also being able to read the DSN.

### Database User Model

The bot uses a dedicated database user inside the shared instance with the minimum privileges needed:

* `USAGE` on the schemas it accesses.
* Appropriate DML (`SELECT`, `INSERT`, `UPDATE`, `DELETE`) on those schemas.
* `CREATE` on the database is optional and only required if `ensure_schema()` is expected to create the bot's schema on first run; otherwise the schema is pre-created out-of-band.

The bot must never assume superuser-level privileges and must not attempt cross-tenant access in the shared instance.

### Wrapper Requirements

A single async-friendly wrapper isolates psycopg behind a narrow interface:

* `ensure_schema()` — idempotent `CREATE SCHEMA IF NOT EXISTS` for the bot's schema.
* `execute(sql, params)` — non-returning statement.
* `fetch_all(sql, params)` — returns a list of dicts keyed by column name.
* `insert_row(table, row)` — schema-qualified insert; identifiers are validated against an allowlist of characters before interpolation.

All driver errors funnel into a single exception type so call sites can apply the §6.9 "never bubble to Telegram" rule uniformly. The synchronous psycopg driver runs inside `asyncio.to_thread` so the FastAPI event loop stays free.

### Acceptance Criteria

* Cloud Run routes to Postgres via the Cloud SQL Auth Proxy socket, not over public IP.
* DSN and instance connection name are sourced from Secret Manager and never hardcoded.
* The runtime SA has `roles/cloudsql.client` on the owning project and `secretAccessor` on both secrets; cross-project IAM is documented as a manual step.
* The wrapper exposes a small async API and converts driver failures into a single bot-level exception type.
* Wrapper behavior (including the socket host override) is covered by tests using fakes/mocks — no real DB required.

---

## 6.9 Branching / Routing Logic

The bot must contain clear branching logic for deciding how each update should be handled.

The exact implementation approach is left to the implementation agent, but it must be proposed explicitly before coding.

Possible approaches:

* Dispatcher/router pattern.
* Handler registry by update type.
* Plugin-style feature modules.
* Command-specific handlers.
* Combination of the above.

### Required Design Properties

The routing design must be:

* Easy to understand.
* Easy to test.
* Easy to extend with new bot features.
* Able to support multiple bots later.
* Able to route by update type, command, message content, file type, and source.

### Acceptance Criteria

* There is a central update-processing entrypoint.
* Update classification is separated from handler execution.
* Each handler has narrow responsibility.
* New features can be added without modifying one giant `if/else` disaster snake.
* Routing behavior is covered by tests.

---

## 6.10 Plugin-Like Feature Structure

The repository should support a plugin-like feature structure.

The exact implementation is flexible, but the code should make it natural to add features such as:

* `hello_world`
* `commands`
* `file_storage`
* future migrated legacy features

Suggested concept:

```text
src/something_dashboard_bot/features/
  hello_world/
    handler.py
    tests...
  commands/
    handler.py
  file_storage/
    handler.py
```

or equivalent.

### Acceptance Criteria

* Feature logic is not all crammed into the webhook route.
* Features can be tested independently.
* Features can be enabled/disabled or routed cleanly.
* Future migrated features have an obvious place to live.

---

## 6.11 Video Downloader (#42)

The bot fetches Instagram Reels and TikTok videos that appear as URLs in
incoming text messages (private chats, groups, supergroups) and replies
with the video pinned to the trigger message. This is a webhook-driven
feature, not a scheduled job — see `src/something_really_bot/features/video_downloader/README.md`
for the full flow, error matrix, and Postgres schema.

### URL detection

Matched anywhere in the text:

* Instagram: `instagram.com/reel/<id>`, `instagram.com/reels/<id>`
* TikTok: `tiktok.com/@user/video/<id>`, `vm.tiktok.com/<id>`, `vt.tiktok.com/<id>`, `tiktok.com/t/<id>`

Profile pages and discover pages are intentionally excluded. If both
platforms appear, Instagram wins.

### Download model

`yt-dlp` (sync, run inside `asyncio.to_thread`) downloads to a per-job
tempdir; `ffmpeg` is bundled into the runtime image for muxing. A 50 MiB
`max_filesize` is enforced at the yt-dlp layer because that's Telegram's
`sendVideo` ceiling for bots. Public videos only — private/login-walled
Reels are out of scope.

### Async model

The webhook acks Telegram immediately:

1. Send a short "Link received, fetching the {Instagram|TikTok} video…"
   reply pinned to the trigger message.
2. Stamp a 👀 reaction on the trigger message (best-effort; group chats
   sometimes restrict bot reactions).
3. Schedule the actual download/upload/send as an `asyncio.create_task`
   so the FastAPI webhook returns 200 to Telegram before the heavy
   work starts.

This requires Cloud Run with `cpu-always-on` (i.e. the existing
service-side setting) so the background task isn't paused after the
HTTP response.

### Persistence

One row per attempt in `public.video_download_jobs`. The table lives in
`public` rather than the bot's `something_bot` schema per operator
direction; the storage module bypasses
`PostgresStorage`'s auto-schema-qualification by issuing raw SQL.
Status lifecycle: `pending → downloading → uploading → sending → succeeded`
(or `failed` at any step, with `error_class`/`error_message`).

### Error reporting

All failures map to a single user-visible reply (see feature README for
the matrix). TikTok's anti-scraping is the dominant failure mode, so
the message names the platform explicitly instead of looking like a
generic bot bug.

### Cloud Run resource implications

The Cloud Run service defaults are bumped to 2 vCPU / 2 GiB / 300 s
timeout / concurrency 8 in `infra/terraform/variables.tf` to fit
`yt-dlp` + `ffmpeg` + a 50 MiB upload. The runtime image installs
`ffmpeg` from apt; `yt-dlp` ships as a runtime Python dep.

### Out of scope (backlog)

* Authenticated cookie jar for private Instagram Reels.
* Resumable / chunked uploads for >50 MB sources.
* Re-encoding to squeeze borderline-too-large clips under the limit.

---

## 6.12 Voice Transcription (#43)

The bot transcribes Telegram voice memos in private, group, and
supergroup chats. Each voice memo gets stored in GCS, transcribed via
OpenAI, summarized + emotion-assessed in a single chat call, and
replied to inline. See `src/something_really_bot/features/voice_transcription/README.md`
for the full flow, error matrix, and Postgres schema.

### Trigger

Any `VoiceContent` in `PrivateMessage`, `GroupMessage`, or
`SupergroupMessage`. `FileStorageHandler` no longer matches
`VoiceContent` (it kept photo + document); voice routing is owned by
this feature.

### Caps

* Duration: 10 minutes — voice memos over the cap get a clear rejection
  reply, no background work.
* File size: 25 MB — defensive ceiling matching the OpenAI request
  limit. 10 min of Opus voice is ~3-5 MB in practice.

### Pipeline

1. Download the voice file from Telegram (in-memory bytes).
2. Upload to GCS under
   `voice_transcription_requests/{chat_id}/{message_id}/voice_{file_unique_id}.ogg`.
3. Transcribe via OpenAI `audio.transcriptions.create` with
   `model="gpt-4o-transcribe"`.
4. One `chat.completions.create` call with `response_format=json_object`
   returns `{"summary": "...", "emotion": "..."}`.
5. Reply to the original voice memo with the formatted transcript +
   summary + emotion read.

Webhook acks Telegram immediately after the inline "Transcribing your
voice memo…" reply + 👀 reaction; steps 1–5 run in
`asyncio.create_task`.

### Persistence

`public.voice_transcription_jobs` — one row per attempt with full
lifecycle (`pending → downloading → uploading → transcribing → analyzing
→ sending → succeeded`, or `failed` at any step). Stores transcript,
summary, emotion, GCS path, and Telegram reply message id on success;
`error_class`/`error_message` on failure.

### Error reporting

Every failure mode (download, transcription, analysis, send) maps to a
single user-visible reply. The OpenAI-key-missing case has its own
explicit message so the bot isn't silent if config drifts.

### Out of scope (backlog)

* `/vtt` command for transcribing uploaded audio files (mp3/m4a/wav) in
  DMs — tracked as a separate backlog issue.
* Editing the "Transcribing…" ack in place vs sending two messages —
  current behavior matches the video downloader pattern (two messages).
* Speaker diarization, multi-speaker emotion analysis.

---

## 6.13 Command Workflow State

Command-driven features that take input across multiple turns
(`/dutch`, `/make-sticker`, `/ocr`, `/summarize`) need to remember
"this user just invoked ``/foo`` and their next message is the input."
This is captured in a shared Postgres table `public.pending_user_actions`,
keyed on `(bot_id, chat_id, user_id)`:

| Column            | Notes                                                        |
| ----------------- | ------------------------------------------------------------ |
| `bot_id`          | which bot is in the conversation                             |
| `chat_id`         | originating chat (DM or group)                               |
| `user_id`         | which user is mid-flow                                       |
| `command`         | the registered command name (e.g. `"dutch"`)                 |
| `expected_input`  | `text` \| `image` \| `document` \| `voice`                    |
| `metadata`        | JSONB — feature-specific extras                              |
| `created_at`      | TIMESTAMPTZ                                                  |
| `expires_at`      | TIMESTAMPTZ — 10 minutes by default                          |

PK is `(bot_id, chat_id, user_id)`, so setting a new pending action
atomically replaces any prior one for that user (no orphan rows).
`get()` filters with `expires_at > now()`, so expired rows are simply
ignored (no janitor required).

The webhook orchestrator resolves the pending action *before* dispatch
and puts it on `BotContext.pending_action`; handlers read it
synchronously from `matches()`. After processing, the handler clears
or advances the state via the async store.

Lives in `public` consistent with the convention #42 / #43 settled on
for cross-feature tables.

This is the first slice of #48's "conversation/workflow state" scope.
Other pieces of #48 (shared error mapper, generalized reply helper,
shared test fixtures) remain on the backlog.

---

## 6.14 /dutch — Dutch to English Translation (#47)

Two-turn command that translates Dutch text to English via OpenAI.

* `/dutch <text>` — translate the inline argument immediately.
* `/dutch` alone — prompt the user and wait up to 10 minutes for a
  follow-up text message from the same user (`pending_user_actions`
  TTL). Next text gets translated and the pending row is cleared.

Works in DMs, groups, and supergroups. Reply is the translation only,
italicized, no preamble — `parse_mode="HTML"`, content `html.escape`-d.

See `src/something_really_bot/features/dutch_translation/README.md` for
the error matrix and lifecycle details.

---

## 6.15 /make-sticker — Image to Sticker-Ready PNG (#44)

Two-turn command, **private chats only**, that converts an image into a
Telegram-sticker-shaped PNG.

* `/make-sticker` — sets a pending action expecting an `image`, prompts
  the user to send one.
* Next photo from the same user within 10 minutes — handler downloads
  it, stores the original under `sticker_requests/`, runs the Pillow
  transform (resize to ≤ 512 px on the longer edge, convert to RGBA,
  PNG encode), stores the output under `sticker_outputs/`, and replies
  with `sendDocument` (not `sendPhoto`, to bypass Telegram's
  re-compression).

No automatic background removal in v1 — existing alpha is preserved,
opaque sources stay opaque. rembg / OpenAI image-edit is a backlog
candidate.

`MakeStickerHandler` registers **before** `FileStorageHandler` so that
a photo with a live pending row goes to the sticker pipeline rather
than the generic file-to-GCS dump.

See `src/something_really_bot/features/make_sticker/README.md` for the
transform internals, GCS layout, and error matrix.

---

## 6.16 /ocr — Image OCR (#45)

Two-turn command, **private chats only**. `/ocr` sets a pending action
expecting an image; the next photo from the same user gets stored under
`ocr_requests/`, sent to OpenAI vision (chat.completions with a base64
`image_url` content part), and the extracted text comes back as a
reply in italics. The model returns the sentinel `NO_TEXT` when nothing
readable is found; the handler translates that to a friendly fallback.

Same dispatch precedence story as `/make-sticker`: `OCRHandler`
registers before `FileStorageHandler` so a photo with a live pending
`/ocr` row goes to the OCR pipeline instead of the generic file dump.

See `src/something_really_bot/features/ocr/README.md` for the OCR
internals and error matrix.

---

## 6.17 /summarize — Document TL;DR (#46)

Two-turn command, **private chats only**. `/summarize` sets a pending
action expecting a document; the next document upload from the same
user gets stored under `summarizer/`, run through the right text
extractor (PyMuPDF for PDFs, `python-docx` for DOCX, plain UTF-8
decode for TXT/MD/CSV/log), hard-capped at 60,000 characters, sent to
OpenAI for a 3-6 sentence TL;DR, and replied to in italics. If the
document was truncated, the reply gets an italic truncation notice
appended.

`SummarizeHandler` registers alongside `MakeStickerHandler` and
`OCRHandler` before `FileStorageHandler`; the match guard
(`pending.command == "summarize"`) ensures only one of the three claims
any given upload.

See `src/something_really_bot/features/summarize/README.md` for the
extraction + summarization internals, error matrix, and scope
restrictions.

## 6.18 Job History Log (#53)

Every handled webhook update and every scheduled-job invocation
produces one row in `public.job_history_log`. The table is the ground
truth for "did this job run today?" debugging and feeds the daily
digest tally (#54).

```sql
CREATE TABLE public.job_history_log (
    id            BIGSERIAL PRIMARY KEY,
    bot_id        TEXT NOT NULL,
    job_name      TEXT NOT NULL,
    chat_id       BIGINT,
    user_id       BIGINT,
    status        TEXT NOT NULL,            -- 'succeeded' | 'failed'
    error_class   TEXT,
    error_message TEXT,                     -- truncated at 2000 chars
    started_at    TIMESTAMPTZ NOT NULL,
    finished_at   TIMESTAMPTZ NOT NULL
);
```

Indexed on `(started_at DESC)` and `(job_name, started_at DESC)` for
the 24-hour tally query.

**Naming convention.** `job_name` is the *folder name* under
`src/something_really_bot/features/<name>/` for handler dispatches
(e.g. `voice_transcription`, `commands`, `openai_fallback`,
`hello_world`, `example`). For scheduled jobs, it's the name
registered with the `JobRegistry` and used in `scheduler.tf`
(e.g. `tiktok-reminder`, `daily-digest`).

**Where it's wired in.**

* `Dispatcher._safe_handle` stamps `job_name`, `started_at`, and
  `finished_at` onto every `HandlerResult` it returns. The webhook
  orchestrator reads those off the result and records one row via
  `services.job_history.safe_record`. Unhandled updates are skipped —
  the table tracks invocations, not noise.
* `POST /jobs/{job_name}` wraps the dispatch in start/finish timestamps
  and records either `status="succeeded"` or `status="failed"`. A
  failure still propagates (Cloud Scheduler needs the 5xx to retry per
  policy), but the row lands first.

Recording is best-effort: `safe_record` swallows `PostgresError` and
logs a warning, the same pattern used elsewhere when Postgres is
unreachable. A broken database must not break the handler itself.

---

## 7. Scheduled Jobs

Cron-style work runs via Cloud Scheduler hitting `POST /jobs/{name}` on Cloud Run (#22). Each job is a `JobHandler` registered in `main.py`; the scheduler is defined in `infra/terraform/scheduler.tf` (one entry per job). OIDC verification on the route ensures only the scheduler SA can invoke it.

### 7.1 Daily digest (#25, generalized in #54)

Single daily Telegram digest reporting per-site website performance and a 24h tally of bot job invocations. Schedule: 10:30 Europe/Amsterdam. Recipient: the chat id in the `SOMETHING_GROUP_CHAT_ID` Secret Manager secret. Cloud Scheduler entry: `something-bot-daily-digest`. Cloud Run route: `POST /jobs/daily-digest`.

Data sources:

* **Google Analytics 4 Data API** — `totalUsers`, `newUsers`, and the top-5 pages by `screenPageViews`. The Cloud Run runtime SA reads each property; Viewer access is granted via the Admin API since GA4's UI rejects service-account emails. See `scripts/grant_ga4_viewer.py`.
* **Google Search Console** (#51) — `clicks` and `impressions` for whole-property totals, rendered alongside the GA4 stats. GSC has no Admin API and rejects service-account emails, so the runtime authenticates with a personal-OAuth refresh token (scope: `webmasters.readonly`) minted one-off via `scripts/grant_gsc_refresh_token.py`. Three Secret Manager secrets back this: `GOOGLE_OAUTH_SECRET_JSON` (the full Desktop OAuth client JSON, parsed at runtime), `GOOGLE_OAUTH_CLIENT_ID` (operator-convenience mirror, not read at runtime), and `GSC_OAUTH_REFRESH_TOKEN` (the long-lived refresh token).
* **`public.job_history_log` (#53)** — per-job `succeeded`/`failed` counts over the trailing 24 hours, rendered as a "Jobs (last 24h)" section appended below the per-site sections.

Graceful degradation: per-site GA4 and GSC fetches run in parallel and fail independently — a site's section drops only if **both** sources fail; otherwise the surviving source renders alone. Postgres failure on the tally query drops only the tally section; if every site fails *and* the tally is empty, the digest still sends "No data today." so the operator notices the failure mode rather than silent absence.

---

## 8. Infrastructure as Code

All infrastructure must be defined in the same repository using Terraform.

Assume no infrastructure exists unless explicitly provided later.

Terraform should define, at minimum:

* Cloud Run service.
* Artifact Registry repository, if needed.
* GCS bucket for Telegram file storage.
* Secret Manager secrets or references to them.
* Service accounts and IAM bindings.
* BigQuery dataset/table resources if the final decision is to manage them through Terraform.
* Cloud Scheduler resources if cron migration is included in the implementation phase.

### Cloud Run Settings

Cloud Run settings should be proposed through an RFC/task before final implementation.

The proposal should cover:

* Region: `europe-west4`
* CPU
* Memory
* Timeout
* Concurrency
* Min/max instances
* Public ingress requirement
* Environment variables
* Secret injection
* Runtime service account

### Public Access

The Telegram webhook requires a publicly reachable endpoint. The service may allow unauthenticated invocation, but must validate the Telegram secret header before processing requests.

### Acceptance Criteria

* Terraform can provision the required infrastructure from scratch.
* Infrastructure is documented.
* Cloud Run is deployed in `europe-west4`.
* Secrets are not committed to the repository.
* IAM permissions follow least-privilege as much as practical.

---

## 9. Secrets and Configuration

Secrets must be stored in Google Secret Manager.

### Required Secrets / Config Values

Likely required:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_WEBHOOK_SECRET
QA_TELEGRAM_USER_IDS
GCP_PROJECT_ID
BIGQUERY_DATASET
GCS_BUCKET
OPENAI_API_KEY
POSTGRES_DSN
POSTGRES_INSTANCE
GOOGLE_OAUTH_SECRET_JSON
GOOGLE_OAUTH_CLIENT_ID
GSC_OAUTH_REFRESH_TOKEN
```

`POSTGRES_DSN` and `POSTGRES_INSTANCE` together drive the shared Cloud SQL connection described in §6.8.1: the DSN supplies user/password/database; the instance connection name is read at deploy time (passed to `--add-cloudsql-instances`) and at runtime (used to mount the Auth Proxy socket and override the DSN host). The instance secret is also readable by the deployer SA so the deploy workflow can pass it to `gcloud`.

Exact naming can be adjusted, but must be documented.

### Configuration Requirements

* Config must be loaded from environment variables and/or Secret Manager-injected env vars.
* No secrets may be hardcoded.
* The app should fail fast with a clear error if required config is missing.
* Config loading must be centralized.
* Multiple-bot support should be considered in the config design, even though only one bot is active initially.

### Acceptance Criteria

* Required config is documented.
* Missing required config causes clear startup/runtime errors.
* Secret values are never logged.
* Config loading is covered by tests.

---

## 10. CI/CD

GitHub Actions must be used for CI/CD.

### Required Behavior

On every push to `main`:

1. Install dependencies using `uv`.
2. Run formatting/lint checks with `ruff`.
3. Run tests with `pytest`.
4. Build Docker image.
5. Push Docker image to Artifact Registry.
6. Deploy to Cloud Run.

Authentication to GCP should use OIDC / Workload Identity Federation, not long-lived JSON service account keys.

The required deployment service account may not exist yet and should be defined/proposed as part of Terraform.

### PR Behavior

PR workflow is not a major concern, but if implemented, it should run:

* Dependency installation.
* Linting/formatting checks.
* Tests.

### Webhook Setup

The implementation agent should decide whether GitHub Actions should also set/update the Telegram webhook after deployment.

This should be explicitly proposed because it involves using the Telegram bot token during deployment.

### Acceptance Criteria

* Push to `main` deploys the service to Cloud Run.
* CI fails if linting or tests fail.
* GCP auth uses OIDC, not static service account keys.
* Docker image is tagged with commit SHA or equivalent immutable identifier.
* Deployment steps are documented.

---

## 11. Repository Structure

The repository should be restructured into a clean Python application.

Suggested structure:

```text
.
├── .github/
│   └── workflows/
│       ├── ci.yml
│       └── deploy.yml
├── docs/
│   ├── specification.md
│   ├── architecture.md
│   ├── migration-plan.md
│   └── decisions/
├── infra/
│   └── terraform/
│       ├── main.tf
│       ├── variables.tf
│       ├── outputs.tf
│       └── environments/
├── common/
│   └── __init__.py/
│   └── client.py/
├── src/
│   └── something_really_bot/
│       ├── __init__.py
│       ├── main.py
│       ├── config.py
│       ├── logging.py
│       ├── telegram/
│       │   ├── client.py
│       │   ├── models.py
│       │   ├── parser.py
│       │   └── security.py
│       ├── routing/
│       │   ├── dispatcher.py
│       │   └── types.py
│       ├── features/
│       │   ├── commands/
│       │   ├── hello_world/
│       │   └── file_storage/
│       ├── persistence/
│       │   ├── bigquery.py
│       │   └── schemas.py
│       ├── file_storage/
│       │   └── gcs.py
│       └── services/
├── tests/
│   ├── unit/
│   └── integration/
├── Dockerfile
├── pyproject.toml
├── uv.lock
├── README.md
├── CLAUDE.md
└── .gitignore
└── .gcloudignore
```

The exact layout can differ, but the final repo must preserve these architectural boundaries:

* Web/API layer
* Telegram parsing/client layer
* Routing/dispatcher layer
* Feature handlers
* Persistence layer
* Storage layer
* Config/secrets layer
* Tests
* Terraform infrastructure
* CI/CD workflows

### Acceptance Criteria

* Code lives under `src/`.
* Tests live under `tests/`.
* Infrastructure lives under `infra/terraform/`.
* The repository has a valid `pyproject.toml`.
* Dependencies are managed with `uv`.
* The structure is documented in README or architecture docs.

---

## 12. Testing Requirements

Only use `pytest`.

External systems must be mocked in unit tests:

* Telegram API
* BigQuery
* Google Cloud Storage
* Secret/config injection where relevant

### Required Test Coverage Areas

The first implementation must include tests for:

* Webhook secret validation.
* Telegram update parsing.
* Text message routing.
* `/start` command handling.
* `/help` command handling.
* QA user allowlist behavior.
* BigQuery persistence interface calls.
* File metadata extraction.
* GCS storage interface calls.
* Unsupported update handling.
* Config loading/failure behavior.

No specific coverage percentage is required initially, but key behavior must be covered. The agent should not perform the classic ritual of testing only that `1 == 1` and calling it quality assurance.

### Acceptance Criteria

* `uv run pytest` passes.
* Tests do not require real Telegram/GCP access.
* Tests are deterministic.
* Core routing and security behavior is covered.

---

## 13. Logging and Observability

The initial version only requires basic structured logging.

Logs should go to Cloud Run / Cloud Logging through standard output.

### Required Logging

Log at least:

* Webhook request received.
* Update ID.
* Bot ID/name where available.
* Message type.
* Chat type.
* Processing route/handler selected.
* BigQuery persistence success/failure.
* File storage success/failure.
* Telegram response success/failure.
* Unsupported update types.
* Exceptions with useful context.

### Not Required Initially

* Alerting.
* Dashboards.
* SLOs.
* Pager integration.
* Custom metrics.

These should be listed as future iterations.

### Acceptance Criteria

* Logs are structured enough to query in Cloud Logging.
* Secrets are never logged.
* Exceptions are logged with context.
* Unsupported updates are visible in logs.

---

## 14. Local Development

Local development polish is not a priority.

However, the repository should still support basic developer commands:

```bash
uv sync
uv run pytest
uv run ruff check
uv run ruff format
```

A fully polished local webhook setup with ngrok/cloudflared is not required for the initial implementation.

### Acceptance Criteria

* Project can be installed with `uv sync`.
* Tests can run locally without real GCP/Telegram access.
* README documents minimal local commands.

---

## 15. Docker Packaging

The Cloud Run service must be built from a Dockerfile.

### Requirements

* Use Python 3.12.
* Use `uv` for dependency installation.
* Run FastAPI with an appropriate ASGI server, likely `uvicorn`.
* Expose the correct port for Cloud Run.
* Keep the image reasonably small and production-oriented.

### Acceptance Criteria

* Docker image builds successfully.
* Container starts the FastAPI app.
* Container works with Cloud Run’s expected `PORT` environment variable.
* Dockerfile is committed to the repository.

---

## 16. Multi-Bot Readiness

The first deployment only needs one active Telegram bot, but the architecture must not assume that there can only ever be one.

### Design Requirements

* Introduce a bot identifier concept.
* Keep bot-specific config isolated.
* Avoid hardcoding one bot token throughout the app.
* Structure handlers so that bot-specific behavior can be added later.
* Store bot identifier in BigQuery records.

Possible future shape:

```text
bots:
  default:
    token: secret ref
    webhook_secret: secret ref
    enabled_features:
      - commands
      - hello_world
      - file_storage
  another_bot:
    token: secret ref
    webhook_secret: secret ref
    enabled_features:
      - custom_feature
```

The exact config mechanism does not need to be implemented fully now, but the design should not block it.

### Acceptance Criteria

* Code has a clear place for bot-specific configuration.
* Persistence includes bot identifier.
* Telegram client is not implemented as an untestable global singleton.

---

## 17. Feature Migration

Feature-by-feature migration is tracked in the umbrella issue #21. Each migrated feature lands as its own PR with tests; legacy code is not copied verbatim.

---

## 18. Known Unknowns / Required RFCs

The implementation agent must create short RFC/proposal documents or issue descriptions for the following before implementation where applicable.

### 18.1 BigQuery Schema RFC

Define:

* Dataset/table names.
* Raw update table schema.
* Normalized message table schema.
* File metadata table schema.
* Response/event logging schema.
* Partitioning/clustering approach, if any.

### 18.2 Async File Processing RFC

Define whether file processing should be:

* Inline async in the webhook request.
* Pub/Sub-based.
* Cloud Tasks-based.
* Cloud Run Job-based.

Given the expected tiny volume, prefer the simplest reliable solution.

### 18.3 Cloud Run Settings RFC

Define:

* CPU/memory.
* Timeout.
* Concurrency.
* Min/max instances.
* Service account.
* Ingress/authentication model.

### 18.4 Multi-Bot Config RFC

Define how the project should support multiple bots later without overbuilding it immediately.

---

## 19. Suggested Implementation Phases

## Phase 1 — Project Skeleton

* Add Python 3.12 project structure.
* Add `pyproject.toml`.
* Configure `uv`.
* Configure `ruff`.
* Configure `pytest`.
* Add initial FastAPI app.
* Add Dockerfile.
* Add minimal README.

Acceptance:

* `uv sync` works.
* `uv run ruff check` works.
* `uv run pytest` works.
* Docker image builds.

---

## Phase 2 — Webhook Foundation

* Implement `POST /webhook`.
* Implement Telegram secret header validation.
* Implement update parsing.
* Implement basic routing.
* Implement unsupported update handling.

Acceptance:

* Webhook accepts valid Telegram-like payloads.
* Invalid secret is rejected.
* Supported update types are classified.
* Tests cover parsing/security/routing.

---

## Phase 3 — Hello World / QA Response

* Implement QA user allowlist config.
* Implement text message handler.
* Implement `/start` and `/help` handlers.
* Implement Telegram send message client.
* Mock Telegram client in tests.

Acceptance:

* Authorized QA users receive Hello World/parrot response.
* Unauthorized users do not.
* `/start` and `/help` return placeholders.

---

## Phase 4 — BigQuery Persistence

* Propose BigQuery schema.
* Implement BigQuery persistence service.
* Store raw updates.
* Store normalized message metadata.
* Store response metadata where applicable.
* Add tests with mocks.

Acceptance:

* Incoming updates are persisted.
* Persistence failures are logged safely.
* Tests verify persistence calls.

---

## Phase 5 — File Storage

* Propose async file handling approach.
* Implement file metadata extraction.
* Implement Telegram file download.
* Provision/configure GCS bucket.
* Store files in GCS.
* Store file metadata in BigQuery.
* Add tests with mocked Telegram/GCS/BigQuery.

Acceptance:

* Photo/document/voice files sent directly to bot are stored in GCS.
* Metadata is stored in BigQuery.
* The webhook remains reliable.

---

## Phase 6 — Terraform Infrastructure

* Add Terraform project structure.
* Define Cloud Run service.
* Define Artifact Registry.
* Define GCS bucket.
* Define service accounts/IAM.
* Define Secret Manager resources/references.
* Define optional BigQuery resources.
* Define optional Cloud Scheduler resources after cron RFC.

Acceptance:

* Terraform plan succeeds.
* Infrastructure is documented.
* Cloud Run deploy target exists.

---

## Phase 7 — CI/CD

* Add GitHub Actions workflows.
* Configure OIDC / Workload Identity Federation.
* Run lint/tests on push.
* Build Docker image.
* Push to Artifact Registry.
* Deploy to Cloud Run.
* Optionally set Telegram webhook after deploy if approved.

Acceptance:

* Push to `main` deploys successfully.
* No static GCP keys are used.
* CI fails on lint/test failure.

---

## Phase 8 — Feature Migration

* Migrate legacy features one at a time via the umbrella issue #21.
* Each migrated feature has its own PR with tests.

---

## 20. AI Agent Operating Instructions

The implementation agent must follow these rules:

1. Do not blindly copy messy legacy code; preserve useful business logic only after understanding it.
2. Keep the first deployment of any new feature minimal.
3. Prefer simple architecture over over-engineering.
4. Keep boundaries clean: API, routing, features, persistence, storage, config.
5. Add tests for every meaningful behavior implemented.
6. Mock external APIs in unit tests.
7. Do not commit secrets.
8. Use `uv`, `ruff`, and `pytest`.
9. Use Terraform for infrastructure.
10. Use GitHub Actions with GCP OIDC.
11. Document known unknowns instead of guessing silently.
12. Create small, reviewable tasks/issues.
13. Make the system extensible for future multi-bot support.

---

## 21. Initial Definition of Done

The foundational rebuild is done when:

* A Python 3.12 FastAPI service runs on Cloud Run.
* Telegram webhook points to the new `/webhook` endpoint.
* Telegram secret header validation works.
* Incoming updates are parsed and routed.
* Text messages from QA users receive Hello World/parrot responses.
* `/start` and `/help` work.
* Incoming updates/messages are persisted to BigQuery.
* File messages are stored in GCS with metadata in BigQuery.
* Basic structured logs are visible in Cloud Logging.
* Infrastructure is defined in Terraform.
* Deployment happens from GitHub Actions on push to `main`.
* Tests cover core behavior.
* The repository has a clean, documented structure.
* Legacy feature migration is documented as a separate roadmap.

---

## 22. Open Inputs Required From User

These foundational inputs are captured in code or Terraform variables and no longer block work:

* GCP project ID (`something-bot-338300`).
* BigQuery dataset name (`something_bot`).
* Telegram bot token / QA users / webhook secret (Secret Manager names in `var.bots`).
* Service / repo / WIF naming conventions (Terraform variables).

---

## 23. Final Notes

This bot should stay boring, reliable, and extensible. Not every legacy feature gets rebuilt — only the ones still worth running. The floor is in place; new chandeliers go in via small, reviewable PRs that don't collapse it back into procedural glue.
