"""Mail/calendar leftovers must be bounded jobs, not llm_call* on the web process."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_FORBIDDEN_CALLS = (
    "llm_call_async(",
    "llm_call_async_with_fallback(",
    "task_llm_call_async(",
    "stream_llm(",
    "stream_llm_with_fallback(",
)


def _function_source(path: str, name: str) -> str:
    source = (_ROOT / path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"{name} not found in {path}")


def _assert_bounded_job(body: str, *, job_id: str | None = None) -> None:
    assert "submit_model_job" in body
    assert "bounded_archetype" in body
    for token in _FORBIDDEN_CALLS:
        assert token not in body, f"leftover {token} in body"
    if job_id is not None:
        assert job_id in body


def test_email_helpers_summaries_are_model_jobs():
    interactive = _function_source("routes/email_helpers.py", "_generate_email_summary")
    scheduled = _function_source("routes/email_helpers.py", "_generate_scheduled_email_summary")
    _assert_bounded_job(interactive, job_id="email-summary")
    _assert_bounded_job(scheduled, job_id="email-summary")


def test_email_pollers_have_no_task_llm_call():
    source = (_ROOT / "routes" / "email_pollers.py").read_text(encoding="utf-8")
    for token in _FORBIDDEN_CALLS:
        assert token not in source, f"leftover {token} in email_pollers.py"
    body = _function_source("routes/email_pollers.py", "_auto_summarize_pass_single")
    assert "submit_model_job" in body
    assert "bounded_archetype" in body


def test_email_route_leftovers_are_model_jobs():
    _assert_bounded_job(
        _function_source("routes/email_routes.py", "extract_writing_style"),
        job_id="email-style",
    )
    _assert_bounded_job(
        _function_source("routes/email_routes.py", "translate_email"),
        job_id="email-translate",
    )
    _assert_bounded_job(
        _function_source("routes/email_routes.py", "ai_reply"),
        job_id="email-reply",
    )


def test_calendar_quick_parse_is_model_job():
    body = _function_source("routes/calendar_routes.py", "quick_parse")
    _assert_bounded_job(body, job_id="calendar-parse")
    assert "submit_model_job" in body
    assert "owner" in body


def test_mcp_ai_draft_reply_is_model_job():
    _assert_bounded_job(
        _function_source("mcp_servers/email_server.py", "_ai_draft_reply_to_email"),
        job_id="email-reply",
    )


def test_builtin_mail_calendar_actions_are_model_jobs():
    source = (_ROOT / "src" / "builtin_actions.py").read_text(encoding="utf-8")
    for token in _FORBIDDEN_CALLS:
        assert token not in source, f"leftover {token} in builtin_actions.py"
    _assert_bounded_job(
        _function_source("src/builtin_actions.py", "_translate"),
        job_id="email-translate",
    )
    _assert_bounded_job(
        _function_source("src/builtin_actions.py", "_try_ai_tidy_group"),
        job_id="memory-tidy",
    )
    _assert_bounded_job(
        _function_source("src/builtin_actions.py", "action_classify_events"),
        job_id="calendar-classify",
    )
    _assert_bounded_job(
        _function_source("src/builtin_actions.py", "action_learn_sender_signatures"),
        job_id="email-sender-sig",
    )
    _assert_bounded_job(
        _function_source("src/builtin_actions.py", "action_check_email_urgency"),
        job_id="email-urgency",
    )


@pytest.mark.asyncio
async def test_generate_email_summary_uses_submit_model_job(monkeypatch):
    import routes.email_helpers as email_helpers

    jobs = []

    def fake_submit(archetype, payload, owner, **_kwargs):
        jobs.append((archetype.id, payload, owner, archetype.temperature, archetype.token_limit))
        return SimpleNamespace(
            output={"text": "thinking\n<<<SUMMARY>>>\n- Pay the invoice by Friday.\n<<<END>>>"},
            audit={"resolved_model": "auto"},
        )

    monkeypatch.setattr(email_helpers, "submit_model_job", fake_submit)

    summary = await email_helpers._generate_email_summary(
        url="https://unused.example/v1",
        model="unused-model",
        sender="Billing <billing@example.com>",
        subject="Invoice due",
        body_for_llm="Please pay invoice 123 by Friday.",
        headers={"Authorization": "Bearer test"},
        owner="alice",
        max_tokens=1234,
        timeout=45,
    )

    assert summary == "- Pay the invoice by Friday."
    assert len(jobs) == 1
    job_id, payload, owner, temperature, token_limit = jobs[0]
    assert job_id == "email-summary"
    assert owner == "alice"
    assert temperature == 0.3
    assert token_limit == 1234
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1]["role"] == "user"
    assert "Invoice due" in payload["messages"][1]["content"]


@pytest.mark.asyncio
async def test_scheduled_email_summary_fail_closed_has_no_llm_fallback(monkeypatch):
    import routes.email_helpers as email_helpers
    from services.agents.model_jobs import ModelJobFailed

    llm_calls = []

    def boom(*_args, **_kwargs):
        raise ModelJobFailed("worker unavailable")

    async def fake_llm(*_args, **_kwargs):
        llm_calls.append(True)
        return "should-not-run"

    monkeypatch.setattr(email_helpers, "submit_model_job", boom)
    monkeypatch.setattr("src.llm_core.llm_call_async", fake_llm, raising=False)
    monkeypatch.setattr("src.task_endpoint.task_llm_call_async", fake_llm, raising=False)

    with pytest.raises(ModelJobFailed, match="worker"):
        await email_helpers._generate_scheduled_email_summary(
            url="http://unused.example/v1",
            model="unused",
            sender="Sender",
            subject="Subject",
            body_for_llm="Body",
            owner="alice",
        )

    assert llm_calls == []
