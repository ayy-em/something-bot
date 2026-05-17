# Observability

Cloud Run streams the bot container's stdout into Cloud Logging. The
app emits **one JSON object per log line** so the lines arrive in Cloud
Logging fully structured rather than as opaque text.

Source: `src/something_really_bot/logging.py`. Terraform for log-based
metrics and alert policies lives in `infra/terraform/logging.tf` (#28).

## Log shape

Every line is a JSON object with at minimum:

| Field | Type | Notes |
|---|---|---|
| `severity` | string | Cloud Logging severity. Cloud Run reads it off the JSON and stamps the log entry. One of `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`. |
| `message` | string | The formatted log message. |
| `logger` | string | Python logger name (`something_really_bot.<module>`). |
| `exception` | string | Stack trace when the call used `logger.exception(...)` or set `exc_info=True`. |
| `update_id` | int | Telegram update ID (when in the webhook flow). |
| `bot_id` | string | Always `default` today; multi-bot lands later. |
| `handler` / `handler_name` | string | Name of the matched handler. |
| `route` | string | HTTP path (when known). |
| `error` | string | Pre-formatted error message — already on the line, doesn't need exception parsing. |

Anything passed via `logger.<level>("foo", extra={"k": "v"})` is merged
into the JSON object at the top level, so Cloud Logging exposes it as
`jsonPayload.k` and structured filters work straight away.

## Querying

In Cloud Logging, the log entries are scoped by `resource.type` and
`resource.labels.service_name`:

```
resource.type="cloud_run_revision"
resource.labels.service_name="something-really-bot-cloudrun"
```

Useful filters built on the JSON payload:

| Question | Filter |
|---|---|
| All errors in the last hour | `severity>=ERROR` |
| One specific update | `jsonPayload.update_id=42` |
| Telegram send failures only | `jsonPayload.message="telegram_send_failed"` |
| Webhook calls that never matched a handler | `jsonPayload.message="no_handler_matched"` |
| Scheduled-job runs (success or failure) | `jsonPayload.message=~"^finco_daily_stats_\|^tiktok_reminder_"` |
| Unhandled handler exceptions | `jsonPayload.message="handler_raised"` |

## Alerting

`infra/terraform/logging.tf` provisions:

- A **log-based metric** `something-really-bot-cloudrun-errors`
  counting `severity>=ERROR` lines on this service.
- An **email notification channel** (when `var.alerts_email` is set —
  empty by default so applies in fresh projects don't 4xx on missing
  notification routing).
- An **alert policy** that fires when the metric exceeds
  `var.alerts_error_threshold` (default 5) over
  `var.alerts_error_window_seconds` (default 300 = 5 min).

Configure by setting `alerts_email` in `environments/prod.tfvars`. The
metric is created regardless so it accumulates history.

## What's not in this iteration

Per #28 acceptance criteria — explicit out-of-scope:

- SLOs and on-call rotations.
- External observability vendors (Datadog, Grafana Cloud, etc.).
- Full dashboards with QPS / p95 latency / cold-start counts. Cloud
  Run's built-in metrics page covers the basics interactively; a
  Terraform-managed dashboard is a follow-up.
