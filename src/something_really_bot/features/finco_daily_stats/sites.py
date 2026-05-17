"""Site configuration for the FinCo daily stats digest (#25).

Adding a new site = add an entry to :data:`SITES`. No schema change, no
code change. The handler iterates this list, fetches each source per
site, and assembles a single digest.

GA4 property IDs are what GA4 Data API calls take; ``ga4_account_id`` is
kept purely for human reference / audit-trail.

The ``gsc_site_url`` is the property identifier in Google Search Console.
Domain properties use the ``sc-domain:`` prefix; URL-prefix properties
use the verified ``https://...`` form. Pick whichever is verified for
the site.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SiteConfig:
    """One website tracked by the daily digest."""

    label: str
    domain: str
    ga4_property_id: str
    gsc_site_url: str
    ga4_account_id: str | None = None


SITES: tuple[SiteConfig, ...] = (
    SiteConfig(
        label="FinCo",
        domain="fintechcompass.net",
        ga4_account_id="166319291",
        ga4_property_id="280078425",
        gsc_site_url="sc-domain:fintechcompass.net",
    ),
    SiteConfig(
        label="SR.f",
        domain="somethingreally.fun",
        ga4_account_id="150671594",
        ga4_property_id="398135906",
        gsc_site_url="sc-domain:somethingreally.fun",
    ),
)
