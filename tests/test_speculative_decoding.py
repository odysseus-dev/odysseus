import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.hwfit_routes import setup_hwfit_routes
from services.hwfit.speculative import (
    build_speculative_cli_flags,
    detect_model_family,
    evaluate_speculative_decoding,
    find_compatible_draft_models,
    validate_draft_compatibility,
)


CUDA_24GB = {
    "has_gpu": True,
    "gpu_name": "NVIDIA RTX 4090",
    "gpu_vram_gb": 24.0,
    "gpu_count": 1,
    "available_ram_gb": 64.0,
    "total_ram_gb": 64.0,
    "backend": "cuda",
    "platform": "linux",
}

SMALL_8GB = {
    "has_gpu": True,
    "gpu_name": "NVIDIA RTX 4060",
    "gpu_vram_gb": 8.0,
    "gpu_count": 1,
    "available_ram_gb": 32.0,
    "total_ram_gb": 32.0,
    "backend": "cuda",
    "platform": "linux",
}


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.is_success = 200 <= status_code < 300

    def json(self):
        return self._payload


def test_validate_draft_compatibility_checks_family_and_param_ratio():
    target = {"name": "Qwen/Qwen3-32B", "architecture": "qwen3", "parameter_count": "32B"}
    good_draft = {"name": "Qwen/Qwen3-1.7B", "architecture": "qwen3", "parameter_count": "1.7B"}
    wrong_family = {"name": "meta-llama/Llama-3.2-1B", "architecture": "llama", "parameter_count": "1B"}
    too_large = {"name": "Qwen/Qwen3-14B", "architecture": "qwen3", "parameter_count": "14B"}

    assert detect_model_family(target) == "qwen3"
    ok = validate_draft_compatibility(target, good_draft)
    assert ok["compatible"] is True
    assert ok["reasons"] == []

    bad_fam = validate_draft_compatibility(target, wrong_family)
    assert bad_fam["compatible"] is False
    assert any("mismatch" in r.lower() for r in bad_fam["reasons"])

    bad_size = validate_draft_compatibility(target, too_large)
    assert bad_size["compatible"] is False
    assert any("ratio" in r.lower() for r in bad_size["reasons"])


def test_find_compatible_draft_models_ranks_vram_fit_and_cli_flags():
    target = {"name": "Qwen/Qwen3-32B", "architecture": "qwen3", "parameter_count": "32B"}
    catalog = [
        target,
        {"name": "Qwen/Qwen3-0.6B", "architecture": "qwen3", "parameter_count": "0.6B"},
        {"name": "Qwen/Qwen3-4B", "architecture": "qwen3", "parameter_count": "4B"},
        {"name": "meta-llama/Llama-3.2-1B", "architecture": "llama", "parameter_count": "1B"},
    ]

    plan_24 = find_compatible_draft_models(target, CUDA_24GB, catalog=catalog, backend="llamacpp")
    assert plan_24["recommended"] is not None
    assert plan_24["recommended"]["fits_vram"] is True
    assert plan_24["recommended"]["time_to_success_rating"] == "fast"
    assert "--model-draft" in plan_24["recommended"]["cli"]["command"]

    vllm_flags = build_speculative_cli_flags("vllm", "Qwen/Qwen3-32B", "Qwen/Qwen3-0.6B", draft_tokens=5)
    assert "--speculative-model" in vllm_flags["args"]
    sglang_flags = build_speculative_cli_flags("sglang", "Qwen/Qwen3-32B", "Qwen/Qwen3-0.6B", draft_tokens=5)
    assert "--speculative-draft-model-path" in sglang_flags["args"]

    plan_8 = find_compatible_draft_models(target, SMALL_8GB, catalog=catalog, backend="llamacpp")
    assert plan_8["recommended"]["fits_vram"] is False
    assert plan_8["recommended"]["time_to_success_rating"] == "slower_than_baseline"


def test_evaluate_speculative_decoding_verifies_correctness_and_speedup(monkeypatch):
    def fake_post(url, json=None, headers=None, timeout=None):
        prompt = (json.get("messages") or [{}])[0].get("content", "")
        if "1591" in prompt:
            content = "37 * 43 = 1591\nFINAL: 1591"
        elif "JSON" in prompt:
            content = '{"status": "ok", "items": [1, 2, 3]}'
        else:
            content = "def fib(n: int) -> int:\n    return n if n < 2 else fib(n-1) + fib(n-2)"
        return _FakeResponse({
            "choices": [{"message": {"content": content}}],
            "usage": {"completion_tokens": 24},
        })

    monkeypatch.setattr("services.hwfit.speculative.httpx.post", fake_post)
    report = evaluate_speculative_decoding(
        speculative_base_url="http://localhost:8080/v1",
        model_id="Qwen/Qwen3-32B",
        baseline_base_url="http://localhost:8081/v1",
    )
    assert report["passed"] is True
    assert report["correctness_pass_rate"] == 1.0
    assert report["total_cases"] == 3
    assert report["effective_tps"] > 0


def test_hwfit_speculative_routes(monkeypatch):
    from core.middleware import require_admin

    app = FastAPI()
    app.include_router(setup_hwfit_routes())
    client = TestClient(app)

    # Admin gate enforced by default
    unauth = client.get("/api/hwfit/speculative", params={"model": "Qwen/Qwen3-14B"})
    assert unauth.status_code == 403

    app.dependency_overrides[require_admin] = lambda: {"role": "admin"}

    target = {"name": "Qwen/Qwen3-14B", "architecture": "qwen3", "parameter_count": "14B"}
    draft = {"name": "Qwen/Qwen3-1.7B", "architecture": "qwen3", "parameter_count": "1.7B"}
    monkeypatch.setattr("services.hwfit.hardware.detect_system", lambda **kw: CUDA_24GB)
    monkeypatch.setattr("services.hwfit.models.get_models", lambda: [target, draft])

    resp = client.get("/api/hwfit/speculative", params={"model": "Qwen/Qwen3-14B", "backend": "sglang"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["recommended"]["draft_model"] == "Qwen/Qwen3-1.7B"
    assert "--speculative-draft-model-path" in data["recommended"]["cli"]["command"]

    def fake_post(url, json=None, headers=None, timeout=None):
        return _FakeResponse({
            "choices": [{"message": {"content": "FINAL: 1591"}}],
            "usage": {"completion_tokens": 12},
        })

    monkeypatch.setattr("services.hwfit.speculative.httpx.post", fake_post)
    eval_resp = client.post(
        "/api/hwfit/speculative/evaluate",
        json={
            "speculative_base_url": "http://localhost:8080/v1",
            "model": "Qwen/Qwen3-14B",
            "prompts": [{"id": "arith", "prompt": "37*43", "expected_substring": "1591"}],
        },
    )
    assert eval_resp.status_code == 200
    assert eval_resp.json()["passed"] is True
