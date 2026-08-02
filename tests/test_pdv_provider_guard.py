import httpx
import pytest

from src import pdv_provider_guard


def _configure(tmp_path, monkeypatch):
    key = tmp_path / "adapter.key"
    key.write_text("a" * 64, encoding="ascii")
    monkeypatch.setenv("PDV_PROVIDER_GUARD_REQUIRED", "true")
    monkeypatch.setenv("PDV_EXECUTION_OS_URL", "http://127.0.0.1:4173")
    monkeypatch.setenv("ODYSSEUS_PDV_ADAPTER_KEY_FILE", str(key))


def test_provider_guard_is_inert_for_standalone_upstream(monkeypatch):
    monkeypatch.delenv("PDV_PROVIDER_GUARD_REQUIRED", raising=False)
    assert pdv_provider_guard.authorize_provider_sync("https://example.invalid/v1", "model") is None


def test_provider_guard_accepts_only_correlated_authorization(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    endpoint = "http://127.0.0.1:11435/v1/chat/completions"
    def authorize(*_args, **kwargs):
        request_id = kwargs["json"]["provider_request_id"]
        return httpx.Response(200, json={"allowed": True, "selected_model": "model-1", "selected_endpoint": endpoint, "provider_request_id": request_id, "authorization_receipt_id": "receipt-1"})
    monkeypatch.setattr(pdv_provider_guard.httpx, "post", authorize)
    receipt = pdv_provider_guard.authorize_provider_sync(endpoint, "model-1")
    assert receipt["allowed"] is True
    assert pdv_provider_guard.get_last_authorization_receipt() == receipt


def test_provider_guard_fails_closed_on_denial_or_mismatch(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    monkeypatch.setattr(pdv_provider_guard.httpx, "post", lambda *_args, **_kwargs: httpx.Response(403, json={"allowed": False, "reason_code": "LOCAL_CAPACITY_NOT_AUTHORIZED"}))
    with pytest.raises(RuntimeError, match="LOCAL_CAPACITY_NOT_AUTHORIZED"):
        pdv_provider_guard.authorize_provider_sync("http://127.0.0.1:11435/v1/chat/completions", "model-1")
    assert pdv_provider_guard.get_last_authorization_receipt() is None
    monkeypatch.setattr(pdv_provider_guard.httpx, "post", lambda *_args, **kwargs: httpx.Response(200, json={"allowed": True, "selected_model": "other", "selected_endpoint": "http://127.0.0.1:11435/v1/chat/completions", "provider_request_id": kwargs["json"]["provider_request_id"], "authorization_receipt_id": "receipt-1"}))
    with pytest.raises(RuntimeError, match="correlation mismatch"):
        pdv_provider_guard.authorize_provider_sync("http://127.0.0.1:11435/v1/chat/completions", "model-1")


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
