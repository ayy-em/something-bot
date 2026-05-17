"""Structured-JSON logging configured for Cloud Logging (#28).

Cloud Run scrapes the container's stdout into Cloud Logging. When a
single line of stdout is a JSON object containing a top-level
``severity`` field, Cloud Logging promotes it to the entry severity and
indexes the remaining fields as ``jsonPayload`` — making them queryable
in log filters and usable as labels for log-based metrics.

Reference: https://cloud.google.com/logging/docs/structured-logging

Loggers in this project always call ``get_logger(__name__).<level>(msg, extra={...})``;
the ``extra`` dict is merged into the JSON payload. Reserved Python
``LogRecord`` attributes are excluded automatically so callers never
have to think about clashes.
"""

import json
import logging
import os
from typing import Any

_SEVERITY_BY_LEVELNAME: dict[str, str] = {
    "DEBUG": "DEBUG",
    "INFO": "INFO",
    "WARNING": "WARNING",
    "ERROR": "ERROR",
    "CRITICAL": "CRITICAL",
}

# Attributes the logging module attaches to every LogRecord — we must
# not bubble them into the JSON payload as user-extras.
_RESERVED_LOGRECORD_ATTRS: frozenset[str] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


class StructuredJsonFormatter(logging.Formatter):
    """Render each :class:`logging.LogRecord` as a single JSON line.

    The output line contains, at minimum:

    - ``severity`` — Cloud Logging severity (DEBUG/INFO/WARNING/ERROR/CRITICAL).
    - ``message`` — the formatted message.
    - ``logger`` — the logger name.

    Any non-reserved attributes (i.e. things passed via ``extra=``) are
    merged at the top level so they're queryable via Cloud Logging's
    structured search (``jsonPayload.<key>``).
    """

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        payload: dict[str, Any] = {
            "severity": _SEVERITY_BY_LEVELNAME.get(record.levelname, record.levelname),
            "message": record.getMessage(),
            "logger": record.name,
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED_LOGRECORD_ATTRS:
                continue
            payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, default=str)


_DEFAULT_LEVEL = "INFO"


def configure_logging(level: str | None = None) -> None:
    """Install the structured JSON handler on the root logger.

    Idempotent: re-configuring replaces the active handlers so test
    teardown and module re-imports don't double-log.
    """
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredJsonFormatter())
    root.addHandler(handler)
    root.setLevel(level or os.getenv("LOG_LEVEL", _DEFAULT_LEVEL).upper())


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger for ``name``.

    The first call installs the structured-JSON handler on the root
    logger. Subsequent calls are cheap.
    """
    if not logging.getLogger().handlers:
        configure_logging()
    return logging.getLogger(name)
