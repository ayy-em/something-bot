# 0003 — Unschedule ensure-webhook; right-size the Cloud Run instance

**Status:** Accepted, 2026-08-09.
**Supersedes:** the `*/15 * * * *` `ensure-webhook` entry in `infra/terraform/scheduler.tf`.
**Related:** [0002](0002-async-file-processing.md) (which introduced `cpu_idle = false`).

## Decision

1. **Remove `ensure-webhook` from Cloud Scheduler entirely.** It stays a
   registered job, reachable by hand at `GET /jobs/<name>?token=…`,
   authenticated by a `MANUAL_JOB_TOKEN` shared secret from Secret Manager.
2. **Halve the instance:** 2 vCPU / 2 GiB → 1 vCPU / 1 GiB.

`cpu_idle = false` stays. Decision 0002 still holds — background file
downloads need CPU after the response is flushed.

## Why

Measured on 2026-08-09 via the Monitoring API: the service billed **3,600
instance-seconds per hour** — one instance alive continuously — while
serving about **10 requests per hour**.

Under `cpu_idle = false` the service is on **instance-based billing**: the
instance's entire lifetime is charged, not just request-processing time.
Cloud Run reaps an idle instance after roughly 15 minutes. A `*/15` ping
landed on that boundary and reset the timer every time, so the instance
never scaled to zero.

Cost at 2 vCPU / 2 GiB in `europe-west4` (list rates, instance-based):

| | rate | per instance-second |
| --- | --- | --- |
| 2 vCPU | ~$0.000018 /vCPU-s | $0.000036 |
| 2 GiB | ~$0.0000020 /GiB-s | $0.000004 |
| **total** | | **$0.000040** |

86,400 s/day × $0.000040 ≈ **$3.46/day ≈ EUR 96/month** — for a personal
bot with roughly a dozen requests an hour. Decision 0002's note that "cost
impact at our concurrency is in the noise" was wrong by about EUR 95/month.

The 15-minute cadence was also redundant: `.github/workflows/deploy.yml`
calls `setWebhook` after every revision, which covers the failure mode the
job was written for (Telegram dropping the webhook during a revision swap).
Spontaneous webhook loss is rare enough to handle by hand.

## Expected effect

Idle burn goes to roughly zero. What remains is real Telegram traffic and
two scheduled jobs (`daily-message` daily, `tiktok-reminder` weekly), each
of which spins an instance up and holds it for its ~15-minute idle window.
At 1 vCPU / 1 GiB the rate is $0.000020/instance-second, so a job that
occupies one 15-minute window costs about **$0.018**.

Actual savings track chat activity: a busy conversation still holds an
instance open, now at half the previous rate.

## Why not the alternatives

| Approach | Verdict | Reason |
| --- | --- | --- |
| **Unschedule + right-size** | Chosen | One-line cadence removal plus a sizing change. Keeps decision 0002 intact. |
| Reduce cadence to hourly | Rejected | Still ~6 billable hours/day (24 spin-ups × ~15 min idle) for a job that duplicates what deploy already does. |
| `cpu_idle = true` | Rejected (now) | Kills idle billing outright, but throttles CPU after response flush and breaks the background download in decision 0002. Needs the Cloud Tasks refactor first. |
| `cpu_idle = true` + Cloud Tasks | Deferred | The structurally correct end state — background work runs inside a real request. Real refactor; tracked separately. |
| Scale to 0.5 vCPU | Rejected | Cloud Run requires ≥1 vCPU when CPU is always allocated. |

## Operating it

The token lives in Secret Manager as `manual-job-token`; Terraform creates
the placeholder, the value is added out-of-band and never enters state:

```
openssl rand -hex 32 | gcloud secrets versions add manual-job-token \
  --project=something-bot-338300 --data-file=-
```

To fix a broken webhook, open in a browser:

```
https://<service-url>/jobs/ensure-webhook?token=<token>
```

`GET /jobs/*` 401s when `MANUAL_JOB_TOKEN` is unset, so the route is inert
until the secret version exists. The OIDC-gated `POST /jobs/*` route used
by Cloud Scheduler is unchanged.

Caveat: the token travels in the URL, so it lands in browser history and in
Cloud Run request logs. It grants nothing beyond re-running a job, and
rotating it is one `gcloud secrets versions add` plus a redeploy.
