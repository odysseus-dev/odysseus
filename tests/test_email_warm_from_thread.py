"""Background list refreshes run in a plain thread; warming there must not leak claims."""
import asyncio
import threading

from routes.email import email_routes as er


def test_warm_without_an_event_loop_releases_its_claims(monkeypatch):
    router = er.setup_email_routes()
    calls = []
    real_create_task = asyncio.create_task

    def no_loop(coro):
        calls.append(coro)
        raise RuntimeError("no running event loop")

    monkeypatch.setattr(asyncio, "create_task", no_loop)
    rows = [{"uid": "7", "date_epoch": __import__("time").time(), "size": 10}]
    # Called the way the background list refresh calls it: from a plain thread.
    t = threading.Thread(target=lambda: router._schedule_recent_email_warm(rows, "INBOX", "hotmail", "jo"))
    t.start()
    t.join()
    monkeypatch.setattr(asyncio, "create_task", real_create_task)

    assert len(calls) == 1
    # The same message can be claimed again later instead of being stuck.
    called_again = []
    monkeypatch.setattr(asyncio, "create_task", lambda coro: (called_again.append(coro), coro.close()))
    router._schedule_recent_email_warm(rows, "INBOX", "hotmail", "jo")
    assert len(called_again) == 1
