"""Tests for :mod:`something_really_bot.services.openai_client`."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from something_really_bot.services.openai_client import (
    SYSTEM_PROMPT,
    OpenAIClient,
    OpenAIRequestError,
)


def _make_client(*, completions_mock: AsyncMock) -> OpenAIClient:
    """Build an OpenAIClient with a stub AsyncOpenAI whose
    chat.completions.create is the provided mock."""
    fake_chat = SimpleNamespace(completions=SimpleNamespace(create=completions_mock))
    fake_sdk = SimpleNamespace(chat=fake_chat)
    return OpenAIClient(
        api_key=SecretStr("test-key"),
        model="gpt-4o-mini",
        client=fake_sdk,  # type: ignore[arg-type]
        timeout_seconds=5.0,
    )


def _fake_response(text: str | None) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


async def test_complete_returns_trimmed_response_text() -> None:
    create = AsyncMock(return_value=_fake_response("  Paris.  "))
    client = _make_client(completions_mock=create)

    result = await client.complete("Capital of France?")

    assert result == "Paris."
    create.assert_awaited_once()
    call_kwargs = create.await_args.kwargs
    assert call_kwargs["model"] == "gpt-4o-mini"
    assert call_kwargs["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert call_kwargs["messages"][1] == {"role": "user", "content": "Capital of France?"}


async def test_complete_translates_sdk_exception() -> None:
    create = AsyncMock(side_effect=RuntimeError("rate limited"))
    client = _make_client(completions_mock=create)

    with pytest.raises(OpenAIRequestError):
        await client.complete("hi")


async def test_complete_raises_when_no_choices() -> None:
    create = AsyncMock(return_value=SimpleNamespace(choices=[]))
    client = _make_client(completions_mock=create)

    with pytest.raises(OpenAIRequestError):
        await client.complete("hi")


async def test_complete_raises_on_empty_content() -> None:
    create = AsyncMock(return_value=_fake_response(""))
    client = _make_client(completions_mock=create)

    with pytest.raises(OpenAIRequestError):
        await client.complete("hi")


class _StaticContextLoader:
    def __init__(self, messages: tuple[str, ...]) -> None:
        self._messages = messages

    async def get_context_messages(self) -> tuple[str, ...]:
        return self._messages


async def test_complete_prepends_context_messages_between_system_and_user() -> None:
    create = AsyncMock(return_value=_fake_response("ok"))
    fake_chat = SimpleNamespace(completions=SimpleNamespace(create=create))
    fake_sdk = SimpleNamespace(chat=fake_chat)
    loader = _StaticContextLoader(("fact: name is Eve", "project: stx-onboarding"))
    client = OpenAIClient(
        api_key=SecretStr("test-key"),
        model="gpt-4o-mini",
        client=fake_sdk,  # type: ignore[arg-type]
        timeout_seconds=5.0,
        context_loader=loader,  # type: ignore[arg-type]
    )

    await client.complete("what's my name?")

    messages = create.await_args.kwargs["messages"]
    assert messages[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert messages[1] == {"role": "system", "content": "fact: name is Eve"}
    assert messages[2] == {"role": "system", "content": "project: stx-onboarding"}
    assert messages[-1] == {"role": "user", "content": "what's my name?"}
