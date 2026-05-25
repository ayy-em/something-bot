"""OIDC-token verification for incoming Cloud Scheduler calls (#22).

Cloud Scheduler is configured (in Terraform) to attach an OIDC token to
every call to ``/jobs/<name>``. The token is a Google-signed JWT whose
``email`` claim is the scheduler service account's email. We verify the
signature against Google's public keys and check the email matches
``Settings.scheduler_service_account_email``; anything else is 401/403.

The Cloud Run service itself is publicly invocable
(``allUsers: roles/run.invoker``) because Telegram needs to POST the
webhook without auth, so we cannot rely on platform-level IAM to gate
the ``/jobs/*`` route — application-level verification is the trust
anchor here.
"""

from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from something_really_bot.config import Settings, get_settings
from something_really_bot.logging import get_logger

_logger = get_logger(__name__)


def _verify_token(token: str) -> dict[str, Any]:
    """Verify a Google-issued OIDC token; return the claims dict.

    Audience is *not* enforced here — Cloud Scheduler defaults the
    audience to the target URL, which we can't compute statically
    without coupling the service to its own Cloud Run URL. The email
    claim is the AuthZ anchor instead.
    """
    return id_token.verify_oauth2_token(token, google_requests.Request())


def verify_scheduler_oidc_token(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """FastAPI dependency: 401 unless the request carries a valid OIDC
    token issued for the configured scheduler service account."""
    expected_email = settings.scheduler_service_account_email
    if not expected_email:
        _logger.warning("scheduler_oidc_missing_config_rejecting_jobs_call")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Scheduler OIDC not configured.",
        )

    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing OIDC bearer token.",
        )

    token = auth_header[7:].strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Empty OIDC bearer token.",
        )

    try:
        claims = _verify_token(token)
    except Exception as exc:  # noqa: BLE001 — google-auth raises many subclasses
        _logger.warning("scheduler_oidc_verify_failed", extra={"error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid OIDC token.",
        ) from exc

    allowed_emails: set[str] = {expected_email}
    extra_csv = settings.scheduler_additional_emails
    if extra_csv:
        allowed_emails.update(e.strip() for e in extra_csv.split(",") if e.strip())

    if claims.get("email") not in allowed_emails:
        _logger.warning("scheduler_oidc_wrong_email", extra={"got": claims.get("email")})
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="OIDC token issued for unexpected service account.",
        )
