"""Tests for :mod:`something_really_bot.services.openai_context`."""

from dataclasses import dataclass

from something_really_bot.services.openai_context import (
    MAX_CONTEXT_BYTES,
    OpenAIContextLoader,
)


@dataclass
class _FakeBlob:
    name: str
    content: bytes = b""
    raises: BaseException | None = None
    downloads: int = 0

    def download_as_bytes(self) -> bytes:
        self.downloads += 1
        if self.raises is not None:
            raise self.raises
        return self.content


class _FakeStorageClient:
    """Returns the same fixed list of blobs to every ``list_blobs`` call.

    Tracks call count so we can assert caching behaviour.
    """

    def __init__(self, blobs: list[_FakeBlob]) -> None:
        self._blobs = blobs
        self.calls: int = 0

    def list_blobs(self, _bucket: str, *, prefix: str = "") -> list[_FakeBlob]:
        self.calls += 1
        return [b for b in self._blobs if b.name.startswith(prefix)]


class _ManualClock:
    """A monotonic-clock substitute we can advance from tests."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_get_context_messages_returns_blobs_in_name_order() -> None:
    client = _FakeStorageClient(
        blobs=[
            _FakeBlob(name="context/b.md", content=b"second"),
            _FakeBlob(name="context/a.md", content=b"first"),
            _FakeBlob(name="context/notes.txt", content=b"ignored"),
        ]
    )
    loader = OpenAIContextLoader(
        bucket_name="x", client=client, clock=_ManualClock()
    )

    result = await loader.get_context_messages()

    assert result == ("first", "second")


async def test_get_context_messages_caches_within_ttl_and_refreshes_after() -> None:
    blobs = [_FakeBlob(name="context/a.md", content=b"hello")]
    client = _FakeStorageClient(blobs=blobs)
    clock = _ManualClock()
    loader = OpenAIContextLoader(
        bucket_name="x", client=client, ttl_seconds=10.0, clock=clock
    )

    await loader.get_context_messages()
    await loader.get_context_messages()
    assert client.calls == 1  # cached

    clock.now += 11
    await loader.get_context_messages()
    assert client.calls == 2  # refreshed


async def test_get_context_messages_returns_empty_when_list_raises() -> None:
    class _BoomClient:
        def list_blobs(self, *_a, **_k):
            raise RuntimeError("gcs down")

    loader = OpenAIContextLoader(
        bucket_name="x", client=_BoomClient(), clock=_ManualClock()
    )

    assert await loader.get_context_messages() == ()


async def test_get_context_messages_skips_blob_that_fails_to_download() -> None:
    client = _FakeStorageClient(
        blobs=[
            _FakeBlob(name="context/a.md", content=b"good"),
            _FakeBlob(name="context/b.md", raises=RuntimeError("perm denied")),
            _FakeBlob(name="context/c.md", content=b"alsogood"),
        ]
    )
    loader = OpenAIContextLoader(
        bucket_name="x", client=client, clock=_ManualClock()
    )

    result = await loader.get_context_messages()

    assert result == ("good", "alsogood")


async def test_get_context_messages_truncates_above_byte_budget() -> None:
    big = b"x" * (MAX_CONTEXT_BYTES + 100)
    client = _FakeStorageClient(blobs=[_FakeBlob(name="context/a.md", content=big)])
    loader = OpenAIContextLoader(
        bucket_name="x", client=client, clock=_ManualClock()
    )

    result = await loader.get_context_messages()

    assert len(result) == 1
    assert len(result[0]) <= MAX_CONTEXT_BYTES


async def test_get_context_messages_ignores_non_markdown_blobs() -> None:
    client = _FakeStorageClient(
        blobs=[
            _FakeBlob(name="context/a.md", content=b"keep"),
            _FakeBlob(name="context/notes.txt", content=b"drop"),
        ]
    )
    loader = OpenAIContextLoader(
        bucket_name="x", client=client, clock=_ManualClock()
    )

    assert await loader.get_context_messages() == ("keep",)
