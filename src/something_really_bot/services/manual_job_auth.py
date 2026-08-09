"""Token verification for hand-invoked job calls.

``POST /jobs/<name>`` is gated by Cloud Scheduler's OIDC token (see
:mod:`something_really_bot.services.scheduler_auth`). A browser cannot
mint one, so the break-glass ``GET /jobs/<name>?token=…`` route uses a
shared secret from Secret Manager instead.

This exists mainly for ensure-webhook, which is no longer scheduled: the
15-minute cadence held a Cloud Run instance open around the clock under
instance-based billing. See ``docs/decisions/0003-manual-ensure-webhook.md``.
"""

import hmac
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from something_really_bot.config import Settings, get_settings
from something_really_bot.logging import get_logger

_logger = get_logger(__name__)


def verify_manual_job_token(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """FastAPI dependency: 401 unless ``?token=`` matches ``MANUAL_JOB_TOKEN``."""
    expected = settings.manual_job_token
    if expected is None:
        _logger.warning("manual_job_token_missing_config_rejecting_jobs_call")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Manual job token not configured.",
        )

    # Strip the stored value: `gcloud secrets versions add --data-file=-`
    # keeps stdin verbatim, so a secret piped from `openssl rand` carries a
    # trailing newline that would never appear in a pasted URL.
    supplied = request.query_params.get("token", "")
    if not hmac.compare_digest(supplied, expected.get_secret_value().strip()):
        _logger.warning("manual_job_token_mismatch", extra={"path": request.url.path})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid manual job token.",
        )
