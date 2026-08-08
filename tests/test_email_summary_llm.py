import asyncio
import json
import logging
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest


_TMP_DATA = Path(tempfile.mkdtemp(prefix="odysseus-email-summary-"))
os.environ.setdefault("DATA_DIR", str(_TMP_DATA))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP_DATA / 'app.db'}")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _route_endpoint(router, path: str, method: str):
    method = method.upper()
    for route in router.routes:
        if route.path == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")


@pytest.mark.asyncio
async def test_generate_email_summary_uses_shared_llm_adapter(monkeypatch):
    import routes.email_helpers as email_helpers
    import src.llm_core as llm_core

    calls = {}

    async def fake_llm_call_async(url, model, messages, **kwargs):
        calls["url"] = url
        calls["model"] = model
        calls["messages"] = messages
        calls["kwargs"] = kwargs
        return "thinking before marker\n<<<SUMMARY>>>\n- Pay the invoice by Friday.\n<<<END>>>"

    monkeypatch.setattr(llm_core, "llm_call_async", fake_llm_call_async)

    summary = await email_helpers._generate_email_summary(
        url="https://chatgpt.com/backend-api/codex/responses",
        model="gpt-5.5",
        sender="Billing <billing@example.com>",
        subject="Invoice due",
        body_for_llm="Please pay invoice 123 by Friday.",
        headers={"Authorization": "Bearer test"},
        max_tokens=1234,
        timeout=45,
    )

    assert summary == "- Pay the invoice by Friday."
    assert calls["url"] == "https://chatgpt.com/backend-api/codex/responses"
    assert calls["model"] == "gpt-5.5"
    assert calls["kwargs"]["headers"] == {"Authorization": "Bearer test"}
    assert calls["kwargs"]["temperature"] == 0.3
    assert calls["kwargs"]["max_tokens"] == 1234
    assert calls["kwargs"]["timeout"] == 45
    assert calls["kwargs"]["workload"] == "foreground"
    assert calls["messages"][0]["role"] == "system"
    assert calls["messages"][1]["role"] == "user"


@pytest.mark.asyncio
async def test_scheduled_email_summary_uses_background_fallback_chain(monkeypatch):
    import routes.email_helpers as email_helpers
    import src.llm_core as llm_core
    import src.task_endpoint as task_endpoint

    candidates = [
        ("http://primary.invalid/v1", "primary-model", {"X-Candidate": "primary"}),
        ("http://fallback.invalid/v1", "fallback-model", {"X-Candidate": "fallback"}),
    ]
    resolve_calls = []
    wait_calls = []
    llm_calls = []

    def fake_resolve_task_candidates(**kwargs):
        resolve_calls.append(kwargs)
        return candidates

    async def fake_wait_for_interactive_quiet(label):
        wait_calls.append(label)
        return False

    async def fake_llm_call_async(url, model, messages, **kwargs):
        llm_calls.append((url, model, messages, kwargs))
        if model == "primary-model":
            raise RuntimeError("primary unavailable")
        return "<<<SUMMARY>>>\n- Used the fallback model.\n<<<END>>>"

    monkeypatch.setattr(task_endpoint, "resolve_task_candidates", fake_resolve_task_candidates)
    monkeypatch.setattr(task_endpoint, "wait_for_interactive_quiet", fake_wait_for_interactive_quiet)
    monkeypatch.setattr(llm_core, "llm_call_async", fake_llm_call_async)

    summary = await email_helpers._generate_scheduled_email_summary(
        url="http://caller-fallback.invalid/v1",
        model="caller-fallback-model",
        sender="Sender <sender@example.com>",
        subject="Scheduled subject",
        body_for_llm="Please summarize this scheduled email.",
        headers={"Authorization": "Bearer test"},
        owner="alice",
        max_tokens=321,
        timeout=54,
    )

    assert summary == "- Used the fallback model."
    assert resolve_calls == [{
        "fallback_url": "http://caller-fallback.invalid/v1",
        "fallback_model": "caller-fallback-model",
        "fallback_headers": {"Authorization": "Bearer test"},
        "owner": "alice",
    }]
    assert wait_calls == ["background task LLM"]
    assert [call[1] for call in llm_calls] == ["primary-model", "fallback-model"]
    assert all(call[3]["workload"] == "background" for call in llm_calls)
    assert all(call[3]["max_tokens"] == 321 for call in llm_calls)
    assert all(call[3]["timeout"] == 54 for call in llm_calls)


@pytest.mark.asyncio
async def test_scheduled_local_summary_is_preempted_by_foreground_call(monkeypatch):
    import routes.email_helpers as email_helpers
    import src.llm_core as llm_core
    import src.task_endpoint as task_endpoint

    local_url = "http://127.0.0.1:11434/v1/chat/completions"
    background_started = asyncio.Event()
    never_release = asyncio.Event()
    observed_workloads = []

    monkeypatch.setenv("ODYSSEUS_LOCAL_MODEL_GATE", "true")
    monkeypatch.setenv("BACKGROUND_TASK_FOREGROUND_GATE", "false")
    monkeypatch.setattr(llm_core, "_LOCAL_MODEL_LOCK", asyncio.Lock())
    monkeypatch.setattr(llm_core, "_LOCAL_MODEL_CURRENT", {})
    monkeypatch.setattr(llm_core, "_LOCAL_MODEL_WAITING_FOREGROUND", 0)
    monkeypatch.setattr(
        task_endpoint,
        "resolve_task_candidates",
        lambda **_kwargs: [(local_url, "scheduled-model", {})],
    )

    async def fake_wait_for_interactive_quiet(_label):
        return False

    async def gated_llm_call(url, model, messages, **kwargs):
        assert messages
        workload = kwargs.get("workload")
        observed_workloads.append(workload)
        async with llm_core._local_model_slot(url, model, workload=workload):
            background_started.set()
            await never_release.wait()
        return "unreachable"

    monkeypatch.setattr(task_endpoint, "wait_for_interactive_quiet", fake_wait_for_interactive_quiet)
    monkeypatch.setattr(llm_core, "llm_call_async", gated_llm_call)

    background_task = asyncio.create_task(email_helpers._generate_scheduled_email_summary(
        url=local_url,
        model="scheduled-model",
        sender="Sender",
        subject="Scheduled",
        body_for_llm="Scheduled body",
        owner="alice",
    ))
    foreground_task = None
    try:
        await asyncio.wait_for(background_started.wait(), timeout=1)

        async def run_foreground():
            async with llm_core._local_model_slot(
                local_url,
                "interactive-model",
                workload="foreground",
            ):
                return True

        foreground_task = asyncio.create_task(run_foreground())
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(background_task, timeout=1)
        assert await asyncio.wait_for(foreground_task, timeout=1) is True
        assert observed_workloads == ["background"]
    finally:
        for task in (background_task, foreground_task):
            if task is not None and not task.done():
                task.cancel()


@pytest.mark.asyncio
async def test_manual_email_summary_uses_shared_helper_and_caches(tmp_path, monkeypatch):
    import routes.email_helpers as email_helpers
    import routes.email_routes as email_routes
    import src.endpoint_resolver as endpoint_resolver

    db_path = tmp_path / "scheduled_emails.db"
    monkeypatch.setattr(email_helpers, "SCHEDULED_DB", db_path)
    monkeypatch.setattr(email_routes, "SCHEDULED_DB", db_path)
    email_helpers._init_scheduled_db()

    resolve_calls = []

    def fake_resolve_endpoint(kind, owner=None):
        resolve_calls.append((kind, owner))
        assert kind == "utility"
        assert owner == "alice"
        return (
            "https://chatgpt.com/backend-api/codex/responses",
            "gpt-5.5",
            {"Authorization": "Bearer test"},
        )

    helper_calls = {}

    async def fake_generate_email_summary(**kwargs):
        helper_calls.update(kwargs)
        return "- Manual summary"

    monkeypatch.setattr(endpoint_resolver, "resolve_endpoint", fake_resolve_endpoint)
    monkeypatch.setattr(email_routes, "_generate_email_summary", fake_generate_email_summary)

    router = email_routes.setup_email_routes()
    summarize = _route_endpoint(router, "/api/email/summarize", "POST")

    result = await summarize(
        {
            "body": "This is a long enough email body for manual summary.",
            "subject": "Manual subject",
            "from": "Sender <sender@example.com>",
            "message_id": "<manual@example.com>",
            "folder": "INBOX",
        },
        owner="alice",
    )

    assert result == {
        "success": True,
        "summary": "- Manual summary",
        "model_used": "gpt-5.5",
    }
    assert resolve_calls == [("utility", "alice")]
    assert helper_calls["url"] == "https://chatgpt.com/backend-api/codex/responses"
    assert helper_calls["model"] == "gpt-5.5"
    assert helper_calls["headers"]["Authorization"] == "Bearer test"
    assert helper_calls["headers"]["Content-Type"] == "application/json"

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT owner, summary, model_used FROM email_summaries WHERE message_id=?",
            ("<manual@example.com>",),
        ).fetchone()
    finally:
        conn.close()
    assert row == ("alice", "- Manual summary", "gpt-5.5")


@pytest.mark.asyncio
@pytest.mark.parametrize("exception_kind", ["http", "runtime"])
async def test_manual_email_summary_never_exposes_provider_exception(
    monkeypatch,
    caplog,
    exception_kind,
):
    from fastapi import HTTPException
    import routes.email_routes as email_routes
    import src.endpoint_resolver as endpoint_resolver

    secret_detail = (
        "endpoint=https://private.example.internal/v1 provider=ollama "
        "model=private-model response_body=private-response "
        "Authorization: Bearer token-secret-value"
    )

    def fake_resolve_endpoint(kind, owner=None):
        assert kind == "utility"
        assert owner == "alice"
        return (
            "https://private.example.internal/v1",
            "private-model",
            {"Authorization": "Bearer token-secret-value"},
        )

    async def fail_summary(**_kwargs):
        if exception_kind == "http":
            raise HTTPException(status_code=502, detail=secret_detail)
        raise RuntimeError(secret_detail)

    monkeypatch.setattr(endpoint_resolver, "resolve_endpoint", fake_resolve_endpoint)
    monkeypatch.setattr(email_routes, "_generate_email_summary", fail_summary)
    caplog.set_level(logging.WARNING, logger=email_routes.__name__)

    router = email_routes.setup_email_routes()
    summarize = _route_endpoint(router, "/api/email/summarize", "POST")
    result = await summarize(
        {
            "body": "This email body is long enough to summarize.",
            "subject": "Sensitive provider failure",
            "from": "Sender <sender@example.com>",
        },
        owner="alice",
    )

    assert result == {
        "success": False,
        "error": "Failed to summarize",
        "error_code": "email_summary_unavailable",
    }
    exposed = json.dumps(result) + caplog.text
    for marker in (
        "private.example.internal",
        "ollama",
        "private-model",
        "private-response",
        "token-secret-value",
    ):
        assert marker not in exposed
    assert f"type={'HTTPException' if exception_kind == 'http' else 'RuntimeError'}" in caplog.text


@pytest.mark.asyncio
async def test_scheduled_email_summary_uses_shared_helper_and_caches(tmp_path, monkeypatch):
    import routes.email_helpers as email_helpers
    import routes.email_pollers as email_pollers

    db_path = tmp_path / "scheduled_emails.db"
    monkeypatch.setattr(email_helpers, "SCHEDULED_DB", db_path)
    monkeypatch.setattr(email_pollers, "SCHEDULED_DB", db_path)
    email_helpers._init_scheduled_db()

    raw_email = (
        b"From: Sender <sender@example.com>\r\n"
        b"To: Alice <alice@example.com>\r\n"
        b"Subject: Scheduled subject\r\n"
        b"Message-ID: <scheduled@example.com>\r\n"
        b"Date: Tue, 01 Jan 2026 12:00:00 +0000\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        + (b"Please review this scheduled summary email. " * 8)
    )

    class FakeImap:
        def __init__(self):
            self.logout_calls = 0

        def select(self, _folder, readonly=True):
            return "OK", []

        def uid(self, command, *args):
            if command == "SEARCH":
                return "OK", [b"1"]
            if command == "FETCH":
                return "OK", [(b"1 (RFC822)", raw_email)]
            raise AssertionError(f"unexpected uid command: {command!r} {args!r}")

        def logout(self):
            self.logout_calls += 1

    fake_conn = FakeImap()

    def fake_resolve_task_candidates(owner=None):
        assert owner == "alice"
        return [(
            "https://chatgpt.com/backend-api/codex/responses",
            "gpt-5.5",
            {"Authorization": "Bearer test"},
        )]

    helper_calls = {}

    async def fake_generate_email_summary(**kwargs):
        helper_calls.update(kwargs)
        return "- Scheduled summary"

    monkeypatch.setattr(email_pollers, "_load_settings", lambda: {"email_auto_summarize": True})
    monkeypatch.setattr(email_pollers, "_owner_for_email_account", lambda _account_id: "alice")
    monkeypatch.setattr(email_pollers, "_imap_connect", lambda account_id=None, owner="": fake_conn)
    monkeypatch.setattr(email_pollers, "_get_email_config", lambda account_id=None, owner="": {"from_address": "alice@example.com"})
    monkeypatch.setattr(email_pollers, "resolve_task_candidates", fake_resolve_task_candidates)
    monkeypatch.setattr(email_pollers, "_generate_scheduled_email_summary", fake_generate_email_summary)

    result = await email_pollers._auto_summarize_pass_single(account_id="acct-alice")

    assert "summarized 1" in result
    assert "summary failed" not in result
    assert helper_calls["url"] == "https://chatgpt.com/backend-api/codex/responses"
    assert helper_calls["model"] == "gpt-5.5"
    assert helper_calls["headers"]["Authorization"] == "Bearer test"
    assert helper_calls["headers"]["Content-Type"] == "application/json"
    assert helper_calls["owner"] == "alice"
    assert fake_conn.logout_calls == 1

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT owner, summary, model_used FROM email_summaries WHERE message_id=?",
            ("<scheduled@example.com>",),
        ).fetchone()
    finally:
        conn.close()
    assert row == ("alice", "- Scheduled summary", "gpt-5.5")
