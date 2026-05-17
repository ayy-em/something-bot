# /dutch — Dutch → English translation (#47)

Translates Dutch text into English via OpenAI.

## Flow

```
/dutch [text]           → if text present, translate immediately
/dutch                  → set pending_user_actions row (TTL 10 min), prompt
  ↓ user replies with text within 10 min
                        → translate, clear pending row, reply
```

Trigger and follow-up both use the same `DutchTranslationHandler`:

* `matches()` returns True for `CommandContent(command="dutch")` OR a
  `TextContent` from a sender with a `pending_action.command == "dutch"`.
* `handle()` branches on whether it's the trigger or the follow-up.
  Inline-arg form skips the pending state entirely.

Works in DMs, groups, and supergroups.

## Reply format

```
<i><English translation></i>
```

Sent with `parse_mode="HTML"`. Free-form OpenAI output is
`html.escape`-d before interpolation. No preamble, no "Translation:"
label — just the translation in italics so it's visually distinct
from the user's Dutch input.

## Pending action lifecycle

* `/dutch` (no args) → upsert one row in `public.pending_user_actions`
  with `(bot_id, chat_id, user_id)` as the PK, TTL 10 minutes.
* Next text from the same user → handler clears the row before
  translating, so an OpenAI failure doesn't trap the user in a loop.
* `/dutch` again before timeout → upsert replaces the prior row.
* `/dutch` then a different `/command` → the other handler's `matches()`
  fires first (commands take precedence over pending-text matching).

## Error matrix

| Failure                         | User-visible reply                                                                                       |
| ------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `TranslationError` (API failed) | `Couldn't translate that. The translation service might be having a moment — try again shortly.`        |
| `OPENAI_API_KEY` missing        | `Translation isn't configured right now. Logged for review.`                                             |
| Empty follow-up text            | Re-prompts with `Send me the Dutch text you want translated to English (within 10 minutes).`             |
| Telegram send failure           | Logged; no recursive retry.                                                                              |

## Tests

* `tests/unit/features/test_dutch_translation_handler.py` — 13 tests:
  match rules (command vs follow-up vs unrelated text), inline-args
  fast path, prompt-then-followup full path, pending state set/clear
  call shapes, translator error → user error message, empty input →
  re-prompt, Telegram failure swallowed.
* `tests/unit/services/test_pending_actions.py` — 9 tests covering the
  shared store (upsert, get with `expires_at > now()` filter,
  jsonb-as-string handling, clear, Postgres-failure resilience, TTL
  parametrised).
