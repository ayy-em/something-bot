# 0002 — Async file-processing strategy

**Status:** Accepted, 2026-05-16. Implements [#19](../../issues/19); consumed by [#20](../../issues/20).
**Spec:** SPEC §6.7 (File Handling) and §18.2 (Async File Processing RFC).

## Decision

Process Telegram → GCS file downloads **inline async** using
``asyncio.create_task`` after returning 200 to the webhook. The Cloud Run
revision runs with **CPU always allocated** so the background task
survives the response cycle.

If the workload outgrows this (sustained >5% failure rate or routine
files >20MB), swap the inline `FileFetcher` implementation for a Cloud
Tasks-backed one. Handler code does not change.

## Why this, not the alternatives

| Approach | Verdict | Reason |
| --- | --- | --- |
| **Inline async + CPU-always-on** | Chosen | Zero new infra. Fast. Adequate for personal scale (SPEC §6.8). |
| Cloud Tasks | Rejected (now) | Reliable retries, but every file gets an extra IAM-protected endpoint, a queue, and retry-policy tuning. Not justified at this volume. |
| Pub/Sub | Rejected | Same overkill concern; cold-start delay; we'd be paying for fan-out we don't have. |
| Cloud Run Jobs | Rejected | Wrong shape — designed for batches, not "one file per webhook". |

## Implementation contract

Handlers depend on a ``FileFetcher`` interface (lands with #20). The
interface has a single method::

    async def fetch_and_store(self, file_ref: TelegramFileRef) -> None

The MVP implementation does, after the webhook has already returned 200:

1. ``getFile`` against the Telegram Bot API to resolve ``file_path``.
2. Stream the file from Telegram's CDN to GCS via
   ``google-cloud-storage`` (resumable upload), reusing the existing
   ``httpx.AsyncClient`` for the Telegram fetch.
3. On success, update ``telegram_files`` with ``gcs_uri``,
   ``download_status="success"``, and ``downloaded_at``.
4. On failure, update ``download_status="failed"`` and ``error``; log
   structured context. **Do not raise out of the task.**

The webhook never awaits the background task — it returns 200 first,
schedules the task second.

## Cloud Run knob

Set ``--cpu-throttling=false`` (i.e. "CPU always allocated") on the
service. Without this, Cloud Run can throttle the container after the
response is flushed, killing in-flight `asyncio` tasks. Cost impact at
our concurrency is in the noise. The Terraform change is a one-line
attribute on the Cloud Run service (#20 will apply it alongside the
``FileFetcher`` wiring).

## Trade-offs we accept

- **Non-durable.** If Cloud Run terminates the instance (deploy,
  rolling restart, scale-down) mid-download, the file is lost. We
  surface this in the ``download_status="failed"`` row; users can
  re-send. Acceptable at personal scale.
- **No retry budget.** First-failure is final, save for re-sends. If
  this hurts, the migration path is the Cloud Tasks swap above.
- **Single-process bottleneck.** A Cloud Run instance with one in-flight
  download serves the next webhook just fine (asyncio), but extremely
  large files could starve other handlers of CPU. ``concurrency=80``
  plus the small expected file sizes makes this hypothetical.

## Out of scope

- Cloud Tasks / Pub/Sub / Cloud Run Jobs migration paths (documented
  above as the "if we outgrow this" exit ramps).
- File deduplication (SPEC §6.7 explicitly excludes this).
- Files >20MB (Telegram's bot-download cap; not addressable from this
  side).
- Virus scanning / mime sniffing / content policy — out of MVP scope.
