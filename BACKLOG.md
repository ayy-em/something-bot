# Backlog

## Review /make_sticker performance and cost (due 2026-11-02)

**Context:** On 2026-10-19 `/make_sticker` gained rembg background removal
(`u2netp`) and the Cloud Run instance went from 1 GiB to 2 GiB
(`infra/terraform/variables.tf`), on the estimate that memory is ~10% of
the instance-based rate. A standalone sticker service was considered and
deferred; see "Options" in `docs/decisions/0004-eliminating-idle-billing.md`
for the split-services path it would fold into.

**Check:**
- **Applied?** Cloud Run revision actually shows 2 GiB (`terraform apply`
  is manual; the deploy workflow doesn't set memory).
- **Cost:** billing for the Cloud Run service, 14 days before vs after the
  apply. Expected ~+10%. Also `billable_instance_time`.
- **Memory:** Cloud Run memory-utilization p99/max, and any
  "Memory limit exceeded" / instance restarts in logs.
- **Latency + outcomes:** `make_sticker_finished` log lines: count,
  `outcome` mix, `total_ms` / `transform_ms` p50/p95, max
  `process_peak_rss_mb`. Also `make_sticker_succeeded` / `_failed` in
  `processing_events`.
- **Image/cold start:** deploy image size and cold-start latency vs before.

**Decide:**
- Peak RSS comfortably < 1.2 GB and stickers get used → consider switching
  to `u2net` for quality (`REMBG_MODEL` + Dockerfile session class).
- Barely used → consider dropping back to 1 GiB.
- Any OOM, or sticker work hurting other jobs → revisit the standalone
  service / split-services option.

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
