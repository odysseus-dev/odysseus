"""Model-serving safety policy (src/serve_guard.py).

Pins the logic that stops an agent loop / accidental double-tap from stacking
models until the GPU OOMs: the loaded-model cap + stop-previous decision, the
running-serve enumeration, and the best-effort VRAM pre-flight.
"""

import pytest

from src import serve_guard as g


# ── live_serves ──────────────────────────────────────────────────────────────

def _state(*tasks):
    return {"tasks": list(tasks)}


def test_live_serves_filters_type_status_and_host():
    state = _state(
        {"type": "serve", "status": "running", "sessionId": "a", "remoteHost": ""},
        {"type": "serve", "status": "loading", "sessionId": "a2", "remoteHost": ""},
        {"type": "serve", "status": "stopped", "sessionId": "b", "remoteHost": ""},   # terminal
        {"type": "serve", "status": "error", "sessionId": "e", "remoteHost": ""},     # terminal
        {"type": "serve", "status": "running", "sessionId": "c", "remoteHost": "gpu-box"},
        {"type": "download", "status": "running", "sessionId": "d", "remoteHost": ""}, # not a serve
    )
    assert [t["sessionId"] for t in g.live_serves(state, "")] == ["a", "a2"]
    assert [t["sessionId"] for t in g.live_serves(state, "gpu-box")] == ["c"]
    assert g.live_serves(state, "other") == []


def test_live_serves_empty_and_malformed():
    assert g.live_serves({}, "") == []
    assert g.live_serves({"tasks": ["nope", None, 3]}, "") == []


# ── decide_serve ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("running,cap,replace,expected", [
    (0, 1, True,  "proceed"),
    (1, 1, True,  "stop_previous"),
    (1, 1, False, "refuse"),
    (1, 2, True,  "proceed"),
    (2, 2, True,  "refuse"),
    (0, 3, False, "proceed"),
])
def test_decide_serve(running, cap, replace, expected):
    assert g.decide_serve(running, cap, replace)[0] == expected


def test_decide_serve_coerces_bad_cap_to_one():
    # A cap of 0/None must not mean "unlimited" — it floors to 1 (fail safe).
    assert g.decide_serve(1, 0, True)[0] == "stop_previous"
    assert g.decide_serve(1, None, False)[0] == "refuse"


# ── estimate_model_vram_gb ───────────────────────────────────────────────────

def test_estimate_dense_bf16():
    # 7B * 2 bytes * 1.25 overhead = 17.5
    assert g.estimate_model_vram_gb("Qwen2.5-7B-Instruct", "vllm serve Qwen2.5-7B-Instruct") == 17.5


def test_estimate_quantized():
    # 32B * 0.55 (q4/awq) * 1.25 = 22.0
    assert g.estimate_model_vram_gb("Qwen-32B-AWQ", "") == 22.0


def test_estimate_moe_uses_total_params():
    # 8x7B = 56B total
    assert g.estimate_model_vram_gb("Mixtral-8x7B-Instruct", "") == 140.0


def test_estimate_none_when_no_size_token():
    assert g.estimate_model_vram_gb("my-custom-model", "") is None
    assert g.estimate_model_vram_gb("", "") is None


def test_estimate_rejects_absurd_size():
    assert g.estimate_model_vram_gb("Fake-9000B", "") is None


# ── parse_free_vram_gb ───────────────────────────────────────────────────────

def test_parse_free_vram_sums_gpus():
    assert g.parse_free_vram_gb("8192\n8192") == 16.0          # two GPUs
    assert g.parse_free_vram_gb("24576") == 24.0               # one GPU
    assert g.parse_free_vram_gb("") is None
    assert g.parse_free_vram_gb("no GPUs found") is None


# ── vram_verdict ─────────────────────────────────────────────────────────────

def test_vram_verdict():
    assert g.vram_verdict(24.0, 17.5, 2)[0] == "ok"
    assert g.vram_verdict(18.0, 17.5, 2)[0] == "refuse"   # 17.5+2 = 19.5 > 18
    assert g.vram_verdict(8.0, 17.5, 2)[0] == "refuse"
    # Can't measure / estimate → skip (never block on missing data).
    assert g.vram_verdict(None, 17.5, 2)[0] == "skip"
    assert g.vram_verdict(24.0, None, 2)[0] == "skip"


def test_vram_refuse_message_is_actionable():
    _, msg = g.vram_verdict(8.0, 17.5, 2)
    assert "free" in msg.lower() and "headroom" in msg.lower()
