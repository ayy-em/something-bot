"""Tiny wrapper around the shared Cloud SQL Postgres instance (#31).

The DB lives in a different GCP project. The bot connects via the Cloud
SQL Auth Proxy: Cloud Run mounts the proxy socket at
``/cloudsql/<instance-connection-name>/`` (configured by
``gcloud run deploy --add-cloudsql-instances=<conn>``) and authenticates
with the runtime SA's IAM, which holds ``roles/cloudsql.client`` on the
owning project.

Two secrets drive the wiring:

- ``POSTGRES_DSN``     — psycopg connection string with the bot's
  user/password/database. The host:port in the DSN is intentionally
  **ignored at runtime**; it's there so the same DSN works for local
  TCP connections during development.
- ``POSTGRES_INSTANCE`` — Cloud SQL instance connection name
  (``project:region:instance``). When present, the wrapper overrides
  the DSN's host with ``/cloudsql/<POSTGRES_INSTANCE>`` so psycopg
  routes through the Auth Proxy socket regardless of what the DSN says.
  Unset locally → psycopg uses the DSN's host:port as-is.

The wrapper is deliberately small:

- ``PostgresStorage.ensure_schema()`` issues ``CREATE SCHEMA IF NOT EXISTS``.
- ``PostgresStorage.execute(sql, params)`` runs a non-returning statement.
- ``PostgresStorage.fetch_all(sql, params)`` returns ``list[dict[str, Any]]``.
- ``PostgresStorage.insert_row(table, row)`` inserts a dict, schema-qualified.

The synchronous ``psycopg`` driver runs inside ``asyncio.to_thread`` so
the FastAPI event loop stays free, matching the pattern used by the GCS
and GA4 wrappers.

Errors are funneled into :class:`PostgresError`; callers decide whether
to surface or swallow per the SPEC §6.9 "never bubble to Telegram" rule.
"""

import asyncio
from collections.abc import Callable, Mapping
from functools import lru_cache
from typing import Any

from pydantic import SecretStr

from something_really_bot.config import get_settings
from something_really_bot.logging import get_logger

_logger = get_logger(__name__)

ConnectionFactory = Callable[[], Any]


class PostgresError(Exception):
    """Raised on any Postgres driver/connection failure."""


class PostgresStorage:
    """Sync-Postgres helper wrapped for the asyncio event loop."""

    def __init__(
        self,
        dsn: SecretStr,
        *,
        schema: str = "something_bot",
        instance_connection_name: str | None = None,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        self._dsn = dsn
        self._schema = schema
        self._instance = instance_connection_name
        self._factory = connection_factory or self._default_factory

    @property
    def schema(self) -> str:
        return self._schema

    async def ensure_schema(self) -> None:
        """``CREATE SCHEMA IF NOT EXISTS <schema>`` — idempotent."""
        await self._run(
            lambda conn: self._exec(
                conn,
                f'CREATE SCHEMA IF NOT EXISTS "{self._schema}"',
                params=(),
            )
        )

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        await self._run(lambda conn: self._exec(conn, sql, params))

    async def fetch_all(
        self, sql: str, params: tuple[Any, ...] = ()
    ) -> list[dict[str, Any]]:
        return await self._run(lambda conn: self._fetch(conn, sql, params))

    async def insert_row(self, table: str, row: Mapping[str, Any]) -> None:
        """Insert ``row`` into ``<schema>.<table>``.

        Column names are quoted with double-quotes to preserve casing;
        values pass through psycopg parameter substitution so SQL
        injection from values isn't possible. ``table`` is rejected if
        it contains anything but ASCII letters / digits / underscores.
        """
        if not _is_safe_identifier(table):
            raise PostgresError(f"Unsafe table identifier: {table!r}")
        if not row:
            raise PostgresError("insert_row: row must not be empty")

        columns = ", ".join(f'"{c}"' for c in row)
        placeholders = ", ".join(["%s"] * len(row))
        sql = (
            f'INSERT INTO "{self._schema}"."{table}" ({columns}) '
            f"VALUES ({placeholders})"
        )
        await self.execute(sql, tuple(row.values()))

    # --------------------------------------------------------------- #
    # Internals
    # --------------------------------------------------------------- #

    async def _run(self, work: Callable[[Any], Any]) -> Any:
        try:
            return await asyncio.to_thread(self._run_sync, work)
        except PostgresError:
            raise
        except Exception as exc:  # noqa: BLE001 — funnel all driver errors
            _logger.warning(
                "postgres_call_failed",
                extra={"exception_type": type(exc).__name__},
            )
            raise PostgresError(str(exc)) from exc

    def _run_sync(self, work: Callable[[Any], Any]) -> Any:
        conn = self._factory()
        try:
            try:
                result = work(conn)
                conn.commit()
                return result
            except Exception:
                conn.rollback()
                raise
        finally:
            conn.close()

    @staticmethod
    def _exec(conn: Any, sql: str, params: tuple[Any, ...]) -> None:
        with conn.cursor() as cur:
            cur.execute(sql, params)

    @staticmethod
    def _fetch(conn: Any, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            columns = [d.name for d in (cur.description or [])]
            return [dict(zip(columns, row, strict=False)) for row in cur.fetchall()]

    def _default_factory(self) -> Any:
        # Deferred so test doubles don't need psycopg installed.
        import psycopg

        kwargs: dict[str, Any] = {"autocommit": False}
        if self._instance:
            # Route through the Cloud SQL Auth Proxy socket; psycopg
            # kwargs override the host:port in the DSN string, so the
            # same DSN works locally (TCP) and in Cloud Run (socket).
            kwargs["host"] = f"/cloudsql/{self._instance}"
        return psycopg.connect(self._dsn.get_secret_value(), **kwargs)


def _is_safe_identifier(name: str) -> bool:
    return bool(name) and all(c.isalnum() or c == "_" for c in name)


@lru_cache(maxsize=1)
def get_postgres_storage() -> PostgresStorage | None:
    """Return the process-wide :class:`PostgresStorage`, or ``None`` if no DSN."""
    settings = get_settings()
    if settings.postgres_dsn is None:
        return None
    instance = (
        settings.postgres_instance.get_secret_value()
        if settings.postgres_instance is not None
        else None
    )
    return PostgresStorage(
        dsn=settings.postgres_dsn,
        schema=settings.postgres_schema,
        instance_connection_name=instance,
    )
