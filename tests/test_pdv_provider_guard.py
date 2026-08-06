import httpx
import pytest
from contextlib import asynccontextmanager

from src import pdv_provider_guard


def _configure(tmp_path, monkeypatch):
    key = tmp_path / "adapter.key"
    key.write_text("a" * 64, encoding="ascii")
    monkeypatch.setenv("PDV_PROVIDER_GUARD_REQUIRED", "true")
    monkeypatch.setenv("PDV_EXECUTION_OS_URL", "http://127.0.0.1:4173")
    monkeypatch.setenv("ODYSSEUS_PDV_ADAPTER_KEY_FILE", str(key))


def _set_ranking(endpoint, model):
    pdv_provider_guard._last_routing.set({
        "routing_receipt_id": "ody_provider_routing_" + "1" * 36,
        "task_class": "chat",
        "ordered_candidates": [{"candidate_index": 0, "endpoint": endpoint, "model": model, "timeout_ms": 30000, "retry_limit": 1}],
    })


def test_provider_guard_is_inert_for_standalone_upstream(monkeypatch):
    monkeypatch.delenv("PDV_PROVIDER_GUARD_REQUIRED", raising=False)
    assert pdv_provider_guard.authorize_provider_sync("https://example.invalid/v1", "model") is None


def test_provider_guard_accepts_only_correlated_authorization(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    endpoint = "http://127.0.0.1:11435/v1/chat/completions"
    _set_ranking(endpoint, "model-1")
    def authorize(*_args, **kwargs):
        request_id = kwargs["json"]["provider_request_id"]
        return httpx.Response(200, json={"allowed": True, "selected_model": "model-1", "selected_endpoint": endpoint, "provider_request_id": request_id, "routing_receipt_id": kwargs["json"]["routing_receipt_id"], "candidate_index": kwargs["json"]["candidate_index"], "authorization_receipt_id": "receipt-1"})
    monkeypatch.setattr(pdv_provider_guard.httpx, "post", authorize)
    receipt = pdv_provider_guard.authorize_provider_sync(endpoint, "model-1")
    assert receipt["allowed"] is True
    assert pdv_provider_guard.get_last_authorization_receipt() == receipt


def test_direct_authorization_first_obtains_a_bound_ranking_receipt(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    pdv_provider_guard._last_routing.set(None)
    endpoint = "http://127.0.0.1:11435/v1/chat/completions"
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        if url.endswith("/provider/rank"):
            return httpx.Response(200, json={
                "allowed": True, "routing_receipt_id": "ody_provider_routing_" + "2" * 36, "task_class": "chat",
                "selected_provider": "llama.cpp", "selected_endpoint": endpoint, "selected_model": "model-1",
                "ordered_candidates": [{"candidate_index": 0, "endpoint": endpoint, "model": "model-1", "timeout_ms": 30000, "retry_limit": 0}],
            })
        payload = kwargs["json"]
        return httpx.Response(200, json={
            "allowed": True, "selected_provider": "llama.cpp", "selected_model": "model-1", "selected_endpoint": endpoint,
            "provider_request_id": payload["provider_request_id"], "routing_receipt_id": payload["routing_receipt_id"],
            "candidate_index": payload["candidate_index"], "authorization_receipt_id": "receipt-1",
        })

    monkeypatch.setattr(pdv_provider_guard.httpx, "post", post)
    receipt = pdv_provider_guard.authorize_provider_sync(endpoint, "model-1")
    assert calls[0].endswith("/provider/rank") and calls[1].endswith("/provider/authorize")
    assert receipt["routing_receipt_id"].startswith("ody_provider_routing_")


def test_provider_guard_fails_closed_on_denial_or_mismatch(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    endpoint = "http://127.0.0.1:11435/v1/chat/completions"
    _set_ranking(endpoint, "model-1")
    monkeypatch.setattr(pdv_provider_guard.httpx, "post", lambda *_args, **_kwargs: httpx.Response(403, json={"allowed": False, "reason_code": "LOCAL_CAPACITY_NOT_AUTHORIZED"}))
    with pytest.raises(RuntimeError, match="LOCAL_CAPACITY_NOT_AUTHORIZED"):
        pdv_provider_guard.authorize_provider_sync(endpoint, "model-1")
    assert pdv_provider_guard.get_last_authorization_receipt() is None
    monkeypatch.setattr(pdv_provider_guard.httpx, "post", lambda *_args, **kwargs: httpx.Response(200, json={"allowed": True, "selected_model": "other", "selected_endpoint": endpoint, "provider_request_id": kwargs["json"]["provider_request_id"], "routing_receipt_id": kwargs["json"]["routing_receipt_id"], "candidate_index": kwargs["json"]["candidate_index"], "authorization_receipt_id": "receipt-1"}))
    with pytest.raises(RuntimeError, match="correlation mismatch"):
        pdv_provider_guard.authorize_provider_sync(endpoint, "model-1")


def test_provider_ranking_reorders_without_serializing_credentials(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    candidates = [
        ("https://paid.example/v1/chat/completions", "paid-model", {"Authorization": "Bearer secret"}),
        ("http://127.0.0.1:11435/v1/chat/completions", "local-model", {"X-Local-Key": "secret"}),
    ]
    captured = {}

    def rank(*_args, **kwargs):
        captured.update(kwargs["json"])
        return httpx.Response(200, json={
            "allowed": True,
            "reason_code": "AUTHORIZED",
            "routing_receipt_id": "ody_provider_routing_1",
            "task_class": "chat",
            "selected_endpoint": candidates[1][0],
            "selected_model": candidates[1][1],
            "ordered_candidates": [
                {"candidate_index": 1, "endpoint": candidates[1][0], "model": candidates[1][1], "timeout_ms": 30000, "retry_limit": 1},
                {"candidate_index": 0, "endpoint": candidates[0][0], "model": candidates[0][1], "timeout_ms": 60000, "retry_limit": 0},
            ],
        })

    monkeypatch.setattr(pdv_provider_guard.httpx, "post", rank)
    assert pdv_provider_guard.rank_provider_candidates_sync(candidates) == [candidates[1], candidates[0]]
    assert captured == {"task_class": "chat", "candidates": [{"endpoint": item[0], "model": item[1]} for item in candidates]}
    assert "secret" not in repr(captured)
    assert pdv_provider_guard.get_ranked_route_policy(candidates[1][0], candidates[1][1])["retry_limit"] == 1


def test_provider_ranking_fails_closed_on_malformed_order(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    candidate = ("http://127.0.0.1:11435/v1/chat/completions", "local-model", {})
    monkeypatch.setattr(pdv_provider_guard.httpx, "post", lambda *_args, **_kwargs: httpx.Response(200, json={
        "allowed": True, "routing_receipt_id": "receipt", "task_class": "chat",
        "selected_endpoint": candidate[0], "selected_model": candidate[1],
        "ordered_candidates": [{"candidate_index": 7, "endpoint": candidate[0], "model": candidate[1], "timeout_ms": 30000, "retry_limit": 1}],
    }))
    with pytest.raises(RuntimeError, match="correlation mismatch"):
        pdv_provider_guard.rank_provider_candidates_sync([candidate])


@pytest.mark.asyncio
async def test_async_provider_ranking_preserves_opaque_candidate_headers(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    candidate = ("http://127.0.0.1:11435/v1/chat/completions", "local-model", {"Authorization": "secret"})

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, _url, **kwargs):
            assert kwargs["json"]["candidates"] == [{"endpoint": candidate[0], "model": candidate[1]}]
            assert "secret" not in repr(kwargs["json"])
            return httpx.Response(200, json={
                "allowed": True, "routing_receipt_id": "receipt", "task_class": "chat",
                "selected_endpoint": candidate[0], "selected_model": candidate[1],
                "ordered_candidates": [{"candidate_index": 0, "endpoint": candidate[0], "model": candidate[1], "timeout_ms": 30000, "retry_limit": 1}],
            })

    monkeypatch.setattr(pdv_provider_guard.httpx, "AsyncClient", Client)
    assert await pdv_provider_guard.rank_provider_candidates([candidate]) == [candidate]


def test_provider_outcome_requires_exact_durable_receipt_correlation(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    authorization = {"authorization_receipt_id": "ody_provider_auth_" + "1" * 36, "provider_request_id": "2" * 36}

    def record(*_args, **kwargs):
        payload = kwargs["json"]
        return httpx.Response(201, json={
            "outcome_receipt_id": "ody_provider_outcome_" + "3" * 36,
            "authorization_receipt_id": payload["authorization_receipt_id"],
            "provider_request_id": payload["provider_request_id"],
            "outcome": payload["outcome"],
        })

    monkeypatch.setattr(pdv_provider_guard.httpx, "post", record)
    receipt = pdv_provider_guard.record_provider_outcome_sync(authorization, "timed_out", 60000, cost_microusd=0)
    assert receipt["provider_request_id"] == authorization["provider_request_id"]


def test_bound_dispatch_receives_exact_provider_outcome_correlation(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    endpoint = "http://127.0.0.1:11435/v1/chat/completions"
    _set_ranking(endpoint, "model-1")
    monkeypatch.setenv("PDV_EXECUTION_OS_DISPATCH_ID", "ody_dispatch_" + "4" * 36)
    transitions = []

    def post(url, **kwargs):
        payload = kwargs["json"]
        if url.endswith("/provider/authorize"):
            return httpx.Response(200, json={
                "allowed": True, "selected_provider": "llama.cpp", "selected_model": "model-1", "selected_endpoint": endpoint,
                "provider_request_id": payload["provider_request_id"], "routing_receipt_id": payload["routing_receipt_id"],
                "candidate_index": payload["candidate_index"], "authorization_receipt_id": "ody_provider_auth_" + "5" * 36,
            })
        if url.endswith("/provider/outcome"):
            return httpx.Response(201, json={
                "outcome_receipt_id": "ody_provider_outcome_" + "6" * 36,
                "authorization_receipt_id": payload["authorization_receipt_id"], "provider_request_id": payload["provider_request_id"], "outcome": payload["outcome"],
            })
        transitions.append(payload)
        return httpx.Response(200, json={"state": payload["state"]})

    monkeypatch.setattr(pdv_provider_guard.httpx, "post", post)
    authorization = pdv_provider_guard.authorize_provider_sync(endpoint, "model-1")
    outcome = pdv_provider_guard.record_provider_outcome_sync(authorization, "failed", 10)
    assert transitions == [
        {"state": "running", "provider_request_id": authorization["provider_request_id"], "final_receipt_id": None},
        {"state": "failed", "provider_request_id": authorization["provider_request_id"], "final_receipt_id": outcome["outcome_receipt_id"]},
    ]


def test_normal_sync_llm_call_records_provider_outcome_and_usage(monkeypatch):
    import src.llm_core as llm_core

    authorization = {"authorization_receipt_id": "auth", "provider_request_id": "request"}
    outcomes = []
    monkeypatch.setattr(pdv_provider_guard, "authorize_provider_sync", lambda _endpoint, _model: authorization)
    monkeypatch.setattr(pdv_provider_guard, "record_provider_outcome_sync", lambda auth, outcome, duration, **telemetry: outcomes.append((auth, outcome, telemetry)))
    monkeypatch.setattr(llm_core, "httpx_post_kimi_aware", lambda *_args, **_kwargs: httpx.Response(200, json={"choices": [{"message": {"content": "synthetic result"}}], "usage": {"prompt_tokens": 3, "completion_tokens": 2}}))
    result = llm_core.llm_call("https://provider.example.invalid/v1", "synthetic-model", [{"role": "user", "content": "synthetic"}], max_tokens=4)
    assert result == "synthetic result"
    assert outcomes == [(authorization, "completed", {"input_tokens": 3, "output_tokens": 2})]


def test_normal_async_and_stream_paths_record_provider_outcomes():
    from pathlib import Path
    source = (Path(__file__).parents[1] / "src" / "llm_core.py").read_text(encoding="utf-8")
    assert source.count("record_provider_outcome(") >= 7
    assert "record_provider_outcome_sync(authorization, \"completed\"" in source


@pytest.mark.asyncio
async def test_stream_error_event_records_failed_outcome(monkeypatch):
    import src.llm_core as llm_core

    outcomes = []

    async def authorize(_endpoint, _model):
        return {"authorization_receipt_id": "auth", "provider_request_id": "request"}

    async def record(_authorization, outcome, _duration, **_telemetry):
        outcomes.append(outcome)

    async def inner(*_args, **_kwargs):
        yield 'event: error\ndata: {"status": 502, "text": "synthetic"}\n\n'

    @asynccontextmanager
    async def slot(*_args, **_kwargs):
        yield

    monkeypatch.setattr(pdv_provider_guard, "authorize_provider", authorize)
    monkeypatch.setattr(pdv_provider_guard, "record_provider_outcome", record)
    monkeypatch.setattr(llm_core, "_stream_llm_inner", inner)
    monkeypatch.setattr(llm_core, "_local_model_slot", slot)
    chunks = [chunk async for chunk in llm_core.stream_llm("https://provider.example/v1", "model", [{"role": "user", "content": "hi"}])]
    assert chunks[0].startswith("event: error")
    assert outcomes == ["failed"]


def test_every_direct_model_post_path_has_provider_preflight():
    from pathlib import Path
    root = Path(__file__).parents[1]
    assert "authorize_provider_sync(target_url, model_id)" in (root / "routes" / "model_routes.py").read_text(encoding="utf-8")
    image_source = (root / "src" / "ai_interaction.py").read_text(encoding="utf-8")
    for call in ("authorize_provider(images_url, model_id)", "authorize_provider(edits_url, model_id)", "authorize_provider(harmonize_url, model_id)"):
        assert call in image_source
    gallery_source = (root / "routes" / "gallery" / "gallery_routes.py").read_text(encoding="utf-8")
    assert gallery_source.count("authorize_provider(") >= 7
    assert "authorize_provider_sync(url, model or \"embedding-endpoint\")" in (root / "routes" / "embedding_routes.py").read_text(encoding="utf-8")
    assert "authorize_provider_sync(self.url, self.model)" in (root / "src" / "embeddings.py").read_text(encoding="utf-8")
    assert "await authorize_provider(images_url, model_id)" in (root / "mcp_servers" / "image_gen_server.py").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_builtin_image_mcp_denial_prevents_provider_post(monkeypatch):
    import mcp_servers.image_gen_server as image_server
    import src.ai_interaction as ai_interaction
    import src.settings as settings

    monkeypatch.setattr(settings, "get_setting", lambda name, default=None: default)
    monkeypatch.setattr(settings, "load_settings", lambda: {"image_model": "gpt-image-1"})
    monkeypatch.setattr(ai_interaction, "_resolve_model", lambda *_args, **_kwargs: ("https://images.example/v1/chat/completions", "gpt-image-1", {}))
    called = []

    async def denied(endpoint, model):
        called.append((endpoint, model))
        raise RuntimeError("synthetic policy denial")

    monkeypatch.setattr(pdv_provider_guard, "authorize_provider", denied)
    result = await image_server.call_tool("generate_image", {"prompt": "synthetic"})
    assert called == [("https://images.example/v1/images/generations", "gpt-image-1")]
    assert "synthetic policy denial" in result[0].text
