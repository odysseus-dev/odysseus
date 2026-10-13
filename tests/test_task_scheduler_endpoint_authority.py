"""Scheduler credential recovery uses registered endpoint identity after normalization."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlparse

import pytest

import core.database as database
import src.endpoint_resolver as resolver
from src.agent_runtime.authority import seal_task_authority
from src.task_scheduler import TaskScheduler
from tests.helpers.database import disposable_database


PROVIDERS = [
    ("openai", "https://api.openai.com", "https://api.openai.com/v1/chat/completions"),
    ("anthropic_v1", "https://api.anthropic.com/v1", "https://api.anthropic.com/v1/messages"),
    ("ollama", "http://192.168.1.5:11434", "http://192.168.1.5:11434/api/chat"),
    ("generic", "https://models.example.test/v1", "https://models.example.test/v1/chat/completions"),
    ("anthropic_root", "https://api.anthropic.com", "https://api.anthropic.com/v1/messages"),
    ("ollama_api", "http://192.168.1.5:11434/api", "http://192.168.1.5:11434/api/chat"),
]


@pytest.fixture
def execution(tmp_path, monkeypatch):
    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr(database, "SessionLocal", factory)
        monkeypatch.setattr(resolver, "resolve_url", lambda url: url)
        monkeypatch.setattr("src.interactive_gate.wait_for_interactive_quiet", AsyncMock())
        monkeypatch.setattr("src.task_endpoint.resolve_task_candidates", lambda **kwargs: [])
        monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: default)
        monkeypatch.setattr("src.task_scheduler._resolve_task_timezone", lambda db, task: "UTC")
        monkeypatch.setattr("src.tool_index.get_tool_index", lambda: None)
        monkeypatch.setattr("src.research_handler.RESEARCH_DATA_DIR", tmp_path / "research")
        monkeypatch.setattr("src.event_bus.fire_event", lambda *args: None)
        captured = {}

        async def stream(**kwargs):
            captured.update(endpoint_url=kwargs["endpoint_url"], headers=kwargs["headers"])
            yield 'data: {"delta": "Complete"}\n\n'

        monkeypatch.setattr("src.agent_loop.stream_agent_loop", stream)
        researcher = SimpleNamespace(research=AsyncMock(return_value="Complete"),
                                     get_stats=lambda: {}, findings=[])

        def research(**kwargs):
            captured.update(endpoint_url=kwargs["llm_endpoint"], headers=kwargs["llm_headers"])
            return researcher

        monkeypatch.setattr("src.deep_research.DeepResearcher", research)

        async def run(kind, url, *, execute_agent_task=True, **overrides):
            task = SimpleNamespace(
                id="task", owner="alice", name="Research task", prompt="Research this topic",
                task_type="research" if kind == "research" else "llm", action=None,
                endpoint_url=url, model="model", session_id="session", max_steps=1,
                crew_member_id=None, character_id=None,
                headers={"Authorization": "Bearer caller-secret", "x-api-key": "caller-secret"},
            )
            task.__dict__.update(overrides)
            task.request_authority_json = seal_task_authority(
                task.prompt, task.task_type, task.action, owner=task.owner)
            scheduler = TaskScheduler.__new__(TaskScheduler)
            scheduler._session_manager = None
            if kind == "agent":
                if execute_agent_task:
                    with factory() as db:
                        result = await scheduler._execute_llm_task(task, db)
                else:
                    result = await scheduler._run_agent_loop(url, task.model, task, task.session_id)
            else:
                with factory() as db:
                    result = await scheduler._execute_research_task(task, db)
            assert result == "Complete"
            return captured

        yield factory, run


@pytest.mark.parametrize("kind", ["agent", "research"])
@pytest.mark.parametrize("provider, base, chat_url", PROVIDERS)
@pytest.mark.parametrize("selection", ["base", "chat"])
@pytest.mark.parametrize("server_key", ["server-secret", None])
async def test_scheduler_recovers_static_credentials_after_chat_normalization(execution, kind, provider, base, chat_url, selection, server_key):
    factory, run = execution
    with factory() as db:
        db.add(database.ModelEndpoint(id="endpoint", name="Endpoint", owner="alice",
                                     base_url=base, api_key=server_key, is_enabled=True))
        db.commit()
    captured = await run(kind, base if selection == "base" else chat_url)
    assert captured == {"endpoint_url": chat_url, "headers": resolver.build_headers(server_key, base)}


@pytest.mark.parametrize("kind", ["agent", "research"])
@pytest.mark.parametrize("provider, base, chat_url", PROVIDERS)
async def test_scheduler_refreshes_registered_session_credentials(execution, monkeypatch, kind, provider, base, chat_url):
    factory, run = execution
    with factory() as db:
        db.add(database.ModelEndpoint(id="endpoint", name="Endpoint", owner="alice",
                                     base_url=base, api_key="stale-secret", is_enabled=True,
                                     provider_auth_id="server-session"))
        db.commit()
    runtime = MagicMock(return_value={"base_url": base, "api_key": "refreshed-secret"})
    monkeypatch.setattr("src.chatgpt_subscription.resolve_runtime_credentials", runtime)
    captured = await run(kind, base)
    runtime.assert_called_once_with("server-session", owner="alice")
    assert captured == {"endpoint_url": chat_url, "headers": resolver.build_headers("refreshed-secret", base)}


@pytest.mark.parametrize("kind", ["agent", "research"])
async def test_scheduler_uses_refreshed_server_runtime_url(execution, monkeypatch, kind):
    factory, run = execution
    base = "https://chatgpt.com/backend-api/codex"
    runtime_base = "https://runtime.example.test/v1"
    with factory() as db:
        db.add(database.ModelEndpoint(id="endpoint", name="Endpoint", owner="alice",
                                     base_url=base, api_key="stale-secret", is_enabled=True,
                                     provider_auth_id="server-session"))
        db.commit()
    runtime = MagicMock(return_value={"base_url": runtime_base, "api_key": "refreshed-secret"})
    monkeypatch.setattr("src.chatgpt_subscription.resolve_runtime_credentials", runtime)
    captured = await run(kind, base)
    runtime.assert_called_once_with("server-session", owner="alice")
    assert captured == {"endpoint_url": runtime_base + "/chat/completions",
                        "headers": {"Authorization": "Bearer refreshed-secret"}}


@pytest.mark.parametrize("kind", ["agent", "research"])
async def test_scheduler_refresh_failure_never_uses_static_or_caller_credentials(execution, monkeypatch, kind):
    factory, run = execution
    base = PROVIDERS[0][1]
    with factory() as db:
        db.add(database.ModelEndpoint(id="endpoint", name="Endpoint", owner="alice",
                                     base_url=base, api_key="stale-secret", is_enabled=True,
                                     provider_auth_id="server-session"))
        db.commit()
    runtime = MagicMock(side_effect=ValueError("Session expired"))
    monkeypatch.setattr("src.chatgpt_subscription.resolve_runtime_credentials", runtime)
    assert (await run(kind, base))["headers"] == {}
    runtime.assert_called_once_with("server-session", owner="alice")


@pytest.mark.parametrize("kind", ["agent", "research"])
@pytest.mark.parametrize("provider, base, chat_url", PROVIDERS[:4])
@pytest.mark.parametrize("state", ["disabled", "foreign_owner", "unregistered", "shared"])
async def test_scheduler_credentials_require_enabled_visible_registration(execution, monkeypatch, kind, provider, base, chat_url, state):
    factory, run = execution
    with factory() as db:
        if state != "unregistered":
            db.add(database.ModelEndpoint(id="endpoint", name="Endpoint",
                                         owner="bob" if state == "foreign_owner" else None if state == "shared" else "alice",
                                         base_url=base, api_key="server-secret", is_enabled=state != "disabled"))
            db.commit()
    runtime = MagicMock(wraps=resolver.resolve_endpoint_runtime)
    monkeypatch.setattr(resolver, "resolve_endpoint_runtime", runtime)
    captured = await run(kind, base)
    if state == "shared":
        assert captured["headers"] == resolver.build_headers("server-secret", base)
        assert runtime.call_args.kwargs == {"owner": "alice"}
    else:
        assert captured["headers"] == {}
        runtime.assert_not_called()


@pytest.mark.parametrize("kind", ["agent", "research"])
@pytest.mark.parametrize("provider, base, chat_url", PROVIDERS[:4])
@pytest.mark.parametrize("change", [
    "scheme", "host", "port", "userinfo", "query", "fragment",
    "sibling", "descendant", "models", "responses",
])
async def test_scheduler_invalid_route_never_recovers_registered_credentials(execution, monkeypatch, kind, provider, base, chat_url, change):
    factory, run = execution
    with factory() as db:
        db.add(database.ModelEndpoint(id="endpoint", name="Endpoint", owner="alice",
                                     base_url=base, api_key="server-secret", is_enabled=True))
        db.commit()
    parsed = urlparse(chat_url)
    mutations = {
        "scheme": parsed._replace(scheme="https" if parsed.scheme == "http" else "http"),
        "host": parsed._replace(netloc="attacker.example.test"),
        "port": parsed._replace(netloc=f"{parsed.hostname}:4444"),
        "userinfo": parsed._replace(netloc=f"caller:secret@{parsed.netloc}"),
        "query": parsed._replace(query="api_key=caller-secret"),
        "fragment": parsed._replace(fragment="fragment"),
        "sibling": parsed._replace(path=urlparse(base).path + "/sibling"),
        "descendant": parsed._replace(path=parsed.path + "/descendant"),
        "models": parsed._replace(path=urlparse(base).path + "/models"),
        "responses": parsed._replace(path=urlparse(base).path + "/responses"),
    }
    runtime = MagicMock(wraps=resolver.resolve_endpoint_runtime)
    monkeypatch.setattr(resolver, "resolve_endpoint_runtime", runtime)
    captured = await run(kind, mutations[change].geturl(), execute_agent_task=False)
    assert captured["headers"] == {}
    runtime.assert_not_called()


async def test_scheduled_research_keeps_configured_resolver_credentials(execution, monkeypatch):
    _, run = execution
    url = "https://runtime.example.test/v1/chat/completions"
    headers = {"Authorization": "Bearer configured-server-secret"}
    configured = MagicMock(return_value=(url, "model", headers))
    monkeypatch.setattr(resolver, "resolve_endpoint", configured)
    recovery = MagicMock(side_effect=AssertionError("Configured credentials must not be replaced"))
    monkeypatch.setattr(resolver, "resolve_owner_registered_endpoint", recovery)
    captured = await run("research", None)
    assert captured == {"endpoint_url": url, "headers": headers}
    assert configured.call_args.kwargs["owner"] == "alice"
    recovery.assert_not_called()
