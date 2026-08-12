# Voice transcription (#43, #56, #63)

Transcribes Telegram voice memos: downloads the file, stores it in
GCS, transcribes via OpenAI `gpt-transcribe`, optionally generates
a 1-3 sentence summary + 1 sentence emotion read in a single chat
call (only for memos over 2 minutes), and edits the in-flight
"Transcribing…" ack with the final reply.

Memos over 2 minutes also get roasted: a second chat call writes a
~25 word parody TL;DR in the speaker's own voice, TTS turns it into
Ogg/Opus, and the bot posts it as a voice message replying to the
original memo (#63).

## Flow

```
incoming voice message (private, group, supergroup)
  → handler.matches() → True if VoiceContent
    → reject if duration > 10 min or file_size > 25 MB
    → ack: send "Transcribing your voice memo…" (reply_to=msg)
                              ↳ capture ack_message_id
    → react: 👀 on trigger (best-effort)
    → schedule asyncio.create_task(_run_background)  # webhook returns 200
        ├── jobs.insert_pending(...)                     → row id
        ├── jobs.update_status(id, "downloading")
        ├── telegram.get_file_path + download_file       → bytes
        ├── jobs.update_status(id, "uploading")
        ├── gcs.upload at voice_transcription_requests/{chat}/{msg}/voice_{uniq}.ogg
        ├── jobs.update_status(id, "transcribing")
        ├── openai.audio.transcriptions.create(model="gpt-transcribe")
        ├── if voice.duration > 120s:  # asyncio.gather, both at once
        │     ├── jobs.update_status(id, "analyzing")
        │     ├── openai.chat.completions.create with JSON response_format
        │     │     → {"summary": "...", "emotion": "..."}
        │     └── roast (#63), concurrently:
        │           ├── openai.chat.completions.create (parody model)
        │           │     → ~25 word TL;DR in the speaker's voice
        │           └── openai.audio.speech.create(response_format="opus")
        │                 → Ogg/Opus bytes
        ├── jobs.update_status(id, "sending")
        ├── if reply fits in 4096 chars:
        │     └── telegram.edit_message_text(ack_message_id, single reply)
        ├── else (long transcript):
        │     ├── edit ack with summary/vibe + "split into N messages" notice
        │     └── send N transcript chunks as separate messages
        │           (last chunk ends with "End of transcript")
        ├── if a roast survived:
        │     ├── telegram.sendVoice(caption="Okay, well — the short version…")
        │     └── gcs.upload at …/parody_{uniq}.ogg
        ├── jobs.mark_succeeded(...)
        └── persistence.record_event("voice_transcription_succeeded", ...)
```

Failure at any step:

* mark row failed with `error_class`/`error_message` plus any partial
  results available at the point of failure (transcript, summary,
  emotion, GCS path)
* edit the ack with a user-visible error reply (matrix below) — same
  edit-or-fallback path as success
* swallow further Telegram send failures so we don't loop on a broken chat

## Caps

| Limit               | Value          | Rationale                                                    |
| ------------------- | -------------- | ------------------------------------------------------------ |
| Duration            | 10 min         | Operator preference. Telegram voice memos can technically go to 60 min. |
| File size           | 25 MB          | `gpt-transcribe` request ceiling. 10 min of Opus voice is ~3-5 MB so this is defensive. |
| Transcribe timeout  | 60 s           | Whole-request OpenAI timeout.                                |
| Analysis timeout    | 25 s           | Short chat call.                                             |
| Parody timeout      | 30 s           | Chat call on a stronger model than the analysis one.         |
| Speech timeout      | 45 s           | TTS renders audio, so it is slower than a text completion.   |
| Parody words        | 40             | Enforced in code by `_trim_to_words`, preferring a sentence boundary. The prompt asks for 25 — see below. |
| Parody length       | 400 chars      | Second backstop; 40 very long words is still billed and still spoken aloud. |

Both caps short-circuit before the ack/reaction/background task — the
user gets one clear rejection reply and that's it.

## Reply format (#56)

Two templates, picked by duration:

**Long memo (`voice.duration > 120s`)** — runs the OpenAI chat summary
+ emotion read, renders both in one blockquote alongside the
transcript in another:

```
Summary & Vibe:
<blockquote><1-3 sentence factual summary>
<1 sentence emotion read></blockquote>

Transcript:
<blockquote><full transcript></blockquote>
```

**Short memo (`voice.duration <= 120s`)** — skips the analyze step
entirely (saves one OpenAI call and a couple seconds; for a short
memo the transcript itself is shorter than any commentary would be):

```
Voice-to-text:
<blockquote><full transcript></blockquote>
```

Sent with `parse_mode="HTML"`. Free-form OpenAI output is
`html.escape`-d before interpolation so a stray `<` in a transcript
doesn't break the HTML parse.

**Single-message replies** when the reply fits within Telegram's
4096-character limit. The `Transcribing your voice memo…` ack is
*edited in place* with the final reply when the background task
finishes — the user never sees a separate ack message hanging around.
If the edit fails (rare), we fall back to sending the reply as a new
message so the user still gets the result.

**Multi-message replies** when the transcript exceeds 4096 characters:

1. The ack is edited with the summary/vibe (for long memos) or a
   plain notice, plus "The transcript is too long and will be split
   into N messages."
2. N follow-up messages: `Transcript pt. 1 of N:` through
   `Transcript pt. N of N:`, each with the chunk in a blockquote.
3. The last chunk appends "End of transcript".

Chunk boundaries prefer newlines, then spaces, and never split
inside an HTML entity. If a subsequent chunk send fails, earlier
chunks are still delivered (partial delivery over total failure).
The full transcript is always persisted to Postgres regardless of
send outcome.

## The roast (#63)

Only for memos over `SHORT_DURATION_THRESHOLD_SECONDS` — the same
threshold that gates the summary. Both read the transcript and neither
needs the other's output, so they run under one `asyncio.gather`;
serializing them would add a whole chat round-trip to the wait.

Two calls: `openai_parody_model` writes the roast, `openai_tts_model`
speaks it in `openai_tts_voice`. Defaults are `gpt-5.2`,
`gpt-4o-mini-tts` and `marin`. The parody model is deliberately *not*
`Settings.openai_model` — that one is shared with the chat fallback and
OCR, and comic timing wants a stronger model than those need.

The prompt asks for a first-person TL;DR performed as the speaker,
keeping the actual point recognisable, mocking how and what they said
but nothing about their appearance or identity. Output is plain text
because it is spoken aloud verbatim.

**The word cap is enforced twice, because the model ignores the number
it is given.** Measured 2026-08-12 against `gpt-5.2`, 5 samples per
setting on the same transcript:

| Prompt asks for | Words returned | Over 40 |
| --- | --- | --- |
| 40 | 44, 44, 48, 49, 49 | 5/5 |
| 30 | 37, 37, 39, 40, 45 | 1/5 |
| 25 | 26, 27, 28, 29, 31 | 0/5 |

So `PROMPT_WORD_TARGET = 25` goes in the prompt and `MAX_PARODY_WORDS =
40` is enforced by `_trim_to_words`, which prefers to cut on a sentence
boundary so a clipped roast still sounds finished. Typical output is
~27 words / ~160 chars, about six seconds of audio. The first version
shipped at 100 words and produced a ~20 second clip that was a chore to
sit through.

The TTS call carries its own `instructions`, written along the axes the
model actually steers on — voice, delivery, pacing, intonation, emotion
— rather than as a single adjective. A flat read kills the joke no
matter how good the words are.

`response_format="opus"` gives Ogg/Opus, which is exactly what
`sendVoice` needs to render a real voice bubble instead of a file
attachment.

**The roast is garnish and fails like garnish.** `ParodyError`,
`SpeechError`, a rejected `sendVoice`, a failed archive upload — each is
logged and dropped, and the transcript reply goes out exactly as it
would have. Nothing about the roast can produce a user-visible error,
and a job whose roast died is still `succeeded`. If the *analysis* fails
the roast is suppressed too: the user gets an error reply, and a
punchline on top of that would be tone-deaf.

Ordering is fixed: transcript first, roast second, replying to the
original memo rather than to the transcript message.

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
| `parody_text`                   | the roast that was spoken; `NULL` when it failed or was skipped |
| `parody_gcs_object_path`        | archived roast audio; `NULL` when the send or upload failed    |
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
* Postgres wiring from #31 is reused. The two `parody_*` columns are
  added by `ensure_table()` via `ADD COLUMN IF NOT EXISTS`, so the live
  DB picks them up on the first memo after deploy — no migration step.
* `OPENAI_PARODY_MODEL`, `OPENAI_TTS_MODEL` and `OPENAI_TTS_VOICE` are
  plain env vars with working defaults; set them only to override.

Note that group members must allow voice messages from the bot for the
roast to be deliverable — Telegram rejects `sendVoice` with
`VOICE_MESSAGES_FORBIDDEN` for users on the restricted setting. That is
handled like any other roast failure: logged, dropped, transcript
unaffected.

## Tests

* `tests/unit/features/test_voice_transcription_handler.py` — match
  rules across private/group/text, happy path in DM (ack + background
  ordering), happy path in group with correct chat_id/reply targeting,
  the four failure branches each route to the right user-visible
  message, transcriber-missing edge case, ack-failure swallowed. For the
  roast: ordering (transcript before roast), caption + reply target,
  archive + persistence, short memos skipping it, parody/speech/send
  failures each leaving the transcript untouched, analysis failure
  suppressing it, and group delivery.
* `tests/unit/features/test_voice_transcription_transcriber.py` —
  OpenAI wrapper: text-stripping, empty-text rejection, SDK errors
  wrapped, JSON parsing, missing-field rejection, non-JSON rejection,
  plus the roast call (own model, no JSON envelope, truncation, empty
  and SDK-error paths) and the TTS call (model/voice/format/instructions,
  empty-audio and SDK-error paths).
* `tests/unit/telegram/test_client.py` — `sendVoice` multipart body,
  `ok=false` and HTTP-error paths.
