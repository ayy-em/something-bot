# /make_sticker — image → cut-out, outlined sticker PNG (#44)

Takes an image, cuts the subject out of its background and returns a
white-outlined PNG sized for Telegram's sticker uploader (exactly 512 px
on the longer edge, aspect ratio preserved).

## Ways in

Private chats, groups and supergroups alike. Input is a photo or an image
file (`image/*` document, except SVG).

| Invocation | What happens |
| --- | --- |
| `/make_sticker` | Sets `pending_user_actions(command="/make_sticker", expected_input="image")` for that (chat, user) and prompts. The same user's next photo / image file in that chat within 10 min is the input. |
| Photo or image file captioned `/make_sticker` | Processed straight away. `/make-sticker` and `/make_sticker@Bot` work too. |
| `/make_sticker` as a reply to a photo or image file | The replied-to image is processed straight away. |
| `/make_sticker` as a reply to anything else | Same as the bare command: prompt. |

Pending state is per (chat, user), so in a group only the person who
sent `/make_sticker` arms the next upload, and only in that group.

**Group privacy mode:** with BotFather privacy mode on, the bot only
sees commands, replies to its own messages and mentions in groups. The
caption and reply forms still work (they are commands); the two-turn
form needs the follow-up photo to be a reply to the bot's prompt, or
privacy mode off (or the bot as a group admin). Voice transcription in
groups has the same requirement, so wherever that works, all three
forms work.

**Telegram inline mode (`@bot …` in any chat) is not supported and
can't be:** inline queries carry text only, never an uploaded image.

## Flow

```
trigger (command / caption / reply / pending follow-up)
  → clear our pending row (only if one is live)
  → ack "Working on your sticker…"
  → schedule asyncio.create_task(_run_background)
       ├── telegram.get_file_path + download_file → bytes
       ├── gcs.upload at sticker_requests/{chat}/{msg}/input_{uniq}
       ├── transform(bytes) → StickerImage (off-thread)
       ├── gcs.upload at sticker_outputs/{chat}/{msg}/sticker_{uniq}.png
       ├── telegram.send_document(png_bytes, "image/png", reply_to=msg)
       └── persistence.record_event("make_sticker_succeeded", ...)
```

`{msg}` is the triggering message (the command, or the captioned /
follow-up photo). Failure at any step → one user-visible reply, a
`make_sticker_failed` event, further send failures swallowed.

## Transform

`transform.py`, in this order:

1. **Decode.** Pillow, any format it understands (JPEG, PNG, WebP, BMP,
   GIF first frame). JPEGs far larger than needed are decoded at reduced
   scale (`Image.draft`). EXIF orientation is applied, so phone photos
   sent as files don't come out sideways.
2. **Fit.** Scale up *or* down with Lanczos so the longer edge is
   504 px (512 − 2 × 4 px outline). A 64×32 input becomes 504×252.
3. **Cut out.** rembg with the `u2netp` model. An all-transparent result
   raises `NoSubjectError`.
4. **Outline.** Threshold the alpha at 50 %, dilate it with a 4 px
   round brush, blur 0.75 px for anti-aliasing, fill white, and composite
   the cut-out on top. The canvas grows by 4 px per side, so the result
   is exactly 512 px on the longer edge. Subjects touching the frame get
   the border along the frame edge too.
5. **Encode.** PNG, `optimize=True`.

### Why `u2netp`, not rembg's default `u2net`

Measured on 1 vCPU, CPU memory arena off (on top of the ~100 MB app):

| Model | Size | Resident after load | Peak |
| --- | --- | --- | --- |
| `u2net` | 176 MB | 655 MB | 945 MB |
| `isnet-general-use` | 179 MB | 627 MB | 1.17 GB |
| `u2netp` | 4.7 MB | 293 MB | 548 MB |

The service runs on 2 GiB (`infra/terraform/variables.tf`), shared with
yt-dlp/ffmpeg at concurrency 8, so `u2netp` keeps real headroom where
`u2net` would eat half the instance. Cut-out quality on portraits and
objects is close to `u2net`; it is rougher on busy backgrounds. Switching
models means changing `REMBG_MODEL` and the session class in the
Dockerfile (a unit test keeps them in sync). Whether to switch is a
review item for 2026-11-02 (see `BACKLOG.md`).

Every sticker logs one `make_sticker_finished` line with `outcome`,
`total_ms`, `transform_ms` and `process_peak_rss_mb` (process lifetime
high-water mark), which is what that review reads.

Memory controls in code:

* The onnxruntime CPU memory arena is **off**. With it on, RSS grew by
  ~250 MB per inference (measured with `u2net`) and never came back.
* A process-wide lock allows one inference at a time.
* rembg is imported lazily on the first sticker (~240 MB of
  onnxruntime / scipy / scikit-image / OpenCV), so instances that never
  make a sticker never pay for it. The first sticker after a cold start
  takes a couple of seconds longer.

The model is baked into the image at `/app/.u2net` (`U2NET_HOME`) so a
cold start never downloads it from GitHub.

## Delivery

Sent via `sendDocument` (not `sendPhoto`) so Telegram doesn't
re-compress the PNG and strip the alpha channel. To turn it into a real
sticker, forward the file to `@Stickers`.

## GCS layout

```
sticker_requests/{chat_id}/{trigger_message_id}/input_{file_unique_id}
sticker_outputs/{chat_id}/{trigger_message_id}/sticker_{file_unique_id}.png
```

Both are kept indefinitely for now; retention review goes on the
backlog if storage volume ever becomes a concern.

## Dispatch precedence

`MakeStickerHandler` is registered **before** `FileStorageHandler`, so
a private photo that belongs to a sticker request (caption, pending row)
reaches this handler. Anything else falls through to
`FileStorageHandler` as a generic upload.

## Error matrix

| Failure | User-visible reply |
| --- | --- |
| `TelegramFileError` on download | `Couldn't pull that image from Telegram. Try sending it again in a moment.` |
| `NoSubjectError` | `Couldn't find anything to cut out in that image. Try one with a clear subject.` |
| `StickerTransformError` | `Couldn't turn that into a sticker. The image might be corrupted or in an unusual format. Try a different one.` |
| GCS upload / `TelegramSendError` | `Something went wrong handling that image. Logged.` |

## Scope / restrictions

* **No media groups.** Telegram delivers an album as one update per
  image; the pending row is cleared by the first one, and a caption
  only rides on one of them.
* **No auto-crop to the subject.** rembg leaves the transparent area
  where the background was, so a small subject in a wide shot stays
  small inside the 512 px frame.

## Tests

* `tests/unit/features/test_make_sticker_handler.py`: transform
  (up/downscale, fit size handed to rembg, outline geometry and
  crispness, EXIF, empty cut-out, bad input, Dockerfile/model sync) with
  a stub background remover; matching for private / group / supergroup,
  caption, reply, pending and image documents; prompt, happy paths and
  every error branch.
* `tests/unit/test_parser.py`: `reply_to_message` parsing.

The real rembg model is not exercised in unit tests (no model download
in CI).
