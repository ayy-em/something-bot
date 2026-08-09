# 0004 — Eliminating idle billing (cpu_idle=true + durable background work)

**Status:** Proposed — deliberately not decided. Revisit after one week of
post-[0003](0003-manual-ensure-webhook.md) data (i.e. on or after 2026-08-16).
**Related:** [0002](0002-async-file-processing.md) (introduced `cpu_idle = false`),
[0003](0003-manual-ensure-webhook.md) (removed the 15-minute cron, halved the instance).

## What this is about

The service runs with `cpu_idle = false` ("CPU always allocated"), which puts
it on **instance-based billing**: the instance's entire lifetime is charged,
not just request-processing time. That flag exists because six handlers do
work *after* the webhook has already returned 200, and throttled CPU would
stall them.

Decision 0003 removed the cron that was holding an instance open 24/7. What
remains is the structural question: should we stop paying for idle at all?

## What we'd want

1. Cost that tracks actual work rather than instance uptime.
2. Background work (media downloads, transcription, OCR) that still completes
   — and ideally completes *more* reliably than today.

## The scope, which is larger than 0002 implies

Decision 0002 framed post-response work as one thing: the file → GCS download.
It is now **six** sites, all using `asyncio.create_task`:

| Site | Work done after the 200 |
| --- | --- |
| `file_storage/fetcher.py` | Telegram file → GCS upload |
| `features/video_downloader/handler.py` | yt-dlp download + ffmpeg mux |
| `features/voice_transcription/handler.py` | audio → transcript |
| `features/ocr/handler.py` | image → text |
| `features/summarize/handler.py` | document extract + summarize |
| `features/make_sticker/handler.py` | image → sticker |

Flipping `cpu_idle = true` throttles **all six**, not just the file download.
That is the single biggest input to the cost of this work, and it is not
visible from 0002.

Mitigating factor: every one of them already takes an injectable `scheduler`
parameter (defaulting to `asyncio.create_task`). The seam for swapping in a
different dispatch mechanism exists and is uniform. The work is in the
infrastructure and the semantics, not in restructuring handler code.

## Cost

Measured 2026-07-26 → 2026-08-08: **~230 requests/day**, of which 96 were the
now-removed ensure-webhook cron and ~1 the daily-message job. Real traffic is
therefore **~130 requests/day**.

Rates below are `europe-west4` Tier 1 list prices; treat as estimates.

At the new 1 vCPU / 1 GiB sizing:

| Billing mode | Rate | Charged for |
| --- | --- | --- |
| Instance-based (`cpu_idle = false`, today) | $0.000020 /instance-s | Whole instance lifetime |
| Request-based (`cpu_idle = true`) | $0.0000265 /request-s + $0.40/M req | Request processing only |

### If we don't do this

Cost is driven entirely by **how clustered the ~130 daily requests are**,
because each burst pins an instance for Cloud Run's ~15-minute idle window
(not configurable).

| Instance uptime | Per day | Per month |
| --- | --- | --- |
| Floor — 2 scheduled jobs, no chat | $0.04 | ~EUR 1 |
| 2 h/day (bursty use) | $0.14 | ~EUR 4 |
| 6 h/day | $0.43 | ~EUR 12 |
| 12 h/day (spread across waking hours) | $0.86 | ~EUR 24 |

**We do not currently know which row we are on.** The cron masked it — the
instance was pinned at 100% regardless. One week of `billable_instance_time`
after 0003 resolves this, and it is the single fact that decides whether this
work is worth doing.

### If we do it

~130 webhook requests/day plus worker invocations, with background work now
running inside a request. Generously, ~400 billable request-seconds/day:

- 400 s/day × $0.0000265 ≈ **$0.32/month**
- Monthly usage ≈ 12,000 vCPU-s and 12,000 GiB-s, against a request-based
  free tier of 180,000 vCPU-s / 360,000 GiB-s / 2M requests
- Cloud Tasks: ~2,000 ops/month against a 1M/month free tier

**Effectively EUR 0/month.** So the saving is whatever the row above turns out
to be — somewhere between EUR 1 and EUR 24/month, most plausibly EUR 3–10.

That is the uncomfortable finding: **0003 already captured the large majority
of the available savings** (~EUR 96/month → single-digit EUR/month). Item 3's
remaining cost case is small in absolute terms. Its real justification, if any,
is reliability.

## The reliability argument (the stronger one)

`asyncio.create_task` gives no durability. If the instance is reaped mid-work
— scale-down, revision swap, OOM, crash — the download is lost silently and
the user gets nothing. `cpu_idle = false` narrows that window but does not
close it. Cloud Run can terminate an instance at any time.

Cloud Tasks would add: durable queueing, automatic retries with backoff,
dead-lettering, and per-task observability. Decision 0002 already named its own
trigger for this — *"sustained >5% failure rate or routine files >20MB"*. That
trigger is the right one, and it is about correctness, not cost.

Worth noting we can measure this today: `telegram_files` rows carry
`download_status`, so the current failure rate is already queryable.

## Options

| Option | Cost/month | Effort | Verdict |
| --- | --- | --- | --- |
| **Do nothing; measure for a week** | EUR 1–24 | ~0 | **Recommended now** |
| Full Cloud Tasks + `cpu_idle = true` | ~EUR 0 | Multi-day, 6 migration sites, real regression risk across 6 user-facing features | Only if measurement or failure rate justifies it |
| Split services: light webhook (`cpu_idle=true`) + worker (`cpu_idle=false`) | ~EUR 1–3 | Moderate — second service, deploy pipeline, fire-and-forget HTTP hop | Best middle ground if cost alone is the driver |
| Shrink to 1 vCPU / 512 MiB | −5% | Trivial | Not worth it — CPU is 90% of the bill and can't go below 1 vCPU with CPU always allocated |
| Reduce Cloud Run's idle window | — | — | Not possible; not a tunable knob |
| Per-request `cpu_idle` | — | — | Not possible; service-level flag |

The **split-services** option deserves more attention than it usually gets. The
webhook path is what stays warm; the heavy media work is rare. Running a
minimal `cpu_idle = true` webhook service that fires a request at a separate
`cpu_idle = false` worker gets most of the cost benefit without queue
semantics. It does not give you retries — that is exactly what Cloud Tasks adds
on top, and only that increment needs justifying separately.

## Recommendation

1. **Now:** nothing further. Add a GCP budget alert (~EUR 10/month) as cheap
   insurance — this is worth doing regardless of what we decide later.
2. **2026-08-16:** pull a week of `billable_instance_time` and query
   `telegram_files` for the real `download_status` failure rate.
3. **Decide on evidence:**
   - Idle burn sustained above ~EUR 10/month → do the **split-services** option.
   - Background-work failure rate above ~5% → do **Cloud Tasks**, per 0002's
     own trigger, and take `cpu_idle = true` as a free side effect.
   - Neither → close this and leave the architecture alone.

Doing the full refactor now would be optimizing a EUR 5/month line item at the
cost of destabilizing six working features. The measurement is nearly free;
take it first.
