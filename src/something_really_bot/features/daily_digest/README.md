# Daily digest (#25, generalized in #54)

Single daily Telegram message reporting per-site website performance
across all configured sites, plus a tally of bot job invocations over
the trailing 24 hours.

## Flow

```
10:30 Europe/Amsterdam
  Cloud Scheduler (something-bot-daily-digest, OIDC)
    → POST /jobs/daily-digest on Cloud Run
        → DailyDigestJob.run(ctx)
            → for each site in SITES: GA4 + GSC in parallel
            → fetch 24h job tally from job_history_log
            → compose digest with per-site degradation
            → send to settings.something_group_chat_id
            → persist ResponseRecord(response_type="scheduled_daily_digest")
```

## Data sources

| Source | Module | Surfaces |
|---|---|---|
| GA4 Data API | `source/google_analytics.py` | `totalUsers`, `newUsers`, top-5 by `screenPageViews` |
| Google Search Console (#51) | `source/google_search_console.py` | `clicks`, `impressions` (whole-property totals) |
| `public.job_history_log` (#53) | `services.job_history.JobHistoryLogger.fetch_recent_summary` | per-job ok/failed counts over the last 24h |

Both website-data wrappers run the synchronous Google SDK in a thread
via `asyncio.to_thread` so the FastAPI loop is never blocked. For each
site, GA4 and GSC are fetched in parallel and degrade independently:

- GA4 failures are funneled to `GoogleAnalyticsError`.
- GSC failures are funneled to `GoogleSearchConsoleError`.
- A site's section is dropped **only if both sources fail**. If just
  one source is up, that source still renders.
- Postgres failures on the tally query degrade to an empty tally (with
  a warning) — the website sections still send.

GSC has no Admin API and its UI rejects service-account emails, so
GSC access is authenticated with a personal-OAuth refresh token
(scope `webmasters.readonly`), minted one-off via
`scripts/grant_gsc_refresh_token.py` and stored in three Secret
Manager secrets:

- `GOOGLE_OAUTH_SECRET_JSON` — the full Desktop OAuth client JSON
  (`installed.client_id` + `installed.client_secret`), parsed by
  `_build_service` at call time.
- `GOOGLE_OAUTH_CLIENT_ID` — operator-convenience mirror of the
  client_id (also lives inside `GOOGLE_OAUTH_SECRET_JSON`). Not read
  at runtime; provisioned so the Cloud Run `--set-secrets` injection
  succeeds if we want to wire it later.
- `GSC_OAUTH_REFRESH_TOKEN` — the long-lived personal refresh token.

The runtime SA needs `roles/secretmanager.secretAccessor` on all
three; Terraform's `cloudrun_*` IAM bindings cover this.

## Tally section

Rendered only when ``job_history_log`` has at least one row in the
trailing 24 hours. Rows are grouped by ``job_name`` and counted by
``status``, sorted by descending total then alphabetically by job
name:

```
Jobs (last 24h)
  video_downloader   3 ok, 1 failed
  voice_transcription  2 ok
  daily-digest       1 ok
```

If both succeeded and failed counts are non-zero, both are listed; a
zero count is omitted entirely. The section header is plain text (no
emoji) to keep the visual hierarchy: per-site sections own the
emoji-heavy stats; the tally is meant to be a glanceable footer.

## Configuration

- Recipient chat: `Settings.something_group_chat_id`, sourced from the
  `SOMETHING_GROUP_CHAT_ID` Secret Manager secret (Cloud Run injects via
  `--set-secrets`).
- Sites: `sites.py` — adding a site = adding one `SiteConfig` entry. No
  schema change.

## Granting GA4 Viewer to the runtime SA

GA4's Admin UI rejects service-account emails ("This email doesn't
match a Google Account"), but the Admin API accepts them. Use the
one-off script:

```bash
# Authenticate as a Google user with Administrator on the property:
gcloud auth application-default login \
  --scopes=https://www.googleapis.com/auth/analytics.manage.users,openid

# Grant Viewer for each property the digest pulls from:
uv run python scripts/grant_ga4_viewer.py \
  --property-id 280078425 \
  --sa-email something-bot-cloudrun-sa@something-bot-338300.iam.gserviceaccount.com

uv run python scripts/grant_ga4_viewer.py \
  --property-id 398135906 \
  --sa-email something-bot-cloudrun-sa@something-bot-338300.iam.gserviceaccount.com
```

A `200` with the created binding means it took. Subsequent runs return
the existing binding (idempotent).

## Adding a site

1. In `sites.py`, append a `SiteConfig(...)` with the GA4 property ID.
2. Run `scripts/grant_ga4_viewer.py` for the new property ID.

## Tests

- `tests/unit/features/test_daily_digest_handler.py` covers the digest
  composition, per-site degradation, total-failure fallback, send
  failure, missing client, persistence-failure swallowing, and the
  job-tally section (omission when empty, rendering + sorting when
  populated, Postgres-failure degradation).
- `tests/unit/features/test_daily_digest_sources.py` covers the GA4
  wrapper with a fake client.
