# Shared Postgres (#31)

The bot connects to a **shared Cloud SQL Postgres instance** that lives
in a different GCP project. All bot data lives under a dedicated
schema (default name: `something_bot`).

## Connection

The DSN is held in the `POSTGRES_DSN` Secret Manager secret in
`something-bot-338300` and injected into Cloud Run via `--set-secrets`
(see `.github/workflows/deploy.yml`).

DSN form:

```
postgres://<user>:<pass>@<host>:5432/<db>?sslmode=require
```

Or, with the Cloud SQL Auth Proxy sidecar / unix socket form:

```
postgres://<user>:<pass>@/<db>?host=/cloudsql/<project>:<region>:<instance>
```

## Cross-project IAM

Terraform here can't manage IAM on the other project. Out-of-band:

1. On the project that owns the Cloud SQL instance, grant
   `roles/cloudsql.client` to
   `something-bot-cloudrun-sa@something-bot-338300.iam.gserviceaccount.com`.
2. Create a database user inside the shared instance with `SELECT,
   INSERT, UPDATE, DELETE, USAGE` on the `something_bot` schema only.
3. Add the DSN to the `POSTGRES_DSN` Secret Manager secret in
   `something-bot-338300`.

## Bootstrap

`PostgresStorage.ensure_schema()` issues
`CREATE SCHEMA IF NOT EXISTS something_bot` so a freshly created DB user
with `CREATE` on the database lands in a usable state on first call.

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
