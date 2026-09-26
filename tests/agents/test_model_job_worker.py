"""Bounded-job worker: typed results, no conversation backend, fail closed."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.agents._import_agents import ensure_agents_package

ensure_agents_package()

from services.agents import model_jobs as model_jobs_mod  # noqa: E402
from services.agents.model_jobs import InvalidArchetype, ModelJobExecutor  # noqa: E402

_ROOT = Path(__file__).resolve().parents[2]
_OVERLAY = _ROOT / "docker-compose.openhands.yml"
_WORKER = _ROOT / "services" / "agents" / "model_job_worker.py"
_WEB_SOURCES = (
    _ROOT / "src" / "ai_interaction.py",
    _ROOT / "routes" / "chat_routes.py",
    _ROOT / "routes" / "chat_helpers.py",
    _ROOT / "services" / "agents" / "model_jobs.py",
)
_INFERENCE_KEY_NAMES = (
    "NINE_ROUTER_KEY",
    "NINE_ROUTER_API_KEY",
    "NINE_ROUTER_INFERENCE_KEY",
    "NINEROUTER_KEY",
    "NINEROUTER_API_KEY",
    "9ROUTER_KEY",
    "9ROUTER_API_KEY",
)
_SECRET_MARKERS = (
    "sk-odysseusjobs",
    "sk-odysseus-jobs",
    "Authorization",
    "api_key",
    "virtual_key",
)


def _job_archetype(**overrides: object) -> SimpleNamespace:
    data = dict(
        id="extract-fields",
        version=1,
        execution_kind="model-job",
        input_schema="ExtractInputV1",
        result_schema="ExtractResultV1",
        token_limit=128,
        timeout_seconds=10,
        tools=[],
        conversation_policy=None,
        workspace_policy=None,
        delegation_policy=None,
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def _service_block(compose: str, name: str) -> str:
    header = f"  {name}:"
    lines = compose.splitlines()
    start = next((index for index, line in enumerate(lines) if line == header), None)
    if start is None:
        return ""
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.startswith("  ") and not line.startswith("    ") and line.endswith(":"):
            end = index
            break
        if line and not line.startswith(" ") and not line.startswith("#"):
            end = index
            break
    return "\n".join(lines[start:end])


def test_structured_extraction_returns_object_without_conversation_or_chat():
    """AE4: typed job object, provenance, no OpenHands/Canvas/chat identity."""
    from services.agents.model_job_worker import execute_worker_job

    created_conversations: list[object] = []
    chat_rows: list[object] = []

    def invoke(payload, **kwargs):
        assert payload["text"] == "Ada Lovelace invented the analyst role."
        return {
            "fields": {"name": "Ada Lovelace", "role": "analyst"},
            "_provenance": {
                "resolved_model": "openai/auto",
                "resolved_route": "9router",
                "api_key": "sk-secret-should-not-leak",
                "openhands_conversation_id": "conv-should-not-exist",
            },
        }

    result = execute_worker_job(
        _job_archetype(),
        {"text": "Ada Lovelace invented the analyst role."},
        owner="owner-1",
        invoke=invoke,
    )
    assert result.output == {"fields": {"name": "Ada Lovelace", "role": "analyst"}}
    assert result.owner == "owner-1"
    assert result.conversation_id is None
    assert result.audit["resolved_model"] == "openai/auto"
    assert result.audit["resolved_route"] == "9router"
    assert "api_key" not in result.audit
    assert "sk-secret-should-not-leak" not in json.dumps(result.audit)
    assert result.audit.get("openhands_conversation_id") is None
    assert created_conversations == []
    assert chat_rows == []


def test_compare_runs_two_bounded_jobs_with_resolved_model_provenance():
    def invoke(payload, **kwargs):
        pane = payload["pane_id"]
        return {
            "text": f"pane-{pane}",
            "_provenance": {
                "resolved_model": f"model-{pane}",
                "resolved_route": "9router",
            },
        }

    run_compare_jobs = model_jobs_mod.run_compare_jobs
    results = run_compare_jobs(
        (
            {"pane_id": "left", "text": "summarize A", "model": "auto"},
            {"pane_id": "right", "text": "summarize B", "model": "balanced"},
        ),
        owner="owner-1",
        invoke=invoke,
    )
    assert len(results) == 2
    assert [item.output["text"] for item in results] == ["pane-left", "pane-right"]
    assert [item.audit["resolved_model"] for item in results] == ["model-left", "model-right"]
    assert all(item.conversation_id is None for item in results)
    assert all(item.owner == "owner-1" for item in results)


def test_invoke_structured_model_fails_closed_when_worker_unavailable(monkeypatch):
    from src.ai_interaction import invoke_structured_model

    monkeypatch.delenv("ODYSSEUS_MODEL_JOB_WORKER_URL", raising=False)
    with pytest.raises(model_jobs_mod.ModelJobFailed, match="worker"):
        invoke_structured_model(
            {"text": "extract"},
            archetype=_job_archetype(),
            owner="owner-1",
        )


def test_worker_missing_ninerouter_access_fails_closed():
    from services.agents.model_job_worker import invoke_nine_router

    with pytest.raises(model_jobs_mod.ModelJobFailed, match="9router"):
        invoke_nine_router(
            {"text": "extract"},
            archetype=_job_archetype(),
            owner="owner-1",
            base_url="",
            virtual_key="",
        )


def test_rewrite_and_compare_have_no_stream_llm_fallback():
    source = (_ROOT / "routes" / "chat_routes.py").read_text(encoding="utf-8")
    rewrite = source.split("async def stream_rewrite", 1)[1].split(
        "return StreamingResponse(stream_rewrite()", 1
    )[0]
    assert "stream_llm(" not in rewrite
    assert "stream_llm_with_fallback" not in rewrite
    assert "submit_model_job" in rewrite
    compare = source.split('elif compare_mode and chat_mode == "chat":', 1)[1]
    compare_body = compare.split("else:", 1)[0]
    assert "stream_llm_with_fallback" not in compare_body
    assert "stream_llm(" not in compare_body
    assert "submit_model_job" in compare_body


def test_model_job_still_rejects_agent_archetype_and_tools():
    executor = ModelJobExecutor(invoke=lambda payload, **kwargs: {"ok": True})
    with pytest.raises(InvalidArchetype, match="agent"):
        executor.execute(_job_archetype(execution_kind="agent"), {}, owner="u1")
    with pytest.raises(InvalidArchetype, match="tools"):
        executor.execute(_job_archetype(tools=["odysseus.mail.send"]), {}, owner="u1")


def test_model_job_worker_calls_configure_tracer():
    """Worker must export to the Collector; env alone is not enough."""
    worker_src = _WORKER.read_text(encoding="utf-8")
    assert "configure_tracer" in worker_src
    assert "chat_completion_span" in worker_src or "gen_ai.operation.name" in worker_src
    assert "prefer_stdlib_calendar" in worker_src


def test_prefer_stdlib_calendar_exposes_timegm(monkeypatch, tmp_path):
    """Fake app calendar on path must not hide stdlib timegm after prefer."""
    from services.observability.stdlib_calendar import prefer_stdlib_calendar

    fake = tmp_path / "calendar"
    fake.mkdir()
    (fake / "__init__.py").write_text("# app calendar shadow\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    import sys

    sys.modules.pop("calendar", None)
    import calendar as shadowed

    assert not hasattr(shadowed, "timegm")
    prefer_stdlib_calendar()
    import calendar as fixed

    assert hasattr(fixed, "timegm")


def test_overlay_odysseus_env_omits_worker_ninerouter_key():
    compose = _OVERLAY.read_text(encoding="utf-8")
    odysseus = _service_block(compose, "odysseus")
    worker = _service_block(compose, "odysseus-model-jobs")
    assert odysseus, "overlay must keep odysseus service"
    assert worker, "overlay must add odysseus-model-jobs worker"
    leaked = [
        name
        for name in _INFERENCE_KEY_NAMES
        if name in odysseus or name in {part.split("=", 1)[0] for part in odysseus.split()}
    ]
    assert leaked == []
    assert "sk-" not in odysseus
    assert "odysseus-jobs" not in odysseus
    list_entries = [line.strip() for line in odysseus.splitlines() if line.strip().startswith("- ")]
    assert any("ODYSSEUS_MODEL_JOB_WORKER_URL=" in line for line in list_entries)
    # Metadata/catalog URLs on odysseus are fine; inference keys and the jobs
    # catalog default stay on odysseus-model-jobs only.
    assert "NINE_ROUTER_DEFAULT_MODEL" not in odysseus
    assert "NINE_ROUTER_V1" not in odysseus
    assert "NINE_ROUTER_SQLITE" not in odysseus
    assert "9router:/opt/odysseus/9router-data" in worker
    assert "NINE_ROUTER_DEFAULT_MODEL=cx/gpt-5.5" in worker
    assert "./services:/app/services:ro" in worker
    assert "python" in worker and "model_job_worker" in worker
    assert "entrypoint:" in worker
    assert "pull_policy: never" in worker
    assert _service_block(compose, "9router")
    assert "deploy/openhands/opencode.json" in compose
    assert "deploy/openhands/hermes/config.yaml" in compose


def test_web_sources_do_not_hold_worker_ninerouter_key():
    worker_src = _WORKER.read_text(encoding="utf-8")
    assert "odysseus-jobs" in worker_src
    assert "apiKeys" in worker_src
    for path in _WEB_SOURCES:
        text = path.read_text(encoding="utf-8")
        assert "odysseus-jobs" not in text
        assert "sk-odysseusjobs" not in text
        assert "NINE_ROUTER_SQLITE" not in text
        if path.name == "ai_interaction.py":
            invoke = text.split("def invoke_structured_model", 1)[1].split("\ndef ", 1)[0]
            assert "/v1/chat/completions" not in invoke
            assert "Bearer" not in invoke


def test_worker_has_no_mcp_workspace_transcript_or_agent_profile():
    text = _WORKER.read_text(encoding="utf-8")
    lowered = text.lower()
    assert "mcp" not in lowered
    assert "workspace" not in lowered
    assert "transcript" not in lowered
    assert "agent_profile" not in lowered
    assert "openhands" not in lowered
    assert "ChatMessage" not in text


def test_session_title_heuristic_used_when_sufficient():
    session_title_heuristic = model_jobs_mod.session_title_heuristic
    title = session_title_heuristic("Please fix the endpoint fallback bug.")
    assert title == "Please fix the endpoint fallback bug"
    assert session_title_heuristic("hi") is None
    assert session_title_heuristic("  ???  ") is None


def test_auto_name_session_skips_worker_when_heuristic_titles(monkeypatch):
    import routes.chat_helpers as chat_helpers

    calls: list[object] = []
    monkeypatch.setattr(
        chat_helpers,
        "submit_model_job",
        lambda *args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(output={"title": "nope"}),
    )
    sess = SimpleNamespace(
        id="session-1",
        owner="alice",
        history=[SimpleNamespace(role="user", content="Please fix the endpoint fallback bug.")],
    )
    updates: list[tuple[str, str]] = []
    session_manager = SimpleNamespace(
        update_session_name=lambda session_id, title: updates.append((session_id, title))
    )
    import asyncio

    asyncio.run(chat_helpers.auto_name_session(session_manager, sess))
    assert calls == []
    assert updates == [("session-1", "Please fix the endpoint fallback bug")]


def test_interactive_chat_remains_stream_governed_agent():
    source = (_ROOT / "routes" / "chat_routes.py").read_text(encoding="utf-8")
    stream = source.split("async def chat_stream", 1)[1].split("async def chat_resume", 1)[0]
    assert "async for chunk in stream_governed_agent(" in stream


def test_submit_model_job_uses_executor_and_strips_secrets():
    def invoke(payload, **kwargs):
        return {
            "title": "Short Title",
            "_provenance": {"resolved_model": "auto", "api_key": "sk-leak"},
        }

    submit_model_job = model_jobs_mod.submit_model_job
    result = submit_model_job(_job_archetype(id="session-title"), {"text": "hi there friend"}, "u1", invoke=invoke)
    assert result.output == {"title": "Short Title"}
    assert "api_key" not in result.audit
    assert result.audit["resolved_model"] == "auto"


def test_resolve_nine_router_model_maps_overlay_routes_to_catalog():
    """Overlay route names and LiteLLM prefixes must become catalog ids for 9router."""
    from services.agents.model_job_worker import resolve_nine_router_model

    assert resolve_nine_router_model(None) == "cx/gpt-5.5"
    assert resolve_nine_router_model("") == "cx/gpt-5.5"
    assert resolve_nine_router_model("auto") == "cx/gpt-5.5"
    assert resolve_nine_router_model("automatic") == "cx/gpt-5.5"
    assert resolve_nine_router_model("fast") == "cx/gpt-5.5"
    assert resolve_nine_router_model("balanced") == "cx/gpt-5.5"
    assert resolve_nine_router_model("best") == "cx/gpt-5.5"
    assert resolve_nine_router_model("openai/auto") == "cx/gpt-5.5"
    assert resolve_nine_router_model("openai/automatic") == "cx/gpt-5.5"
    assert resolve_nine_router_model("openai/cx/gpt-5.5") == "cx/gpt-5.5"
    assert resolve_nine_router_model("cx/gpt-5.5") == "cx/gpt-5.5"
    assert resolve_nine_router_model("cx/gpt-5.4") == "cx/gpt-5.4"


def test_resolve_nine_router_model_honors_env_default(monkeypatch):
    from services.agents.model_job_worker import resolve_nine_router_model

    monkeypatch.setenv("NINE_ROUTER_DEFAULT_MODEL", "cx/gpt-5.4")
    assert resolve_nine_router_model("automatic") == "cx/gpt-5.4"
    monkeypatch.setenv("NINE_ROUTER_DEFAULT_MODEL", "openai/cx/gpt-5.4-mini")
    assert resolve_nine_router_model("auto") == "cx/gpt-5.4-mini"


def test_invoke_nine_router_posts_catalog_model_not_overlay_route(monkeypatch):
    """Direct 9router rejects auto/automatic; worker must send a catalog id."""
    from services.agents import model_job_worker as worker

    captured: dict[str, object] = {}

    class _Resp:
        status = 200

        def read(self) -> bytes:
            return json.dumps(
                {
                    "model": "gpt-5.5",
                    "choices": [{"message": {"content": "Short Title"}}],
                }
            ).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode())
        return _Resp()

    monkeypatch.setattr(worker.urlrequest, "urlopen", fake_urlopen)
    monkeypatch.delenv("NINE_ROUTER_DEFAULT_MODEL", raising=False)

    out = worker.invoke_nine_router(
        {"text": "hello world", "model": "automatic"},
        archetype=_job_archetype(id="session-title", token_limit=64),
        owner="u1",
        base_url="http://9router:20128/v1",
        virtual_key="sk-test",
    )
    assert captured["body"]["model"] == "cx/gpt-5.5"
    assert out["title"] == "Short Title"
    assert out["_provenance"]["resolved_model"] == "gpt-5.5"
