# /summarize — document TL;DR (#46)

Two-turn command, private chat only. User invokes `/summarize`, sends
a PDF / DOCX / TXT / MD file, bot replies with a 3-6 sentence TL;DR
in italics.

## Flow

```
/summarize       → set pending_user_actions(command="summarize",
                                              expected_input="document"),
                    prompt
  ↓ user sends a document within 10 min
                 → clear pending row
                 → ack "Reading your document…"
                 → schedule asyncio.create_task(_run_background)
                      ├── telegram.get_file_path + download_file → bytes
                      ├── gcs.upload at summarizer/{chat}/{msg}/{uniq}_{filename}
                      ├── extract(bytes, filename, mime_type)  # off-thread
                      │     → ExtractedDocument(text, char_count, truncated)
                      ├── (if empty text) short-circuit with "looks empty" reply
                      ├── summarizer.summarize(text)
                      │     → openai.chat.completions with the chat model
                      ├── send_message(reply, parse_mode="HTML")
                      │     + truncation notice if text was cut at 60k chars
                      └── persistence.record_event("summarize_succeeded", ...)
```

## Supported file types

* `.pdf` (PyMuPDF / `fitz`)
* `.docx` (`python-docx`)
* `.txt`, `.md`, `.markdown`, `.log`, `.csv` (UTF-8 decode with
  replacement)
* Anything else → `UnsupportedDocumentError` → user-visible reply
  listing the supported types

Classification uses MIME first (when Telegram provides one), then
filename extension. PDF and DOCX libs are imported lazily.

## Token / size handling

Hard cap at **60,000 characters** of extracted text (~15k tokens —
fits comfortably under any chat model's context window).

If the document is bigger, summarize the **first 60k characters only**
and the reply gets an italicized notice:

```
<i>(Document was long — summarized the first 60k characters only.)</i>
```

No chunking, no multi-pass summarization in v1. Backlog candidates:
chunked summarization for very long PDFs, OCR for scanned PDFs.

## Reply format

```
<i><3-6 sentence TL;DR></i>
[+ <i>(truncation notice)</i> when applicable]
```

`parse_mode="HTML"`, content `html.escape`-d.

## Error matrix

| Failure                              | User-visible reply                                                                              |
| ------------------------------------ | ----------------------------------------------------------------------------------------------- |
| `TelegramFileError` on download      | `Couldn't pull that file from Telegram. Try sending it again in a moment.`                      |
| `UnsupportedDocumentError`           | `I can summarize PDF, DOCX, TXT, and Markdown files. That file type isn't on the list.`         |
| `DocumentExtractionError`            | `Couldn't read the text out of that file. It might be corrupted or use an unusual format.`      |
| Empty extracted text                 | `That document looks empty — there's nothing to summarize.`                                     |
| `SummarizationError` (OpenAI failed) | `Couldn't summarize that document. The summarization service might be having a moment — try again shortly.` |
| `OPENAI_API_KEY` missing             | `Summarization isn't configured right now. Logged for review.`                                  |
| GCS upload / `TelegramSendError`     | `Something went wrong handling that file. Logged.`                                              |

## GCS layout

```
summarizer/{chat_id}/{trigger_message_id}/{file_unique_id}_{filename or 'document'}
```

Kept indefinitely for now.

## Dispatch precedence

`SummarizeHandler` registers alongside `MakeStickerHandler` and
`OCRHandler`, all **before** `FileStorageHandler`. The match guard
(`pending.command == "summarize"`) ensures only one of these three
claims any given upload, and only when the user is actually mid-flow
for that specific command.

## Scope / restrictions

* **Private chats only.**
* **No `.doc` (legacy)** beyond a best-effort attempt — `python-docx`
  only supports `.docx`. If a `.doc` slips through, the user gets the
  unsupported-type reply.
* **No image/scanned PDF OCR.** A scanned PDF without an OCR text
  layer will extract as empty and the user will see the "looks empty"
  reply.
* **No structured output.** The summary is plain prose.

## Tests

* `tests/unit/features/test_summarize_handler.py` — 18 tests:
  * Extractor: plain text, markdown by extension, truncation at the
    60k cap, unsupported-type rejection, real-DOCX round-trip,
    real-PDF round-trip (uses PyMuPDF to build a small PDF in
    memory), corrupted-PDF maps to `DocumentExtractionError`.
  * Handler: match rules, command sets pending + prompts, happy path,
    truncation notice rendered, unsupported reply, empty-document
    reply, summarizer-failure reply, download-failure reply, missing
    summarizer reply, ack-failure swallowed.
