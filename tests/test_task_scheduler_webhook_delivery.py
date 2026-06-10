import asyncio
import sys
import types

import pytest

from src.task_scheduler import TaskScheduler


def _task(name="Daily Brief"):
    return types.SimpleNamespace(id="task-1", name=name)


def test_webhook_output_target_detection_and_resolution(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_TASK_WEBHOOK_URL", " http://127.0.0.1:9090/hooks/default ")
    monkeypatch.setenv("ODYSSEUS_TASK_WEBHOOK_ALERTS_TEAM", "http://127.0.0.1:9090/hooks/alerts")

    assert TaskScheduler._is_webhook_output_target("webhook")
    assert TaskScheduler._is_webhook_output_target("Webhook:alerts-team")
    assert not TaskScheduler._is_webhook_output_target("session")

    assert TaskScheduler._resolve_task_webhook("webhook") == "http://127.0.0.1:9090/hooks/default"
    assert TaskScheduler._resolve_task_webhook("Webhook:alerts team") == "http://127.0.0.1:9090/hooks/alerts"


def test_task_webhook_validation_rejects_unsafe_urls():
    TaskScheduler._validate_task_webhook_url("http://127.0.0.1:9090/hooks/default")

    with pytest.raises(RuntimeError):
        TaskScheduler._validate_task_webhook_url("ftp://example.com/hook")
    with pytest.raises(RuntimeError):
        TaskScheduler._validate_task_webhook_url("http://169.254.169.254/latest/meta-data")


def test_deliver_via_webhook_posts_generic_payload_without_network(monkeypatch):
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
    monkeypatch.setenv("ODYSSEUS_TASK_WEBHOOK_URL", "http://127.0.0.1:9090/hooks/default")

    scheduler = TaskScheduler.__new__(TaskScheduler)

    asyncio.run(scheduler._deliver_via_webhook("webhook", _task(), "hello"))

    assert posts == [
        (
            "http://127.0.0.1:9090/hooks/default",
            {
                "event": "task.completed",
                "source": "odysseus",
                "task_id": "task-1",
                "task_name": "Daily Brief",
                "result": "hello",
            },
        )
    ]


def test_deliver_via_webhook_surfaces_http_failures(monkeypatch):
    class FakeResponse:
        status_code = 500

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
    monkeypatch.setenv("ODYSSEUS_TASK_WEBHOOK_URL", "http://127.0.0.1:9090/hooks/default")

    scheduler = TaskScheduler.__new__(TaskScheduler)

    with pytest.raises(RuntimeError, match="HTTP 500"):
        asyncio.run(scheduler._deliver_via_webhook("webhook", _task(), "hello"))
