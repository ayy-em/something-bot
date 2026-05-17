# FinCo daily stats (#25)

Single daily Telegram message reporting per-site website performance
across all configured sites.

## Flow

```
10:30 Europe/Amsterdam
  Cloud Scheduler (something-bot-finco-daily-stats, OIDC)
    → POST /jobs/finco-daily-stats on Cloud Run
        → FinCoDailyStatsJob.run(ctx)
            → for each site in SITES: GA4 + GSC in parallel
            → compose digest with per-source/per-site degradation
            → send to settings.something_group_chat_id
            → persist ResponseRecord(response_type="scheduled_finco_daily_stats")
```

## Data sources

| Source | Module | Surfaces |
|---|---|---|
| GA4 Data API | `source/google_analytics.py` | `totalUsers`, `newUsers`, top-5 by `screenPageViews` |
| Google Search Console (`webmasters` v3) | `source/google_search_console.py` | `clicks` |

Both wrappers run the synchronous Google SDKs in a thread via
`asyncio.to_thread` so the FastAPI loop is never blocked. Failures are
funneled to dedicated exception types (`GoogleAnalyticsError`,
`GoogleSearchConsoleError`) so the handler can omit that source's lines
without failing the whole digest.

## Configuration

- Recipient chat: `Settings.something_group_chat_id`, sourced from the
  `SOMETHING_GROUP_CHAT_ID` Secret Manager secret (Cloud Run injects via
  `--set-secrets`).
- Sites: `sites.py` — adding a site = adding one `SiteConfig` entry. No
  schema change.

## Adding a site

1. In `sites.py`, append a `SiteConfig(...)` with the GA4 property ID
   and the GSC site URL (`sc-domain:...` for domain properties or the
   verified `https://...` form for URL-prefix properties).
2. Manually grant the Cloud Run runtime SA Viewer on the GA4 property
   and at least Restricted access in GSC. There is no Terraform for GA4
   admin or GSC permissions.

## Tests

- `tests/unit/features/test_finco_daily_stats_handler.py` covers the
  digest composition, per-source/per-site degradation, total-failure
  fallback, send failure, missing client, and persistence-failure
  swallowing.
- `tests/unit/features/test_finco_daily_stats_sources.py` covers the
  GA4 and GSC wrappers with fake clients.
