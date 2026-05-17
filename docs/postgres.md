# Shared Postgres (#31)

The bot connects to a **shared Cloud SQL Postgres instance** that lives
in a different GCP project. All bot data lives under a dedicated
schema (default name: `something_bot`).

## Connection

Two secrets in `something-bot-338300` drive the wiring:

- `POSTGRES_DSN` — psycopg DSN with the bot's user, password and
  database name. The host:port in the DSN is only used for local
  development against a TCP-reachable instance; in Cloud Run the
  wrapper overrides it (see below).
- `POSTGRES_INSTANCE` — Cloud SQL instance connection name in the
  canonical `project:region:instance` form. Read at deploy time by
  `.github/workflows/deploy.yml` and passed to
  `gcloud run deploy --add-cloudsql-instances` so Cloud Run mounts the
  Cloud SQL Auth Proxy socket at `/cloudsql/<conn>/`. Also injected
  into the runtime environment via `--set-secrets` so the wrapper can
  point psycopg at the socket.

DSN form (any of these work — the host portion is ignored at runtime
when `POSTGRES_INSTANCE` is set):

```
postgres://<user>:<pass>@<host>:5432/<db>
postgres://<user>:<pass>@/<db>
```

How the wrapper routes to the socket: psycopg `connect()` accepts a
`host=` kwarg that overrides whatever host is in the DSN string.
`PostgresStorage._default_factory` passes
`host=/cloudsql/<POSTGRES_INSTANCE>` whenever the instance is set, so
the same DSN string works for local TCP development and for Cloud Run
without any conditional DSN rewriting.

Locally (no `POSTGRES_INSTANCE`), psycopg uses the DSN host:port as-is.

## Cross-project IAM

Terraform here can't manage IAM on the project that owns the Cloud SQL
instance. Out-of-band, on that project:

1. Grant `roles/cloudsql.client` to
   `something-bot-cloudrun-sa@something-bot-338300.iam.gserviceaccount.com`
   so the runtime SA can open Auth Proxy connections.
2. Create a database inside the shared instance for the bot.
3. Create a database user for the bot with `USAGE` on the schemas it
   needs and the appropriate DML grants. `CREATE` on the database is
   only required if you want `ensure_schema()` to create the
   `something_bot` schema on first run; otherwise pre-create the
   schema and grant DML on it.
4. Put the DSN into `POSTGRES_DSN` and the instance connection name
   into `POSTGRES_INSTANCE` in `something-bot-338300`'s Secret
   Manager.

## Bootstrap

`PostgresStorage.ensure_schema()` issues
`CREATE SCHEMA IF NOT EXISTS something_bot`. Call sites that need a
schema before writing should invoke it once on startup. If the bot's
DB user lacks `CREATE` on the database, the call will fail — in that
case create the schema out-of-band and skip `ensure_schema()`.

## Wrapper API

```python
from something_really_bot.persistence.postgres import get_postgres_storage

pg = get_postgres_storage()
if pg is not None:
    await pg.ensure_schema()
    await pg.insert_row("reminders", {"chat_id": 42, "fire_at": dt})
    rows = await pg.fetch_all(
        'SELECT * FROM "something_bot"."reminders" WHERE chat_id = %s',
        (42,),
    )
```

All methods funnel driver failures into `PostgresError`. The underlying
psycopg driver is synchronous; calls hop through `asyncio.to_thread` so
they don't block the FastAPI event loop.

Connections are short-lived (one per call). At our QPS that's fine; if
this grows, swap in a pool here without touching call sites.
