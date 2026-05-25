# Daily Weather Forecast (#58)

Scheduled job that sends a daily weather message to the group chat.

## Schedule

Cloud Scheduler fires `POST /jobs/daily-weather` at **05:05 UTC** every day
(= 08:05 Moscow / 07:05 Amsterdam during CEST).

## Message content

1. **Weather** for Amsterdam and Moscow — daily high/low, feels-like temp,
   condition, wind, humidity, sunrise/sunset.
2. **Reunion countdown** — days until the next reunion date (set via
   `/next-reunion`). Special milestone sentences at 14/7/3/2/1/0 days.
3. **EUR/RUB exchange rate** — last available rate from open.er-api.com.
4. **"This day in history"** — random event from the Wikimedia REST API.

## Data sources

| Source | Module | Needs API key? |
| --- | --- | --- |
| Open-Meteo forecast API | `sources/open_meteo.py` | No |
| open.er-api.com | `sources/fx_rates.py` | No |
| Wikimedia On This Day | `sources/wikipedia_otd.py` | No |
| Postgres `public.reunion_date` | `reunion.py` | N/A |

## Graceful degradation

Each source is fetched independently. A failure in any single source omits
that section from the message — the rest still sends. If every source fails,
the message reads "No data available today."

## QA variant

A `daily-weather-qa` job sends the same message as a DM to JM's chat
(extracted from the `JM_TG_ID` key of the `telegram-qa-users` secret).
Triggerable on demand via the **Daily Weather QA** GitHub Actions workflow
(`workflow_dispatch`). The deployer SA is authorized alongside the
scheduler SA via `SCHEDULER_ADDITIONAL_EMAILS`.

## Configuration

Reuses `SOMETHING_GROUP_CHAT_ID` (same recipient as the daily digest).
The QA variant reads `jm_chat_id` from the existing `TELEGRAM_QA_USERS`
secret — no new secrets required.

## Files

```
features/daily_weather/
├── handler.py          # DailyWeatherJob (JobHandler protocol)
├── cities.py           # CityConfig dataclass + CITIES tuple
├── reunion.py          # Postgres storage + countdown formatting
└── sources/
    ├── open_meteo.py   # Weather fetch
    ├── fx_rates.py     # EUR/RUB rate fetch
    └── wikipedia_otd.py# On This Day fetch
```

## Tests

`tests/unit/features/test_daily_weather_handler.py` — happy path, per-source
failures, all-sources-fail, missing chat id, send failure, persistence
failure, milestone messages, negative temperatures.
