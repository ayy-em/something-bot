#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "google-auth-oauthlib>=1.2,<2",
# ]
# ///
"""One-off OAuth flow to mint a personal-account GSC refresh token (#51).

The Cloud Run runtime SA can't be added as a Search Console user
(Google's GSC UI rejects non-Google-account emails and there's no Admin
API), so GSC access has to ride on a personal-OAuth refresh token
issued by a user who already has property access. This script runs
that one-off flow on your laptop and prints the resulting refresh token
plus the `gcloud secrets` commands needed to wire it into the bot.

Pre-requisites
--------------

1. **OAuth consent screen configured** for the `something-bot-338300`
   GCP project: APIs & Services → OAuth consent screen.
   - User type: External (or Internal if your Google account is in a
     Workspace org).
   - Publishing status: Testing is fine for a single-user setup; add
     your own Google account under "Test users".
   - Scopes: just add `.../auth/webmasters.readonly` here too so the
     consent screen shows the right scope.

2. **OAuth 2.0 Client ID of type "Desktop app"** created in the same
   project: APIs & Services → Credentials → Create credentials →
   OAuth client ID → Application type "Desktop app".
   Download the JSON ("DOWNLOAD JSON" button) — that's the
   `--client-secrets-file` you pass below.

3. **Search Console access** for the Google account you'll log in as.
   Verify you can open https://search.google.com/search-console/ and
   see both `sc-domain:fintechcompass.net` and
   `sc-domain:somethingreally.fun`.

Usage
-----

    uv run scripts/grant_gsc_refresh_token.py \\
      --client-secrets-file ~/Downloads/client_secret_xxx.json

The script will open a browser, you'll log in as the Google account
that owns the GSC properties, approve the
`auth/webmasters.readonly` scope, and the browser will redirect to a
localhost page confirming success. The terminal will then print the
refresh token + the exact Secret Manager commands to store it.

The refresh token is long-lived but Google can revoke it if you
change your password or explicitly revoke access at
https://myaccount.google.com/permissions. Re-run this script to mint
a new one.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"

# Secret names the bot's GSC reader expects. Keep these aligned with
# the Terraform module that creates the secrets (Secret Manager
# resource names are mirrored verbatim in env vars on Cloud Run).
#
# Note: the runtime only needs GOOGLE_OAUTH_SECRET_JSON (the full
# Desktop client JSON, from which client_id + client_secret are
# parsed) and GSC_OAUTH_REFRESH_TOKEN. GOOGLE_OAUTH_CLIENT_ID is a
# standalone secret kept for operator convenience and isn't consumed
# at runtime, but the script still emits it so the Secret Manager
# inventory matches Terraform.
SECRET_NAMES = {
    "client_json": "GOOGLE_OAUTH_SECRET_JSON",
    "client_id": "GOOGLE_OAUTH_CLIENT_ID",
    "refresh_token": "GSC_OAUTH_REFRESH_TOKEN",
}

DEFAULT_PROJECT_ID = "something-bot-338300"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mint a personal-OAuth refresh token for Google Search Console.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--client-secrets-file",
        required=True,
        type=Path,
        help=(
            "Path to the downloaded OAuth client JSON for a Desktop "
            "client (created in GCP Console → Credentials)."
        ),
    )
    parser.add_argument(
        "--project-id",
        default=DEFAULT_PROJECT_ID,
        help=f"GCP project id for the printed gcloud commands (default: {DEFAULT_PROJECT_ID}).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Local port the OAuth callback listens on. Default: 0 (random).",
    )
    return parser.parse_args()


def _load_client_secrets(path: Path) -> dict[str, str]:
    if not path.exists():
        sys.exit(f"client-secrets-file not found: {path}")
    try:
        raw = path.read_text()
        blob = json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.exit(f"client-secrets-file is not valid JSON: {exc}")

    # The Desktop client JSON wraps the actual values under "installed".
    inner = blob.get("installed") or blob.get("web") or {}
    client_id = inner.get("client_id")
    client_secret = inner.get("client_secret")
    if not client_id or not client_secret:
        sys.exit(
            "client-secrets-file did not contain a client_id + client_secret "
            "under 'installed' / 'web'. Did you download the right JSON? "
            "It must be an OAuth 2.0 Client ID of application type 'Desktop app'."
        )
    return {"client_id": client_id, "client_secret": client_secret, "client_json": raw}


def _run_oauth_flow(client_secrets_path: Path, port: int) -> str:
    """Run the InstalledAppFlow and return the refresh token."""
    # Imported lazily so `--help` works even if google-auth-oauthlib
    # isn't installed (relevant when uv-script metadata hasn't resolved
    # yet on the very first invocation).
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secrets_path),
        scopes=[GSC_SCOPE],
    )
    # ``access_type=offline`` + ``prompt=consent`` together guarantee a
    # refresh_token is returned (Google only emits a fresh refresh
    # token when consent is re-shown).
    credentials = flow.run_local_server(
        port=port,
        access_type="offline",
        prompt="consent",
        open_browser=True,
        success_message=("Auth complete. Refresh token captured. You can close this tab."),
    )

    refresh_token = credentials.refresh_token
    if not refresh_token:
        sys.exit(
            "OAuth flow completed but no refresh_token was returned. This "
            "usually means you've already granted consent for this client + "
            "account before and Google reused a previous grant. Revoke the "
            "app at https://myaccount.google.com/permissions and re-run."
        )
    return refresh_token


def _print_next_steps(
    project_id: str,
    client_id: str,
    client_json: str,
    refresh_token: str,
) -> None:
    print()
    print("=" * 72)
    print("Refresh token captured.")
    print("=" * 72)
    print()
    print("Run these three commands to store everything in Secret Manager.")
    print("Each command is idempotent: re-running creates a new version.")
    print()
    for secret_key, secret_name in SECRET_NAMES.items():
        value = {
            "client_json": client_json,
            "client_id": client_id,
            "refresh_token": refresh_token,
        }[secret_key]
        # ``--data-file=-`` reads from stdin, which avoids leaking the
        # secret into shell history. We pipe via ``printf`` so newlines
        # aren't appended.
        cmd = (
            f"printf %s {shlex.quote(value)} | "
            f"gcloud secrets versions add {secret_name} "
            f"--project={project_id} --data-file=-"
        )
        print(f"  # {secret_name}")
        print(f"  {cmd}")
        print()
    print("If the secrets don't exist yet, create them first with:")
    print()
    for secret_name in SECRET_NAMES.values():
        print(
            f"  gcloud secrets create {secret_name} "
            f"--project={project_id} --replication-policy=automatic"
        )
    print()
    print("Terraform will pick the secrets up from there on the next apply.")


def main() -> None:
    args = _parse_args()
    client = _load_client_secrets(args.client_secrets_file)
    refresh_token = _run_oauth_flow(args.client_secrets_file, args.port)
    _print_next_steps(
        project_id=args.project_id,
        client_id=client["client_id"],
        client_json=client["client_json"],
        refresh_token=refresh_token,
    )


if __name__ == "__main__":
    main()
