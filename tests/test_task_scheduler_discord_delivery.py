import asyncio
import sys
import types

import pytest

from src.task_scheduler import TaskScheduler


def _task(name="Daily Brief"):
    return types.SimpleNamespace(id="task-1", name=name)


def test_discord_output_target_detection_and_webhook_resolution(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_DISCORD_WEBHOOK_URL", " http://127.0.0.1:9090/discord ")

    assert TaskScheduler._is_discord_output_target("discord")
    assert TaskScheduler._is_discord_output_target("Discord")
    assert not TaskScheduler._is_discord_output_target("webhook")
    assert not TaskScheduler._is_discord_output_target("discord:ops")

    assert TaskScheduler._resolve_discord_webhook() == "http://127.0.0.1:9090/discord"


def test_discord_chunks_split_long_output_on_newlines_when_possible():
    first = "a" * 410
    second = "b" * 60

    chunks = TaskScheduler._discord_chunks(f"{first}\n{second}", limit=450)

    assert chunks == [first, second]


def test_deliver_via_discord_posts_discord_payload_without_network(monkeypatch):
    posts = []

    class FakeResponse:
        status_code = 204

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            posts.append((url, json))
            return FakeResponse()

    fake_httpx = types.SimpleNamespace(AsyncClient=FakeAsyncClient)
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
    monkeypatch.setenv("ODYSSEUS_DISCORD_WEBHOOK_URL", "http://127.0.0.1:9090/discord")

    scheduler = TaskScheduler.__new__(TaskScheduler)

    asyncio.run(scheduler._deliver_via_discord("discord", _task(), "hello"))

    assert posts == [
        (
            "http://127.0.0.1:9090/discord",
            {"content": "**Odysseus: Daily Brief**\nhello"},
        )
    ]


def test_deliver_via_discord_chunks_long_payloads(monkeypatch):
    posts = []

    class FakeResponse:
        status_code = 204

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            posts.append(json)
            return FakeResponse()

    fake_httpx = types.SimpleNamespace(AsyncClient=FakeAsyncClient)
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
    monkeypatch.setenv("ODYSSEUS_DISCORD_WEBHOOK_URL", "http://127.0.0.1:9090/discord")

    scheduler = TaskScheduler.__new__(TaskScheduler)

    asyncio.run(scheduler._deliver_via_discord("discord", _task(), "a" * 2001))

    assert len(posts) == 2
    assert posts[0]["content"].startswith("**Odysseus: Daily Brief (1/2)**")
    assert posts[1]["content"].startswith("**Odysseus: Daily Brief (2/2)**")


def test_deliver_via_discord_surfaces_http_failures(monkeypatch):
    class FakeResponse:
        status_code = 400

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            return FakeResponse()

    fake_httpx = types.SimpleNamespace(AsyncClient=FakeAsyncClient)
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
    monkeypatch.setenv("ODYSSEUS_DISCORD_WEBHOOK_URL", "http://127.0.0.1:9090/discord")

    scheduler = TaskScheduler.__new__(TaskScheduler)

    with pytest.raises(RuntimeError, match="HTTP 400"):
        asyncio.run(scheduler._deliver_via_discord("discord", _task(), "hello"))
