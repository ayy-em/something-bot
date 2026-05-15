# Something Dashboard Telegram Bot Revamp — Specification & AI Agent Execution Brief

## 1. Purpose

This document defines the specification for rebuilding the existing **Something Really Bot**, a Telegram personal assistant bot, from scratch inside the existing repository.

The current implementation is a Python 3.9 application running on **Google App Engine**, with Telegram webhook handling and cron jobs defined through App Engine / Cloud Scheduler-style configuration. The existing codebase is considered messy and should not be incrementally refactored. The target state is a clean, extensible, tested Python 3.12 service deployed to **Google Cloud Run**.

The primary intended reader of this specification is an AI coding agent that will use this specification to create a roadmap, break the work into issues, and implement the rebuild.

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

## 3. Current State

### 3.1 Existing Application

The existing bot:

* Lives in the current **Something Dashboard** repository.
* Runs on Python 3.9.
* Is deployed to Google App Engine.
* Uses Telegram Bot API via webhook.
* Has cron-style scheduled functionality, with existing cron configuration to be provided separately.
* Contains legacy functionality to be migrated later.
* Has messy code that should be replaced rather than refactored in place.

### 3.2 Migration Input Format

The existing functionality will be provided separately as a list of references in the following shape upon agent's request:

```text
<file path or code reference> — <short summary of current functionality>
```

The implementation agent must use that list during the feature discovery and migration planning phase.

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

The App Engine deployment must be fully removed and replaced by Cloud Run.

The rebuild happens inside the same repository. The agent should treat the old implementation as disposable. Existing files may be deleted, moved, or replaced as needed, but the migration should be done carefully enough that useful historical logic can still be referenced before removal.

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

## 7. Scheduled Jobs / Cron Migration

The current App Engine setup includes cron-style scheduled jobs. The existing `cron.yaml` or equivalent configuration will be provided separately.

The initial rebuild must include a discovery/proposal step for replacing the cron functionality.

Possible target approaches:

* Google Cloud Scheduler calling Cloud Run endpoints.
* Google Cloud Scheduler publishing to Pub/Sub.
* Cloud Run Jobs triggered by Cloud Scheduler.
* Separate worker service.

The agent must inspect the provided cron configuration and propose the simplest suitable replacement.

### Acceptance Criteria

* Existing cron jobs are inventoried.
* Each cron job has a proposed target architecture.
* Cron migration is included in the roadmap.
* Implementation can be deferred if not required for first cutover.

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
```

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

## 17. Feature Migration Process

Legacy feature migration must be handled separately from the foundational rebuild.

### Required Process

Once the user provides the file/functionality list, the implementation agent must:

1. Inventory existing features.
2. Group features by domain.
3. Identify which features are required for cutover.
4. Identify which features can be deferred.
5. Identify dependencies on App Engine-specific APIs or obsolete libraries.
6. Propose replacement approaches.
7. Create a migration roadmap.
8. Create issues/tasks for implementation.

### Migration Input Format

The user will provide:

```text
<file path or code reference> — <short summary of functionality>
```

### Acceptance Criteria

* Legacy features are not blindly copied into the new app.
* Each migrated feature has tests.
* App Engine-specific dependencies are removed or replaced.
* Feature migration tasks are tracked separately from foundation tasks.

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

### 18.4 Cron Migration RFC

Based on provided `cron.yaml`, define how existing scheduled tasks should be recreated outside App Engine.

### 18.5 Multi-Bot Config RFC

Define how the project should support multiple bots later without overbuilding it immediately.

---

## 19. Suggested Implementation Phases

## Phase 0 — Discovery and Repo Cleanup Plan

* Inspect existing repository.
* Identify App Engine-specific files and deployment config.
* Identify legacy features and cron jobs once provided.
* Propose deletion/replacement plan.
* Confirm target repo structure.

Deliverables:

* Migration inventory.
* File removal/replacement plan.
* Initial task breakdown.

---

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

## Phase 8 — Cron and Legacy Feature Migration

* Inspect provided cron configuration.
* Propose Cloud Scheduler / Cloud Run replacement.
* Inspect provided legacy feature list.
* Create prioritized migration tasks.
* Migrate cutover-critical features first.
* Add tests per migrated feature.

Acceptance:

* App Engine cron dependency is removed.
* Required legacy features are migrated or explicitly deferred.
* Each migrated feature has test coverage.

---

## 20. AI Agent Operating Instructions

The implementation agent must follow these rules:

1. Do not refactor the old App Engine app incrementally. Rebuild cleanly.
2. Do not blindly copy messy legacy code.
3. Preserve useful business logic only after understanding it.
4. Keep the first deployment minimal.
5. Prefer simple architecture over over-engineering.
6. Keep boundaries clean: API, routing, features, persistence, storage, config.
7. Add tests for every meaningful behavior implemented.
8. Mock external APIs in unit tests.
9. Do not commit secrets.
10. Use `uv`, `ruff`, and `pytest`.
11. Use Terraform for infrastructure.
12. Use GitHub Actions with GCP OIDC.
13. Document known unknowns instead of guessing silently.
14. Create small, reviewable tasks/issues.
15. Make the system extensible for future multi-bot support.

---

## 21. Initial Definition of Done

The foundational rebuild is done when:

* The old App Engine app is removed from active deployment.
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

The following inputs are expected later:

1. Existing repository structure or file list.
2. Existing feature list in the agreed format.
3. Existing `cron.yaml` or equivalent scheduled job config.
4. GCP project ID.
5. BigQuery dataset name.
6. Existing BigQuery conventions, if any.
7. Telegram bot token secret name or preferred naming.
8. Telegram webhook secret value/secret name.
9. QA Telegram user IDs.
10. Preferred service name for Cloud Run.
11. Preferred Artifact Registry repository name.
12. Any naming conventions for Terraform resources.

---

## 23. Recommended First Issues

The next agent should likely break this specification into issues similar to:

1. Create Python 3.12 FastAPI project skeleton with `uv`, `ruff`, and `pytest`.
2. Add Dockerfile for Cloud Run deployment.
3. Implement Telegram webhook endpoint with secret header validation.
4. Implement Telegram update parser and typed update classification.
5. Implement routing/dispatcher design proposal.
6. Implement QA allowlist and Hello World/parrot text handler.
7. Implement `/start` and `/help` command handlers.
8. Draft BigQuery schema RFC.
9. Implement BigQuery persistence service.
10. Draft async file-processing RFC.
11. Implement Telegram file download and GCS storage.
12. Add Terraform foundation for Cloud Run, GCS, IAM, Secret Manager, and Artifact Registry.
13. Add GitHub Actions CI workflow.
14. Add GitHub Actions deploy workflow with GCP OIDC.
15. Inspect legacy feature list and create migration roadmap.
16. Inspect cron config and create Cloud Scheduler migration proposal.

---

## 24. Final Notes

The first version should be boring, reliable, and extensible. The goal is not to recreate every legacy feature immediately (most of them would not be moved either way). The goal is to create a clean foundation that can safely receive Telegram updates, store them, respond to QA users, handle files, and support future feature migration without collapsing into another pile of procedural glue.

In other words: build the floor before installing chandeliers. Software teams keep forgetting this, presumably because chandeliers look nicer in sprint demos.
