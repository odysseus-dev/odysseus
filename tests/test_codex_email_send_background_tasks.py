"""The Codex email-send route must forward FastAPI's injected BackgroundTasks.

`send_email` queues delivery with `background_tasks.add_task(_deliver)`. The
codex wrapper passed a fresh `BackgroundTasks()` that FastAPI never drains, so
`POST /api/codex/emails/send` returned success/queued but no SMTP delivery ever
happened. The route now accepts an injected BackgroundTasks and forwards it.
"""
import asyncio
from types import SimpleNamespace

from fastapi import BackgroundTasks

import routes.codex_routes as cx


def _codex_handler(router, method, frag):
    for r in router.routes:
        if frag in getattr(r, "path", "") and method in getattr(r, "methods", set()):
            return r.endpoint
    raise AssertionError(f"no {method} route matching {frag}")


def test_codex_email_send_forwards_injected_background_tasks(monkeypatch):
    captured = {}

    async def fake_send(req, background_tasks, owner):
        captured["bt"] = background_tasks
        captured["owner"] = owner
        background_tasks.add_task(lambda: None)  # what send_email does for _deliver
        return {"queued": True}

    fake_email_router = SimpleNamespace(routes=[
        SimpleNamespace(path="/api/email/send", methods={"POST"}, endpoint=fake_send),
    ])

    monkeypatch.setattr(cx, "require_user", lambda request: "alice")

    router = cx.setup_codex_routes(email_router=fake_email_router)
    handler = _codex_handler(router, "POST", "/emails/send")

    request = SimpleNamespace(state=SimpleNamespace(api_token=False))
    bt = BackgroundTasks()
    body = {"to": "x@example.com", "subject": "hi", "body": "hello"}

    resp = asyncio.run(handler(request=request, background_tasks=bt, body=body))

    assert resp == {"queued": True}
    # send_email must receive the SAME BackgroundTasks FastAPI injected (which it
    # drains after the response) — not a throwaway whose tasks are dropped.
    assert captured["bt"] is bt
    assert captured["owner"] == "alice"
    assert len(bt.tasks) == 1
