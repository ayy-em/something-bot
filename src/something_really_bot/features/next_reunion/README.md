# /next-reunion — Reunion Countdown Command (#58)

Sets or queries the next reunion date. The date is stored in Postgres
and consumed by the daily message job to render a countdown.

## Usage

- `/next-reunion 2026-06-15` — set the date.
- `/next-reunion` — show the current date and countdown (or "not yet known").

## Chat types

Works in private chats, groups, and supergroups.

## Storage

Single-row Postgres table `public.reunion_date` with a `target_date DATE`
and optional `duration_days INTEGER` column. Upsert on set; select on query.
Table and columns are auto-created on first use.

## Error handling

- Invalid date format → reply with format hint.
- Postgres unavailable → reply with "failed to save/retrieve" message.
- No Postgres configured → reply with "storage not configured" message.

## Tests

`tests/unit/features/test_next_reunion_handler.py` — matching, set date,
invalid format, storage failures, query, not-set state, supergroup support.

# /next-reunion-duration — Reunion Duration Command (#60)

Sets or queries how many days the next reunion lasts. When duration is
stored, the daily message shows "enjoying time together" for the full
reunion period, then falls back to "next date unknown" once it ends.

## Usage

- `/next-reunion-duration 5` — set the duration to 5 days.
- `/next-reunion-duration` — show the current duration (or "not set").

## Daily message behavior

1. **Before reunion** — normal countdown ("Your next reunion is in N days.").
2. **During reunion** (target through target + duration - 1) — "You are enjoying time together right now! <3".
3. **After reunion** (target + duration onwards) — "The next reunion date is not yet known :(". 

Without duration set, existing behavior is unchanged.

## Tests

`tests/unit/features/test_next_reunion_duration_handler.py` — matching, set
duration, validation, no-date guard, storage failures, query.

`tests/unit/features/test_daily_message_handler.py` — enjoying/expired/
countdown-with-duration/duration-fetch-failure integration tests.
