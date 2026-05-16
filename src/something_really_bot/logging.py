"""Minimal logging helper.

Cloud Run scrapes the container's stdout/stderr into Cloud Logging, so
plain :mod:`logging` is sufficient for the rebuild's first iterations.
Structured JSON output, log sinks, and Cloud Monitoring alerts are tracked
in the dedicated backlog issue.
"""

import logging

_DEFAULT_FORMAT = "%(levelname)s %(name)s %(message)s"


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger for ``name``.

    The first call installs a basic stream handler at INFO level. Cloud
    Run treats stdout lines as log entries and recognises ``severity``
    levels embedded in the message; until #28 lands we rely on the textual
    level marker.
    """
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format=_DEFAULT_FORMAT)
    return logging.getLogger(name)
