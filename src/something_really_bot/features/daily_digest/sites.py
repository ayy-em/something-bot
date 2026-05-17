"""Site configuration for the daily digest (#25, #54).

Adding a new site = add an entry to :data:`SITES`. No schema change,
no code change. The handler iterates this list, fetches GA4 + GSC per
site in parallel, and assembles a single digest.

GA4 property IDs are what GA4 Data API calls take; ``ga4_account_id``
is kept purely for human reference / audit-trail. ``gsc_site_url`` is
the property identifier the GSC API expects — for domain properties
it's ``sc-domain:<bare-domain>`` (#51). Sites without GSC verification
can leave ``gsc_site_url=None`` and the digest will omit their GSC
line.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SiteConfig:
    """One website tracked by the daily digest."""

    label: str
    domain: str
    ga4_property_id: str
    ga4_account_id: str | None = None
    gsc_site_url: str | None = None


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
