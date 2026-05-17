# Voice transcription (#43)

Transcribes Telegram voice memos: downloads the file, stores it in
GCS, transcribes via OpenAI `gpt-4o-transcribe`, generates a 1-3
sentence summary + 1 sentence emotion read in a single chat call,
and replies with the result pinned to the trigger message.

## Flow

```
incoming voice message (private, group, supergroup)
  → handler.matches() → True if VoiceContent
    → reject if duration > 10 min or file_size > 25 MB
    → ack: send "Transcribing your voice memo…" (reply_to=msg)
    → react: 👀 on trigger (best-effort)
    → schedule asyncio.create_task(_run_background)  # webhook returns 200
        ├── jobs.insert_pending(...)                     → row id
        ├── jobs.update_status(id, "downloading")
        ├── telegram.get_file_path + download_file       → bytes
        ├── jobs.update_status(id, "uploading")
        ├── gcs.upload at voice_transcription_requests/{chat}/{msg}/voice_{uniq}.ogg
        ├── jobs.update_status(id, "transcribing")
        ├── openai.audio.transcriptions.create(model="gpt-4o-transcribe")
        ├── jobs.update_status(id, "analyzing")
        ├── openai.chat.completions.create with JSON response_format
        │     → {"summary": "...", "emotion": "..."}
        ├── jobs.update_status(id, "sending")
        ├── telegram.send_message(formatted reply, reply_to=msg)
        ├── jobs.mark_succeeded(...)
        └── persistence.record_event("voice_transcription_succeeded", ...)
```

Failure at any step:

* mark row failed with `error_class`/`error_message`
* send a single user-visible error reply (matrix below)
* swallow further Telegram send failures so we don't loop on a broken chat

## Caps

| Limit               | Value          | Rationale                                                    |
| ------------------- | -------------- | ------------------------------------------------------------ |
| Duration            | 10 min         | Operator preference. Telegram voice memos can technically go to 60 min. |
| File size           | 25 MB          | Whisper / `gpt-4o-transcribe` request ceiling. 10 min of Opus voice is ~3-5 MB so this is defensive. |
| Transcribe timeout  | 60 s           | Whole-request OpenAI timeout.                                |
| Analysis timeout    | 25 s           | Short chat call.                                             |

Both caps short-circuit before the ack/reaction/background task — the
user gets one clear rejection reply and that's it.

## Reply format

```
Summary:
<i><1-3 sentence factual summary></i>
<i><1 sentence emotion read></i>

Transcript:
<blockquote><full transcript></blockquote>
```

Sent with `parse_mode="HTML"`. Summary and emotion read are italicized;
the transcript itself is wrapped in `<blockquote>` so Telegram renders
it as a real quote block. Free-form OpenAI output is `html.escape`-d
before interpolation so a stray `<` in a transcript doesn't break the
HTML parse.

Summary and emotion sit on adjacent lines (no blank line between) — the
emotion read is a one-liner extension of the summary in practice.

## Error matrix

| Failure                            | User-visible reply                                                                                              |
| ---------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `voice.duration > 600 s`           | `That voice memo is over the 10-minute limit. Try sending a shorter one.`                                       |
| `voice.file_size > 25 MB`          | `That voice memo is too large to transcribe. Try sending a shorter one.`                                        |
| `TelegramFileError` on download    | `Couldn't pull that voice memo from Telegram. Try sending it again in a moment.`                                |
| `TranscriptionError`               | `Couldn't transcribe that voice memo. The transcription service might be having a moment — try again shortly.` |
| `AnalysisError` (JSON parse, etc.) | `Transcribed your voice memo but couldn't summarize it. Try again shortly.`                                     |
| `OPENAI_API_KEY` missing           | `Voice transcription isn't configured right now. Logged for review.`                                            |
| GCS upload failure / catch-all     | `Something went wrong handling that voice memo. Logged.`                                                        |

## Visibility / restrictions

* Voice memos only — uploaded audio files (`.mp3`, `.m4a` as documents)
  are out of scope for v1. The `/vtt` command for explicit audio-file
  transcription in DMs is on the backlog (#55).
* Works in private chats, groups, and supergroups. Channel posts don't
  match.
* Public OpenAI API. No data residency guarantees beyond OpenAI's
  default policies — fine for a personal project, would need DPA review
  to ever connect to anything STX-related.

## Persistence

Postgres table `public.voice_transcription_jobs` (created idempotently
on first use). One row per attempt:

| Column                          | Notes                                                          |
| ------------------------------- | -------------------------------------------------------------- |
| `id` (BIGSERIAL)                | PK                                                             |
| `bot_id`                        | which bot received the memo                                    |
| `chat_id` / `user_id` / `message_id` | originating coordinates                                   |
| `telegram_file_id` / `telegram_file_unique_id` / `duration_seconds` / `file_size_bytes` / `mime_type` | source metadata |
| `status`                        | `pending → downloading → uploading → transcribing → analyzing → sending → succeeded` (or `failed`) |
| `gcs_object_path`               | populated on success                                           |
| `transcript`                    | full transcript text                                           |
| `summary` / `emotion`           | LLM output                                                     |
| `telegram_reply_message_id`     | the bot's reply message id                                     |
| `error_class` / `error_message` | failure reason (message truncated at 2000 chars)               |
| `created_at` / `updated_at`     | TIMESTAMPTZ                                                    |

Indexed on `(chat_id, created_at DESC)` for "what did we transcribe in
this chat lately" lookups.

Lives in `public` consistent with the convention #42 settled on.

## Dispatcher precedence

The handler is registered immediately after `FileStorageHandler` and
before the video downloader / OpenAI fallback. `FileStorageHandler` no
longer matches `VoiceContent` — voice belongs to this feature. Photo
and document uploads still route to `FileStorageHandler` as before.

## Manual prerequisites

None on top of what `#42` already required:

* `OPENAI_API_KEY` is the existing secret. No new secrets.
* No Terraform changes — Cloud Run resource bumps from #42 cover us.
* Postgres wiring from #31 is reused.

## Tests

* `tests/unit/features/test_voice_transcription_handler.py` — match
  rules across private/group/text, happy path in DM (ack + background
  ordering), happy path in group with correct chat_id/reply targeting,
  the four failure branches each route to the right user-visible
  message, transcriber-missing edge case, ack-failure swallowed.
* `tests/unit/features/test_voice_transcription_transcriber.py` —
  OpenAI wrapper: text-stripping, empty-text rejection, SDK errors
  wrapped, JSON parsing, missing-field rejection, non-JSON rejection.
