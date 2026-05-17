# /make-sticker — image → Telegram sticker PNG (#44)

Two-turn command that turns an image into a Telegram-sticker-shaped PNG
(max 512 px on the longer edge, alpha-capable, aspect ratio preserved).

## Flow

```
/make-sticker            → set pending_user_actions(command="make-sticker",
                                                    expected_input="image"),
                            prompt
  ↓ user sends a photo within 10 min
                         → clear pending row
                         → ack "Working on your sticker…"
                         → schedule asyncio.create_task(_run_background)
                              ├── telegram.get_file_path + download_file → bytes
                              ├── gcs.upload at sticker_requests/{chat}/{msg}/input_{uniq}
                              ├── transform(bytes) → StickerImage (Pillow, off-thread)
                              ├── gcs.upload at sticker_outputs/{chat}/{msg}/sticker_{uniq}.png
                              ├── telegram.send_document(png_bytes, "image/png", reply_to=msg)
                              └── persistence.record_event("make_sticker_succeeded", ...)
```

Failure at any step → mark the row failed (when persistence is wired),
send one user-visible reply, swallow further send failures.

## Transform behavior

Built on Pillow. The transform is **resize + convert to RGBA + PNG**:

* Source decoded with `Image.open` (any format Pillow understands —
  JPEG, PNG, WebP, BMP, GIF first-frame, HEIC if `pillow-heif` is
  available; not currently installed).
* Converted to RGBA. Sources with existing alpha keep it; opaque sources
  get an opaque alpha channel (no automatic background removal — see
  out-of-scope below).
* Resized so the longer edge is at most 512 px via Pillow's `thumbnail`
  with Lanczos resampling. **Does not upscale** — a 64×32 input stays
  64×32.
* Saved as PNG with `optimize=True`.

## Delivery

Sent back via Telegram's `sendDocument` (not `sendPhoto`) so Telegram
doesn't re-compress and strip the alpha channel. Recipients see the
PNG as a file attachment, which is the right shape if they want to add
it to a sticker pack.

## GCS layout

```
sticker_requests/{chat_id}/{trigger_message_id}/input_{file_unique_id}
sticker_outputs/{chat_id}/{trigger_message_id}/sticker_{file_unique_id}.png
```

Both are kept indefinitely for now; retention review goes on the
backlog if storage volume ever becomes a concern.

## Dispatch precedence

`MakeStickerHandler` is registered **before** `FileStorageHandler`.
That way:

* User invokes `/make-sticker` → handler claims the trigger and sets
  the pending row.
* User uploads a photo: with the pending row present, the sticker
  handler's `matches()` claims the photo. Without a pending row, the
  match fails and `FileStorageHandler` picks the photo up as a generic
  upload.

## Error matrix

| Failure                              | User-visible reply                                                                              |
| ------------------------------------ | ----------------------------------------------------------------------------------------------- |
| `TelegramFileError` on download      | `Couldn't pull that image from Telegram. Try sending it again in a moment.`                     |
| `StickerTransformError`              | `Couldn't turn that into a sticker. The image might be corrupted or in an unusual format. Try a different one.` |
| GCS upload / `TelegramSendError`     | `Something went wrong handling that image. Logged.`                                             |

## Scope / restrictions

* **Private chats only.** Per-user pending state in a group is awkward
  (whose photo wins?) — group `/make-sticker` would need richer
  routing and is out of scope.
* **No automatic background removal.** v1 preserves existing alpha but
  doesn't try to cut out subjects. Backlog candidate: rembg or OpenAI
  image edit.
* **No multi-image messages.** Telegram delivers a media group as
  separate updates per image — the handler takes the first one that
  arrives while the pending state is live, and clears state.

## Tests

* `tests/unit/features/test_make_sticker_handler.py` — 14 tests:
  match rules, command sets pending + prompts, full happy path (input
  + output uploaded + sendDocument + persistence event), download
  failure, transform failure, sendDocument failure, plus four Pillow
  transform unit tests (resize, no-upscale, alpha preservation,
  non-image input rejection).
