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
column. Upsert on set; select on query. Table is auto-created on first use.

## Error handling

- Invalid date format → reply with format hint.
- Postgres unavailable → reply with "failed to save/retrieve" message.
- No Postgres configured → reply with "storage not configured" message.

## Tests

`tests/unit/features/test_next_reunion_handler.py` — matching, set date,
invalid format, storage failures, query, not-set state, supergroup support.
