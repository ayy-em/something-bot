"""Site configuration for the weekly website stats section.

Adding a new site = add an entry to :data:`SITES`. The section iterates
this list, fetches GA4 + GSC per site in parallel, and assembles a
weekly stats block with week-on-week comparison.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SiteConfig:
    """One website tracked by the weekly website stats."""

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
