"""Grant a service account Viewer access on a GA4 property (#25).

GA4's admin UI rejects service-account emails with "This email doesn't
match a Google Account", but the Admin API still accepts them. Run
this once per (property, service account) pair.

Pre-requisites:

1. You have **Administrator** access on the GA4 property.
2. ADC is set up with the user-management scope:

       gcloud auth application-default login \\
         --scopes=https://www.googleapis.com/auth/analytics.manage.users,openid

   (Plain `gcloud auth login` is not enough — the user-management
   scope is not in the default set.)

3. The Google Analytics Admin SDK is available:

       uv run python scripts/grant_ga4_viewer.py ...

Usage:

    uv run python scripts/grant_ga4_viewer.py \\
      --property-id 280078425 \\
      --sa-email something-bot-cloudrun-sa@something-bot-338300.iam.gserviceaccount.com

Idempotent: if the binding already exists, the API returns it instead
of raising.
"""

from __future__ import annotations

import argparse
import sys

VIEWER_ROLE = "predefinedRoles/viewer"


def grant_viewer(property_id: str, sa_email: str) -> str:
    # Deferred so the SDK import cost only lands when the script actually runs.
    from google.analytics.admin import AccessBinding, AnalyticsAdminServiceClient

    client = AnalyticsAdminServiceClient()
    binding = AccessBinding(user=sa_email, roles=[VIEWER_ROLE])
    response = client.create_access_binding(
        parent=f"properties/{property_id}",
        access_binding=binding,
    )
    return response.name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Grant a SA Viewer on a GA4 property.")
    parser.add_argument(
        "--property-id",
        required=True,
        help="GA4 property numeric id (e.g. 280078425).",
    )
    parser.add_argument(
        "--sa-email",
        required=True,
        help="Service account email to grant Viewer to.",
    )
    args = parser.parse_args(argv)

    try:
        binding_name = grant_viewer(args.property_id, args.sa_email)
    except Exception as exc:  # noqa: BLE001 — print and exit
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"OK: {binding_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
