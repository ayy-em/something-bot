"""Tests for :mod:`something_really_bot.logging` (#28).

Asserts the JSON shape Cloud Logging expects:
- ``severity`` at the top level (so Cloud Run promotes it).
- ``message`` and ``logger`` always present.
- Caller-provided ``extra=`` keys merged at the top level (so they
  become ``jsonPayload.<key>`` and structured filters work).
- Reserved LogRecord attributes never bleed into the payload.
- ``exc_info`` produces an ``exception`` field.
"""

import io
import json
import logging

import pytest

from something_really_bot.logging import (
    StructuredJsonFormatter,
    configure_logging,
    get_logger,
    redact_secrets,
)

# Shaped like a real Telegram bot token, but not one: the id is the
# documented placeholder range and the secret half is typed here.
_FAKE_TOKEN = "bot1234567890:AAF-fake-token-value-not-a-real-secret-x"


@pytest.fixture(autouse=True)
def _reset_root_logging():
    """Tear down the structured handler between tests so we don't leak state."""
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)


def _format(record_kwargs: dict) -> dict:
    formatter = StructuredJsonFormatter()
    record = logging.LogRecord(
        name=record_kwargs["name"],
        level=record_kwargs["level"],
        pathname=__file__,
        lineno=10,
        msg=record_kwargs["msg"],
        args=None,
        exc_info=record_kwargs.get("exc_info"),
    )
    for key, value in record_kwargs.get("extra", {}).items():
        setattr(record, key, value)
    return json.loads(formatter.format(record))


def test_format_includes_severity_message_and_logger() -> None:
    payload = _format({"name": "foo.bar", "level": logging.INFO, "msg": "hello"})

    assert payload["severity"] == "INFO"
    assert payload["message"] == "hello"
    assert payload["logger"] == "foo.bar"


def test_format_promotes_extras_to_top_level() -> None:
    payload = _format(
        {
            "name": "foo",
            "level": logging.WARNING,
            "msg": "noisy",
            "extra": {"update_id": 42, "bot_id": "default", "route": "/webhook"},
        }
    )

    assert payload["severity"] == "WARNING"
    assert payload["update_id"] == 42
    assert payload["bot_id"] == "default"
    assert payload["route"] == "/webhook"


def test_format_excludes_reserved_logrecord_attributes() -> None:
    payload = _format(
        {
            "name": "foo",
            "level": logging.INFO,
            "msg": "x",
            "extra": {"update_id": 1},
        }
    )

    for reserved in ("pathname", "lineno", "process", "msecs"):
        assert reserved not in payload, f"reserved key {reserved!r} leaked into payload"


def test_format_includes_exception_when_exc_info_set() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        exc_info = sys.exc_info()

    payload = _format(
        {
            "name": "foo",
            "level": logging.ERROR,
            "msg": "oops",
            "exc_info": exc_info,
        }
    )

    assert payload["severity"] == "ERROR"
    assert "boom" in payload["exception"]
    assert "ValueError" in payload["exception"]


def test_configure_logging_installs_json_handler_on_root() -> None:
    configure_logging()

    handlers = logging.getLogger().handlers
    assert len(handlers) == 1
    assert isinstance(handlers[0].formatter, StructuredJsonFormatter)


def test_configure_logging_is_idempotent() -> None:
    """Re-configuring replaces handlers rather than stacking them."""
    configure_logging()
    configure_logging()
    configure_logging()

    handlers = logging.getLogger().handlers
    assert len(handlers) == 1


def test_get_logger_writes_json_line_to_stderr() -> None:
    configure_logging()
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(StructuredJsonFormatter())
    log = get_logger("test.json_line")
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False

    log.info("ping", extra={"update_id": 7})

    line = buf.getvalue().strip()
    payload = json.loads(line)
    assert payload["severity"] == "INFO"
    assert payload["message"] == "ping"
    assert payload["update_id"] == 7


def test_redacts_telegram_token_in_message() -> None:
    """httpx logs every request line at INFO; the token sat in the URL."""
    payload = _format(
        {
            "name": "httpx",
            "level": logging.INFO,
            "msg": (
                f"HTTP Request: POST https://api.telegram.org/{_FAKE_TOKEN}/getFile "
                '"HTTP/1.1 200 OK"'
            ),
        }
    )

    assert "AAF-fake-token-value" not in payload["message"]
    assert "bot1234567890:<redacted>" in payload["message"]
    # The useful half of the line survives.
    assert "/getFile" in payload["message"]
    assert "200 OK" in payload["message"]


def test_redacts_token_in_extras() -> None:
    payload = _format(
        {
            "name": "foo",
            "level": logging.WARNING,
            "msg": "telegram_transport_error",
            "extra": {"url": f"https://api.telegram.org/{_FAKE_TOKEN}/sendMessage"},
        }
    )

    assert "AAF-fake-token-value" not in json.dumps(payload)
    assert "<redacted>" in payload["url"]


def test_redacts_token_in_exception_text() -> None:
    try:
        raise RuntimeError(f"failed calling https://api.telegram.org/{_FAKE_TOKEN}/getFile")
    except RuntimeError:
        import sys

        exc_info = sys.exc_info()

    payload = _format({"name": "foo", "level": logging.ERROR, "msg": "oops", "exc_info": exc_info})

    assert "AAF-fake-token-value" not in payload["exception"]
    assert "<redacted>" in payload["exception"]


def test_redacts_openai_style_key() -> None:
    payload = _format(
        {
            "name": "foo",
            "level": logging.INFO,
            "msg": "using key sk-notarealkey0000000000000000000000",
        }
    )

    assert "notarealkey" not in payload["message"]
    assert "sk-<redacted>" in payload["message"]


def test_redact_secrets_leaves_ordinary_text_alone() -> None:
    text = "no secrets here: bot42 sk-short /getFile 200 OK"

    assert redact_secrets(text) == text
