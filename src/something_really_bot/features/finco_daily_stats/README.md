# FinCo daily stats (#25)

Single daily Telegram message reporting per-site website performance
across all configured sites.

## Flow

```
10:30 Europe/Amsterdam
  Cloud Scheduler (something-bot-finco-daily-stats, OIDC)
    → POST /jobs/finco-daily-stats on Cloud Run
        → FinCoDailyStatsJob.run(ctx)
            → for each site in SITES: GA4 in parallel
            → compose digest with per-site degradation
            → send to settings.something_group_chat_id
            → persist ResponseRecord(response_type="scheduled_finco_daily_stats")
```

## Data sources

| Source | Module | Surfaces |
|---|---|---|
| GA4 Data API | `source/google_analytics.py` | `totalUsers`, `newUsers`, top-5 by `screenPageViews` |

The wrapper runs the synchronous Google SDK in a thread via
`asyncio.to_thread` so the FastAPI loop is never blocked. Failures are
funneled to `GoogleAnalyticsError` so the handler can omit a site's
section without failing the whole digest.

Google Search Console is intentionally **not** integrated here. Adding
GSC requires a personal-OAuth refresh-token flow rather than the
runtime SA (Google's GSC UI rejects non-Google-account emails). That
work lives on the backlog — see the GSC integration issue linked from
SPEC.md.

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

- `tests/unit/features/test_finco_daily_stats_handler.py` covers the
  digest composition, per-site degradation, total-failure fallback,
  send failure, missing client, and persistence-failure swallowing.
- `tests/unit/features/test_finco_daily_stats_sources.py` covers the
  GA4 wrapper with a fake client.
