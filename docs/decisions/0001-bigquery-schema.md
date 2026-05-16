# 0001 — BigQuery schema for the Telegram bot

**Status:** Accepted, 2026-05-16. Implements [#17](../../issues/17); consumed by [#18](../../issues/18).
**Spec:** SPEC §6.8 (Persistence) and §18.1 (Schema RFC).

## Decision

One BigQuery dataset, five tables, time-partitioned by date, clustered by
`bot_id` plus the most-queried discriminator per table. Every table carries
`bot_id` so future multi-bot support (SPEC §16) is a config change, not a
migration.

## Dataset

| Property | Value |
| --- | --- |
| Project | `something-bot-338300` |
| Dataset ID | `something_bot` |
| Location | `EU` (regional, matches Cloud Run `europe-west4`) |

`EU` is a multi-region; if cross-region egress ever becomes a cost concern
we can pin to `europe-west4` and recreate the dataset (small at this stage).

## Tables

| Table | Purpose | Partitioning | Clustering |
| --- | --- | --- | --- |
| `telegram_updates_raw` | Raw webhook JSON, append-only audit log | `DATE(received_at)` | `bot_id, update_type` |
| `telegram_messages` | Normalized message-level fields | `DATE(received_at)` | `bot_id, chat_type, message_type` |
| `telegram_files` | File metadata + GCS object path | `DATE(received_at)` | `bot_id, file_type` |
| `bot_responses` | Outgoing messages the bot sent | `DATE(sent_at)` | `bot_id, chat_id` |
| `processing_events` | Per-update handler status / error log | `DATE(occurred_at)` | `bot_id, status` |

### `telegram_updates_raw`

Append-only mirror of the Telegram payload, indexed for forensic lookup.

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `update_id` | INT64 | no | Telegram-issued update ID. |
| `bot_id` | STRING | no | Logical bot identifier (`default` until #16-config lands). |
| `update_type` | STRING | no | `private_message` / `group_message` / `supergroup_message` / `channel_post` / `unsupported`. |
| `raw_payload` | JSON | no | The full POST body Telegram sent. |
| `received_at` | TIMESTAMP | no | Server clock when `/webhook` received it. |

### `telegram_messages`

One row per `message` / `channel_post` extracted from an update. Updates
that don't carry a message (`unsupported`, future callback/inline) are
not written here.

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `update_id` | INT64 | no | Joins back to `telegram_updates_raw`. |
| `bot_id` | STRING | no | |
| `message_id` | INT64 | no | Telegram message ID. |
| `chat_id` | INT64 | no | |
| `chat_type` | STRING | no | `private` / `group` / `supergroup` / `channel`. |
| `chat_title` | STRING | yes | Set for group / supergroup / channel. |
| `user_id` | INT64 | yes | `from.id`; null for channel posts. |
| `username` | STRING | yes | `from.username`; often null. |
| `message_type` | STRING | no | `text` / `command` / `photo` / `document` / `voice` (matches `MessageContent.kind`). |
| `command` | STRING | yes | Populated for `message_type='command'`. |
| `text` | STRING | yes | Message text or caption. |
| `received_at` | TIMESTAMP | no | |
| `processed_at` | TIMESTAMP | yes | When dispatch finished. |
| `processing_status` | STRING | no | `received` / `handled` / `skipped` / `errored`. |
| `handler_name` | STRING | yes | The handler that ran (if any). |
| `error` | STRING | yes | Captured exception message. |

### `telegram_files`

One row per file attachment we observed. `gcs_uri` is populated by #20 once
the file is downloaded and stored.

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `update_id` | INT64 | no | |
| `bot_id` | STRING | no | |
| `chat_id` | INT64 | no | |
| `message_id` | INT64 | no | |
| `file_id` | STRING | no | Telegram file ID. |
| `file_unique_id` | STRING | no | Stable across re-shares. |
| `file_type` | STRING | no | `photo` / `document` / `voice`. |
| `mime_type` | STRING | yes | |
| `file_size_bytes` | INT64 | yes | |
| `original_filename` | STRING | yes | Documents only. |
| `gcs_uri` | STRING | yes | `gs://<bucket>/<path>` once stored. |
| `download_status` | STRING | no | `pending` / `downloaded` / `failed`. |
| `received_at` | TIMESTAMP | no | |
| `downloaded_at` | TIMESTAMP | yes | |
| `error` | STRING | yes | |

### `bot_responses`

Outbound messages. Successful sends carry `message_id`; failures carry
`error`. `in_response_to_update_id` is null for unsolicited outbound
(future cron jobs).

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `bot_id` | STRING | no | |
| `in_response_to_update_id` | INT64 | yes | |
| `chat_id` | INT64 | no | |
| `message_id` | INT64 | yes | The Telegram message ID returned by `sendMessage`. |
| `response_type` | STRING | no | `text` (only kind today); `file` / etc. later. |
| `text` | STRING | yes | |
| `sent_at` | TIMESTAMP | no | |
| `success` | BOOL | no | |
| `error` | STRING | yes | |

### `processing_events`

Coarse-grained activity log. One row per meaningful dispatcher event:
`update_received`, `handler_matched`, `handler_errored`,
`update_unhandled`. Cheap to scan, easy to graph in Looker Studio later.

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `update_id` | INT64 | yes | Null for events not tied to an inbound update. |
| `bot_id` | STRING | no | |
| `event` | STRING | no | `update_received` / `handler_matched` / `handler_errored` / `update_unhandled`. |
| `handler_name` | STRING | yes | |
| `status` | STRING | no | `ok` / `error`. |
| `details` | STRING | yes | Free-text context (truncated by the persistence layer). |
| `occurred_at` | TIMESTAMP | no | |

## Conventions

- All timestamps stored UTC; the persistence layer is responsible for not
  inserting naive datetimes.
- All `STRING` enum-like columns are written as lowercase snake_case at the
  Python boundary; no DB-level constraint.
- Additive migrations only. New columns must be nullable; never DROP /
  rename in place — create a new column and dual-write during transition.
- `raw_payload JSON` is the safety net: anything we forget to normalize
  can be back-filled by reprocessing the raw row.

## Out of scope

- Authorization / row-level security: bot project is single-tenant.
- PII masking: SPEC §6.8 explicitly allows plain storage at this stage.
- Streaming inserts vs. batch: #18 will use streaming inserts; if costs
  surprise us we revisit.
