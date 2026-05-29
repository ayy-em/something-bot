# Backlog

## Residential proxy support for video downloader

**Context:** TikTok aggressively blocks cloud-provider IP ranges (AWS, GCP, Azure). The bot runs on AWS Lambda, so even with browser-like headers and User-Agent rotation, TikTok can still reject requests based on source IP alone. This is the single largest factor behind the frequent "Couldn't fetch this video" errors.

**Proposal:** Add optional residential proxy routing for yt-dlp requests via the `proxy` yt-dlp option.

**Implementation outline:**
- Add an optional `VIDEO_DOWNLOADER_PROXY_URL` env var (stored in Secrets Manager alongside the bot token).
- When set, pass `"proxy": proxy_url` in `ydl_opts` so all yt-dlp HTTP traffic routes through the proxy.
- Services to evaluate: Bright Data, Oxylabs, SmartProxy — all offer rotating residential IPs with pay-per-GB pricing.
- Consider platform-conditional proxying: only route TikTok downloads through the proxy (Instagram may not need it), to minimize proxy bandwidth costs.

**Trade-offs:**
- Cost: residential proxy traffic is billed per GB (~$8–15/GB depending on provider). Video downloads are bandwidth-heavy.
- Latency: adds a hop. Acceptable given the user already waits for a download.
- Operational: proxy credentials need rotation/monitoring; provider outages become a new failure mode.
- Compliance: confirm with Legal/Compliance that routing through residential proxies aligns with TikTok's ToS posture and any applicable regulations.
