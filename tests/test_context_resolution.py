"""Turn-preparation context window resolution and its compact-runtime use."""
import asyncio
from types import SimpleNamespace
import json

import httpx
import pytest

import src.model_context as model_context
from src.agent_runtime import context_resolution as cr
from src.agent_runtime.context_resolution import (
    ContextEvidence,
    ContextObservation,
    UNRESOLVED_CONTEXT,
    combine_observations,
    context_metrics,
    resolve_effective_context,
)
from src.clean_agent_preview import stream_preview
from src.tool_policy import ToolPolicy
from src.turn_contract import resolve_full_inventory_contract

REMOTE = "http://provider.test/v1/chat/completions"
LOCAL = "http://127.0.0.1:8080/v1/chat/completions"
AUTH = {"Authorization": "Bearer secret-token", "Content-Type": "application/json"}

# Opt out of the conftest guard; every test here installs a fake HTTP client.
CONTEXT_PROBE_NETWORK = True


def _obs(evidence, value, source="test"):
    return ContextObservation(evidence, value, source)


class _Response:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _ModelStream:
    def __init__(self, lines, status=200, text=""):
        self.status_code = status
        self.text = text
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aread(self):
        return self.text.encode()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"unexpected provider status {self.status_code}")

    async def aiter_lines(self):
        for line in self._lines:
            yield line


def _answer_lines(usage=None):
    lines = ["data: " + json.dumps({"choices": [{"delta": {"content": "Done."}}]})]
    if usage is not None:
        lines.append("data: " + json.dumps({"choices": [{"delta": {}}], "usage": usage}))
    lines.append("data: [DONE]")
    return lines


class _FakeNetwork:
    """One fake HTTP surface for both metadata GETs and model streams.

    The compact runtime and the probe share ``httpx.AsyncClient``; recording
    every call in order proves which phase performed which I/O.
    """

    def __init__(self, routes=None, *, stream_responses=None, get_delay=0.0, get_error=None):
        self.routes = routes or {}
        self.stream_responses = list(stream_responses or [])
        self.get_delay = get_delay
        self.get_error = get_error
        self.calls = []
        self.requests = []
        self.client_kwargs = []

    def client_factory(self):
        network = self

        class Client:
            def __init__(self, **kwargs):
                network.client_kwargs.append(kwargs)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url, headers=None):
                network.calls.append(("get", url, dict(headers or {})))
                if network.get_delay:
                    await asyncio.sleep(network.get_delay)
                if network.get_error is not None:
                    raise network.get_error
                for suffix, response in network.routes.items():
                    if url.endswith(suffix):
                        return response
                return _Response(404, None)

            def stream(self, method, url, headers=None, json=None):
                network.calls.append(("stream", url, dict(headers or {})))
                network.requests.append(json)
                if network.stream_responses:
                    return network.stream_responses.pop(0)
                return _ModelStream(_answer_lines({"prompt_tokens": 2048, "completion_tokens": 3}))

        return Client


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    cr.clear_probe_cache()
    monkeypatch.setattr(model_context, "is_local_endpoint", lambda url: "127.0.0.1" in url)
    # The real URL builder may resolve hosts; these tests stay off the network.
    monkeypatch.setattr(
        "src.endpoint_resolver.build_models_url", lambda base: base.split("/v1")[0] + "/v1/models",
    )
    monkeypatch.setattr("src.endpoint_resolver.resolve_url", lambda url: url)
    yield
    cr.clear_probe_cache()


def _install(monkeypatch, network):
    monkeypatch.setattr(cr.httpx, "AsyncClient", network.client_factory())

    def legacy_get(url, *args, **kwargs):
        # The legacy model_context probe is synchronous; record it so a
        # terminal-metrics probe through that path is caught as well.
        network.calls.append(("sync_get", url, dict(kwargs.get("headers") or {})))
        return _Response(404, None)

    monkeypatch.setattr(cr.httpx, "get", legacy_get)
    return network


def _catalog(model, **fields):
    return _Response(200, {"data": [{"id": model, **fields}]})


# ---------------------------------------------------------------------------
# Pure combination rules
# ---------------------------------------------------------------------------

def test_operator_declared_window_replaces_known_table_without_contradiction():
    resolution = combine_observations([
        _obs(ContextEvidence.KNOWN_TABLE, 128000),
        _obs(ContextEvidence.OPERATOR_DECLARED, 200000),
    ])
    assert resolution.effective == 200000
    assert resolution.evidence is ContextEvidence.OPERATOR_DECLARED
    assert not resolution.mismatch


def test_operator_declared_window_caps_measured_window():
    resolution = combine_observations([
        _obs(ContextEvidence.PROVIDER_ADVERTISED, 131072),
        _obs(ContextEvidence.OPERATOR_DECLARED, 32768),
    ])
    assert (resolution.effective, resolution.evidence) == (32768, ContextEvidence.OPERATOR_DECLARED)
    # A tighter declared transport limit is a cap, not a contradiction.
    assert not resolution.mismatch


def test_operator_declaration_above_provider_is_contradiction_and_provider_wins():
    resolution = combine_observations([
        _obs(ContextEvidence.PROVIDER_ADVERTISED, 8192),
        _obs(ContextEvidence.OPERATOR_DECLARED, 32768),
    ])
    assert (resolution.effective, resolution.evidence) == (8192, ContextEvidence.PROVIDER_ADVERTISED)
    assert resolution.mismatch
    assert {c.evidence for c in (resolution.conflicts[0].first, resolution.conflicts[0].second)} == {
        ContextEvidence.PROVIDER_ADVERTISED, ContextEvidence.OPERATOR_DECLARED,
    }


def test_provider_advertised_beats_known_table_and_disagreement_is_visible():
    resolution = combine_observations([
        _obs(ContextEvidence.PROVIDER_ADVERTISED, 8192),
        _obs(ContextEvidence.KNOWN_TABLE, 131072),
    ])
    # The legacy probe takes max(api, table) for cloud endpoints. A static
    # table is weaker evidence and must not override the provider silently.
    assert (resolution.effective, resolution.evidence) == (8192, ContextEvidence.PROVIDER_ADVERTISED)
    assert resolution.mismatch


def test_runtime_confirmed_beats_provider_advertised_and_records_mismatch():
    resolution = combine_observations([
        _obs(ContextEvidence.PROVIDER_ADVERTISED, 32768),
        _obs(ContextEvidence.RUNTIME_CONFIRMED, 16384),
    ])
    assert (resolution.effective, resolution.evidence) == (16384, ContextEvidence.RUNTIME_CONFIRMED)
    assert resolution.mismatch


def test_no_evidence_is_unknown_zero_not_a_default():
    resolution = combine_observations([])
    assert resolution.effective == 0
    assert resolution.evidence is ContextEvidence.UNKNOWN
    assert resolution.budget_limit == 0
    assert context_metrics(resolution, 500)["context_length"] == 0


def test_runtime_limit_observation_is_pure_and_lowers_effective_window():
    base = combine_observations([_obs(ContextEvidence.PROVIDER_ADVERTISED, 8192)])
    updated = base.observe_runtime_limit(4096)
    assert (updated.effective, updated.evidence, updated.source) == (
        4096, ContextEvidence.RUNTIME_CONFIRMED, "provider_rejection",
    )
    assert updated.mismatch
    assert base.observe_runtime_limit(None) is base
    assert base.observe_runtime_limit(0) is base


def test_context_metrics_reports_percent_against_stored_window():
    resolution = combine_observations([_obs(ContextEvidence.KNOWN_TABLE, 8000)])
    metrics = context_metrics(resolution, 2000)
    assert metrics["context_length"] == 8000
    assert metrics["context_percent"] == 25.0
    assert metrics["context_resolution"]["evidence"] == "known_table"
    assert metrics["context_resolution"]["mismatch"] is False


# ---------------------------------------------------------------------------
# Provider probe
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_provider_advertised_window_uses_turn_credentials(monkeypatch):
    network = _install(monkeypatch, _FakeNetwork({"/models": _catalog("acme-model", max_model_len=8192)}))
    resolution = await resolve_effective_context(REMOTE, "acme-model", headers=AUTH)
    assert (resolution.effective, resolution.evidence, resolution.source) == (
        8192, ContextEvidence.PROVIDER_ADVERTISED, "models_catalog",
    )
    assert resolution.provider_io and not resolution.cached
    [(kind, url, headers)] = network.calls
    assert kind == "get" and url.endswith("/models")
    assert headers == {"Authorization": "Bearer secret-token"}


@pytest.mark.asyncio
async def test_credentials_are_not_forwarded_to_another_origin(monkeypatch):
    network = _install(monkeypatch, _FakeNetwork({"/models": _catalog("acme-model", max_model_len=8192)}))
    monkeypatch.setattr(
        "src.endpoint_resolver.build_models_url", lambda base: "http://catalog.elsewhere.test/v1/models",
    )
    await resolve_effective_context(REMOTE, "acme-model", headers=AUTH)
    assert network.calls[0][2] == {}


@pytest.mark.asyncio
async def test_credentials_follow_the_resolved_provider_host(monkeypatch):
    network = _install(monkeypatch, _FakeNetwork({"/models": _catalog("acme-model", max_model_len=8192)}))
    monkeypatch.setattr(
        "src.endpoint_resolver.build_models_url", lambda base: "http://100.64.0.9/v1/models",
    )
    monkeypatch.setattr(
        "src.endpoint_resolver.resolve_url", lambda url: url.replace("provider.test", "100.64.0.9"),
    )
    await resolve_effective_context(REMOTE, "acme-model", headers=AUTH)
    assert network.calls[0][2] == {"Authorization": "Bearer secret-token"}


@pytest.mark.asyncio
async def test_slow_url_resolution_is_bounded_by_the_probe_deadline(monkeypatch):
    import time as _time

    _install(monkeypatch, _FakeNetwork())

    def slow_models_url(base):
        _time.sleep(0.5)
        return base.split("/v1")[0] + "/v1/models"

    monkeypatch.setattr("src.endpoint_resolver.build_models_url", slow_models_url)
    loop = asyncio.get_running_loop()
    started = loop.time()
    resolution = await resolve_effective_context(REMOTE, "gpt-4o", deadline_seconds=0.05)
    assert loop.time() - started < 0.4
    assert resolution.probe_errors == ("deadline_exceeded",)
    assert resolution.evidence is ContextEvidence.KNOWN_TABLE


@pytest.mark.asyncio
async def test_known_table_fallback_when_provider_lists_no_window(monkeypatch):
    _install(monkeypatch, _FakeNetwork({"/models": _catalog("gpt-4o-mini")}))
    resolution = await resolve_effective_context(REMOTE, "gpt-4o-mini", headers=AUTH)
    assert (resolution.effective, resolution.evidence) == (128000, ContextEvidence.KNOWN_TABLE)
    assert "models:no_window_listed" in resolution.probe_errors


@pytest.mark.asyncio
async def test_unavailable_metadata_and_unknown_model_is_unknown(monkeypatch):
    _install(monkeypatch, _FakeNetwork({"/models": _Response(503, None)}))
    resolution = await resolve_effective_context(REMOTE, "mystery-model", headers=AUTH)
    assert resolution.evidence is ContextEvidence.UNKNOWN
    assert resolution.effective == 0
    assert resolution.probe_errors == ("models:http_503",)


@pytest.mark.asyncio
async def test_rejected_credentials_are_reported_without_leaking_them(monkeypatch):
    _install(monkeypatch, _FakeNetwork({"/models": _Response(401, None)}))
    resolution = await resolve_effective_context(REMOTE, "gpt-4o", headers=AUTH)
    assert resolution.evidence is ContextEvidence.KNOWN_TABLE
    assert resolution.probe_errors == ("models:http_401",)
    assert "secret-token" not in json.dumps(resolution.to_dict())


@pytest.mark.asyncio
async def test_provider_timeout_is_bounded_and_falls_back(monkeypatch):
    _install(monkeypatch, _FakeNetwork(get_delay=5.0))
    loop = asyncio.get_running_loop()
    started = loop.time()
    resolution = await resolve_effective_context(
        REMOTE, "gpt-4o", headers=AUTH, deadline_seconds=0.05,
    )
    assert loop.time() - started < 1.0
    assert "deadline_exceeded" in resolution.probe_errors
    assert (resolution.effective, resolution.evidence) == (128000, ContextEvidence.KNOWN_TABLE)


@pytest.mark.asyncio
async def test_provider_transport_failure_never_raises(monkeypatch):
    _install(monkeypatch, _FakeNetwork(get_error=httpx.ConnectError("refused")))
    resolution = await resolve_effective_context(
        REMOTE, "mystery-model", client_runtime_context={"model_context_window": 16384},
    )
    assert resolution.probe_errors == ("models:transport_error",)
    assert (resolution.effective, resolution.evidence) == (16384, ContextEvidence.OPERATOR_DECLARED)


@pytest.mark.asyncio
async def test_local_runtime_confirmed_slots_and_provider_mismatch(monkeypatch):
    network = _install(monkeypatch, _FakeNetwork({
        "/slots": _Response(200, [{"n_ctx": 16384}]),
        "/models": _catalog("local-model", max_model_len=32768),
    }))
    resolution = await resolve_effective_context(LOCAL, "local-model")
    assert (resolution.effective, resolution.evidence, resolution.source) == (
        16384, ContextEvidence.RUNTIME_CONFIRMED, "llamacpp_slots",
    )
    assert resolution.mismatch
    assert [url.rsplit("/", 1)[-1] for _, url, _ in network.calls] == ["slots", "models"]


@pytest.mark.asyncio
async def test_local_runtime_confirmed_props_when_slots_disabled(monkeypatch):
    _install(monkeypatch, _FakeNetwork({
        "/slots": _Response(501, None),
        "/props": _Response(200, {"default_generation_settings": {"n_ctx": 8192}}),
        "/models": _catalog("local-model"),
    }))
    resolution = await resolve_effective_context(LOCAL, "local-model")
    assert (resolution.effective, resolution.source) == (8192, "llamacpp_props")
    assert not resolution.mismatch


@pytest.mark.asyncio
async def test_remote_resolution_is_cached_per_credentials(monkeypatch):
    network = _install(monkeypatch, _FakeNetwork({"/models": _catalog("acme-model", max_model_len=8192)}))
    first = await resolve_effective_context(REMOTE, "acme-model", headers=AUTH)
    second = await resolve_effective_context(REMOTE, "acme-model", headers=AUTH)
    assert first.effective == second.effective == 8192
    assert second.cached and not second.provider_io
    assert len(network.calls) == 1
    await resolve_effective_context(REMOTE, "acme-model", headers={"Authorization": "Bearer other"})
    assert len(network.calls) == 2


@pytest.mark.asyncio
async def test_failed_remote_probe_expires_sooner(monkeypatch):
    network = _install(monkeypatch, _FakeNetwork({"/models": _Response(503, None)}))
    now = [1000.0]
    await resolve_effective_context(REMOTE, "acme-model", clock=lambda: now[0])
    await resolve_effective_context(REMOTE, "acme-model", clock=lambda: now[0])
    assert len(network.calls) == 1
    now[0] += cr.PROBE_FAILURE_TTL_SECONDS + 1
    await resolve_effective_context(REMOTE, "acme-model", clock=lambda: now[0])
    assert len(network.calls) == 2


@pytest.mark.asyncio
async def test_local_resolution_is_not_cached(monkeypatch):
    network = _install(monkeypatch, _FakeNetwork({"/slots": _Response(200, [{"n_ctx": 4096}])}))
    await resolve_effective_context(LOCAL, "local-model")
    await resolve_effective_context(LOCAL, "local-model")
    assert sum(1 for kind, url, _ in network.calls if url.endswith("/slots")) == 2


# ---------------------------------------------------------------------------
# Compact runtime integration
# ---------------------------------------------------------------------------

async def _run_preview(messages=None, **kwargs):
    contract = resolve_full_inventory_contract(schemas=[], policy=ToolPolicy())
    raw = [chunk async for chunk in stream_preview(
        endpoint_url=kwargs.pop("endpoint_url", REMOTE), model=kwargs.pop("model", "acme-model"),
        messages=messages or [{"role": "user", "content": "hi"}],
        headers=kwargs.pop("headers", AUTH),
        turn_contract=contract, session_id="test", owner="test",
        disabled_tools=set(), tool_policy=ToolPolicy(), **kwargs,
    )]
    assert raw[-1] == "data: [DONE]\n\n"
    events = [json.loads(chunk[6:]) for chunk in raw if chunk.startswith("data: {")]
    return next(event["data"] for event in events if event.get("type") == "metrics")


@pytest.mark.asyncio
async def test_compact_turn_resolves_once_before_model_and_metrics_do_no_discovery(monkeypatch):
    network = _install(monkeypatch, _FakeNetwork({"/models": _catalog("acme-model", max_model_len=8192)}))
    metrics = await _run_preview()
    kinds = [kind for kind, _, _ in network.calls]
    first_stream = kinds.index("stream")
    # All discovery precedes the first model request; nothing after it,
    # in particular nothing between the last model byte and [DONE].
    assert kinds[:first_stream] == ["get"]
    assert kinds[first_stream:] == ["stream"]
    assert metrics["context_length"] == 8192
    assert metrics["context_percent"] == 25.0
    assert metrics["context_resolution"]["evidence"] == "provider_advertised"
    assert metrics["context_resolution"]["provider_io"] is True


@pytest.mark.asyncio
async def test_compact_turn_with_supplied_resolution_performs_no_metadata_io(monkeypatch):
    network = _install(monkeypatch, _FakeNetwork())
    supplied = combine_observations([_obs(ContextEvidence.OPERATOR_DECLARED, 4096, "client_runtime_context")])
    metrics = await _run_preview(context_resolution=supplied)
    assert [kind for kind, _, _ in network.calls] == ["stream"]
    assert metrics["context_length"] == 4096
    assert metrics["context_resolution"] == supplied.to_dict()


@pytest.mark.asyncio
async def test_compact_metrics_report_unknown_window_as_zero(monkeypatch):
    _install(monkeypatch, _FakeNetwork({"/models": _Response(404, None)}))
    metrics = await _run_preview(model="mystery-model")
    assert metrics["context_length"] == 0
    assert metrics["context_percent"] == 0
    assert metrics["context_resolution"]["evidence"] == "unknown"
    assert metrics["context_resolution"]["probe_errors"] == ["models:http_404"]


@pytest.mark.asyncio
async def test_compact_runtime_budgets_against_resolved_window(monkeypatch):
    turns = []
    for index in range(6):
        turns.append({"role": "user", "content": f"question {index} " + "word " * 280})
        turns.append({"role": "assistant", "content": f"answer {index} " + "word " * 280})
    session = SimpleNamespace(history=turns)

    unbudgeted = _install(monkeypatch, _FakeNetwork())
    await _run_preview(history_session=session, context_resolution=UNRESOLVED_CONTEXT)
    budgeted = _install(monkeypatch, _FakeNetwork())
    resolved = combine_observations([_obs(ContextEvidence.PROVIDER_ADVERTISED, 4096, "models_catalog")])
    await _run_preview(history_session=session, context_resolution=resolved)

    full = unbudgeted.requests[0]["messages"]
    trimmed = budgeted.requests[0]["messages"]
    # An unknown window keeps the previous reactive-only behavior.
    assert len(full) > len(trimmed)
    assert model_context.estimate_tokens(trimmed) < 4096
    assert trimmed[-1]["content"] == "hi"


@pytest.mark.asyncio
async def test_compact_metrics_fold_provider_stated_limit_without_new_discovery(monkeypatch):
    rejection = (
        "This model's maximum context length is 4096 tokens. However, you requested "
        "5000 tokens (4800 in the messages, 200 in the completion)."
    )
    network = _install(monkeypatch, _FakeNetwork(
        {"/models": _catalog("acme-model", max_model_len=8192)},
        stream_responses=[
            _ModelStream([], status=400, text=json.dumps({"error": {"message": rejection}})),
            _ModelStream(_answer_lines({"prompt_tokens": 1024, "completion_tokens": 3})),
        ],
    ))
    metrics = await _run_preview()
    assert [kind for kind, _, _ in network.calls] == ["get", "stream", "stream"]
    resolution = metrics["context_resolution"]
    assert metrics["context_length"] == 4096
    assert (resolution["evidence"], resolution["source"]) == ("runtime_confirmed", "provider_rejection")
    assert resolution["mismatch"] is True


@pytest.mark.asyncio
async def test_compact_turn_proceeds_when_metadata_probe_times_out(monkeypatch):
    _install(monkeypatch, _FakeNetwork(get_error=httpx.ReadTimeout("slow catalog")))
    metrics = await _run_preview(
        model="gpt-4o", client_runtime_context={"model_context_window": 65536},
    )
    resolution = metrics["context_resolution"]
    assert resolution["probe_errors"] == ["models:timeout"]
    assert metrics["context_length"] == 65536
    assert resolution["evidence"] == "operator_declared"


# ---------------------------------------------------------------------------
# Credential scoping (adversarial)
# ---------------------------------------------------------------------------

def _models_at(monkeypatch, models_url, resolved=None):
    monkeypatch.setattr("src.endpoint_resolver.build_models_url", lambda base: models_url)
    monkeypatch.setattr(
        "src.endpoint_resolver.resolve_url", lambda url: url if resolved is None else resolved,
    )


async def _forwarded_headers(monkeypatch, endpoint_url, models_url, resolved=None):
    network = _install(monkeypatch, _FakeNetwork({"/models": _catalog("acme-model", max_model_len=8192)}))
    _models_at(monkeypatch, models_url, resolved)
    await resolve_effective_context(endpoint_url, "acme-model", headers=AUTH)
    [(kind, url, headers)] = network.calls
    assert url == models_url
    return headers


@pytest.mark.asyncio
@pytest.mark.parametrize("models_url", [
    "https://provider.test/v1/models",
    "https://PROVIDER.test:443/v1/models",
])
async def test_credentials_reach_the_configured_provider_origin(monkeypatch, models_url):
    headers = await _forwarded_headers(
        monkeypatch, "https://provider.test/v1/chat/completions", models_url,
    )
    assert headers == {"Authorization": "Bearer secret-token"}


@pytest.mark.asyncio
async def test_credentials_reach_only_the_server_resolved_form_of_the_provider(monkeypatch):
    endpoint = "http://gpu-box:8000/v1/chat/completions"
    resolved = "http://100.64.0.9:8000/v1/chat/completions"
    assert await _forwarded_headers(
        monkeypatch, endpoint, "http://100.64.0.9:8000/v1/models", resolved,
    ) == {"Authorization": "Bearer secret-token"}
    cr.clear_probe_cache()
    # The same address is not trusted when the server resolver did not
    # produce it for this endpoint.
    assert await _forwarded_headers(
        monkeypatch, endpoint, "http://100.64.0.9:8000/v1/models",
    ) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("models_url", [
    "http://provider.test/v1/models",               # scheme downgrade
    "https://provider.test:8443/v1/models",         # other port
    "https://provider.test.evil.example/v1/models", # lookalike host
    "https://evil.example/provider.test/v1/models", # host in path
    "https://provider.test@evil.example/v1/models", # host in userinfo
    "/v1/models",                                   # no origin at all
])
async def test_unrelated_models_url_receives_no_credentials(monkeypatch, models_url):
    assert await _forwarded_headers(
        monkeypatch, "https://provider.test/v1/chat/completions", models_url,
    ) == {}


@pytest.mark.asyncio
async def test_probe_client_never_follows_redirects(monkeypatch):
    network = _install(monkeypatch, _FakeNetwork({"/models": _Response(302, None)}))
    resolution = await resolve_effective_context(REMOTE, "acme-model", headers=AUTH)
    assert network.client_kwargs and all(
        kwargs.get("follow_redirects") is False for kwargs in network.client_kwargs
    )
    assert resolution.probe_errors == ("models:http_302",)
    assert len(network.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    _FakeNetwork(get_error=httpx.ConnectError(
        "connect to https://user:pw-secret@provider.test/v1/models?api_key=query-secret failed")),
    _FakeNetwork(get_error=httpx.ReadTimeout("Bearer secret-token timed out")),
    _FakeNetwork({"/models": _Response(401, None)}),
    _FakeNetwork({"/models": _Response(200, ValueError("api_key=query-secret"))}),
    _FakeNetwork(get_error=RuntimeError("Authorization: Bearer secret-token")),
])
async def test_probe_errors_never_expose_credentials_or_urls(monkeypatch, caplog, failure):
    import logging

    caplog.set_level(logging.DEBUG)
    endpoint = "https://user:pw-secret@provider.test/v1/chat/completions?api_key=query-secret"
    _install(monkeypatch, failure)
    resolution = await resolve_effective_context(endpoint, "acme-model", headers=AUTH)
    assert resolution.probe_errors
    exposed = json.dumps(resolution.to_dict()) + caplog.text + json.dumps(
        context_metrics(resolution, 10),
    )
    for secret in ("secret-token", "pw-secret", "query-secret", "provider.test", "Authorization"):
        assert secret not in exposed


@pytest.mark.asyncio
async def test_bound_endpoint_url_stays_out_of_repr_and_metrics(monkeypatch):
    _install(monkeypatch, _FakeNetwork({"/models": _Response(503, None)}))
    endpoint = "https://user:pw-secret@provider.test/v1/chat/completions?api_key=query-secret"
    resolution = await resolve_effective_context(endpoint, "acme-model", headers=AUTH)
    assert resolution.applies_to(endpoint, "acme-model")
    for rendered in (repr(resolution), str(resolution), json.dumps(resolution.to_dict())):
        assert "pw-secret" not in rendered and "query-secret" not in rendered
