# Video downloader (#42)

Detects Instagram Reel and TikTok URLs in incoming text messages,
downloads the video via `yt-dlp`, stores it in GCS, and replies with
the video pinned to the trigger message.

## Flow

```
incoming text message
  → detector.detect(text) → (url, platform) | None
    → ack: send "Link received, fetching the {Instagram|TikTok} video…"
    → react: 👀 on trigger message (best-effort)
    → schedule asyncio.create_task(_run_background)  # webhook returns 200 immediately
        ├── jobs.insert_pending(...)        → row id
        ├── jobs.update_status(id, "downloading")
        ├── downloader.download(url)        → DownloadedVideo
        ├── jobs.update_status(id, "uploading")
        ├── gcs.upload_bytes(...) at video_downloads/{chat_id}/{message_id}/{filename}
        ├── jobs.update_status(id, "sending")
        ├── telegram.send_video(chat_id, path, reply_to=message_id, duration, ...)
        ├── jobs.mark_succeeded(id, ...)
        └── persistence.record_event("video_downloader_succeeded", ...)
```

Failure at any step:

* mark row failed with `error_class`/`error_message`
* send a single user-visible error reply (see matrix below)
* swallow further send failures so we don't loop on a broken chat

Tempdir is always cleaned up with `shutil.rmtree(..., ignore_errors=True)`.

## URL detection

| Platform   | Patterns                                                |
| ---------- | ------------------------------------------------------- |
| Instagram  | `instagram.com/reel/<id>`, `instagram.com/reels/<id>`   |
| TikTok     | `tiktok.com/@user/video/<id>`, `vm.tiktok.com/<id>`, `vt.tiktok.com/<id>`, `tiktok.com/t/<id>` |

URLs are matched anywhere in the message text. Tracker query strings
(`?igsh=...`, `?_t=...`) are tolerated. Profile URLs and discover pages
are intentionally excluded — only actual video posts match.

If both an IG and a TikTok URL appear, IG wins (first match in the
regex chain).

## Error matrix

| Failure                                | User-visible reply                                                                                                  |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `VideoTooLargeError` (>50 MB)          | `That video is over Telegram's 50 MB upload limit for bots. Try a shorter clip.`                                    |
| `VideoDownloadError` (yt-dlp failed)   | `Couldn't fetch this video. {Instagram\|TikTok} might be rate-limiting our bot right now, or the post is private/deleted. Try again later.` |
| `TelegramSendError` on `send_video`    | `Downloaded the video but Telegram refused to send it back. Logged for review.`                                     |
| Anything else                          | `Something went wrong handling that video. Logged.`                                                                 |

TikTok's anti-scraping is the most common failure source; the message
deliberately calls that out instead of looking like a bot bug.

## Visibility / restrictions

* Public videos only. Private/login-walled Reels are out of scope —
  yt-dlp can't reach them without an authenticated cookie jar.
* Bot only acts in private chats, groups, and supergroups it's a member
  of. Channel posts are not matched.
* 50 MB cap is Telegram's bot upload ceiling for `sendVideo`. Larger
  source media is rejected at the yt-dlp layer (`max_filesize`) before
  we even start the upload.

## Persistence

Postgres table `public.video_download_jobs` (created idempotently on
first use). One row per attempt:

| Column                       | Notes                                              |
| ---------------------------- | -------------------------------------------------- |
| `id` (BIGSERIAL)             | PK                                                 |
| `bot_id`                     | which bot received the message                     |
| `chat_id` / `user_id` / `message_id` | originating coordinates                    |
| `source_url` / `platform`    | what we matched                                    |
| `status`                     | `pending → downloading → uploading → sending → succeeded` (or `failed` at any step) |
| `gcs_object_path`            | populated on success                               |
| `file_size_bytes`, `duration_seconds`, `telegram_video_message_id` | success metadata |
| `error_class`, `error_message` | failure reason (message truncated at 2000 chars) |
| `created_at` / `updated_at`  | TIMESTAMPTZ                                        |

Indexed on `(chat_id, created_at DESC)` for "what did we do in this
chat lately" lookups.

The table lives in `public` rather than `something_bot` per the
operator's #42 scope call; the storage module bypasses
`PostgresStorage`'s auto-schema-qualification by issuing raw SQL keyed
off a single `TABLE_FQN` constant.

## Manual prerequisites

The implementation lands behind two infrastructure changes that the
operator must apply once after the first push to master that contains
this feature:

1. **`terraform apply`** to land the Cloud Run resource bumps in
   `infra/terraform/variables.tf` (2 vCPU / 2 GiB / 300 s timeout /
   concurrency 8). yt-dlp + ffmpeg + a 50 MiB upload won't fit in the
   previous 1 vCPU / 512 MiB defaults.
2. The next push to `master` triggers a Docker rebuild that picks up
   `ffmpeg` from the updated `Dockerfile` and `yt-dlp` from
   `pyproject.toml`. No rollback story is needed — both are
   forward-only additions.

No new secrets. The Postgres wiring already added for #31 is reused.

## Tests

* `tests/unit/features/test_video_downloader_detector.py` — URL
  matching across IG/TikTok variants, no-match cases, IG-wins
  precedence.
* `tests/unit/features/test_video_downloader_handler.py` — happy path
  (DM and group), each error branch maps to the right user-visible
  message, ack-failure is swallowed, persistence event fires only on
  success.
* `tests/unit/telegram/test_client.py` — multipart `sendVideo` body,
  `setMessageReaction`, `reply_parameters` propagation on
  `sendMessage`.
