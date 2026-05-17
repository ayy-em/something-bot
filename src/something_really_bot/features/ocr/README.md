# /ocr — image OCR via OpenAI vision (#45)

Two-turn command, private chat only. User invokes `/ocr`, sends a
photo, bot replies with the extracted text in italics.

## Flow

```
/ocr             → set pending_user_actions(command="ocr",
                                              expected_input="image"),
                    prompt
  ↓ user sends a photo within 10 min
                 → clear pending row
                 → ack "Reading the text…"
                 → schedule asyncio.create_task(_run_background)
                      ├── telegram.get_file_path + download_file → bytes
                      ├── gcs.upload at ocr_requests/{chat}/{msg}/image_{uniq}
                      ├── ocr_client.extract_text(bytes)
                      │     → openai.chat.completions with image_url data: URL
                      ├── send_message(reply, parse_mode="HTML")
                      └── persistence.record_event("ocr_succeeded", ...)
```

## OCR implementation

Uses `chat.completions.create` with the configured chat model
(`OPENAI_MODEL`, defaults to `gpt-4o-mini`). Image is encoded as a
base64 `data:` URL in the `image_url` content part. System prompt
instructs the model to act as an OCR engine and return literal text
only, falling back to the sentinel `NO_TEXT` when nothing readable is
found.

The model is not specialized for OCR — it's the same general-purpose
chat model used elsewhere. Accuracy is fine for screenshots, receipts,
hand-written notes; less reliable for cursive, tiny print, or images
with heavy compression artifacts.

## Reply format

```
<i><extracted text></i>
```

`parse_mode="HTML"`, content `html.escape`-d.

If the model returns the `NO_TEXT` sentinel, the user gets a plain
`I couldn't find any readable text in that image.` instead (no
italics — there's no extracted text to emphasize).

## Error matrix

| Failure                              | User-visible reply                                                                              |
| ------------------------------------ | ----------------------------------------------------------------------------------------------- |
| `TelegramFileError` on download      | `Couldn't pull that image from Telegram. Try sending it again in a moment.`                     |
| `OCRError` (OpenAI failed)           | `Couldn't read text from that image. The OCR service might be having a moment — try again shortly.` |
| `OPENAI_API_KEY` missing             | `OCR isn't configured right now. Logged for review.`                                            |
| GCS upload / `TelegramSendError`     | `Something went wrong handling that image. Logged.`                                             |

## GCS layout

```
ocr_requests/{chat_id}/{trigger_message_id}/image_{file_unique_id}
```

Kept indefinitely for now.

## Dispatch precedence

`OCRHandler` registers alongside `MakeStickerHandler`, both **before**
`FileStorageHandler`. Each only claims a photo when its own pending
action is the live one, so they never collide. Photos without any
live pending action still fall through to `FileStorageHandler`.

## Scope / restrictions

* **Private chats only** — same constraint as `/make-sticker`.
* **No PDF or screenshot batches** — backlog if/when needed.
* **No structured output** (markdown tables, JSON) — plain text only.

## Tests

* `tests/unit/features/test_ocr_handler.py` — 11 tests: match rules,
  command sets pending + prompts, happy path (ack + GCS upload + OCR
  call + reply), `NO_TEXT` sentinel handled, download failure, OCR
  failure, missing-key path, ack-failure swallowed.
