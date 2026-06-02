"""Server-side serve-output diagnosis (mirror of cookbook-diagnosis.js).

`_diagnose_serve_output` maps raw tmux serve output to a structured
{message, suggestions} dict so the agent/tool path can retry with an adjusted
command instead of re-parsing logs. These lock in the pattern table, the
traceback-suppression-when-recovered behaviour, the tail-only scan window, and
pattern precedence.
"""

from routes.cookbook_helpers import _diagnose_serve_output


def test_returns_none_for_empty():
    assert _diagnose_serve_output("") is None
    assert _diagnose_serve_output(None) is None


def test_returns_none_for_clean_output():
    healthy = "INFO: Application startup complete.\nINFO: Uvicorn running on http://0.0.0.0:8000"
    assert _diagnose_serve_output(healthy) is None


def test_cuda_oom_offers_context_and_mem_retries():
    diag = _diagnose_serve_output("...\ntorch.cuda.OutOfMemoryError: CUDA out of memory.\n")
    assert diag["message"] == "GPU ran out of memory during startup or warmup."
    flags = {s.get("flag") for s in diag["suggestions"]}
    assert "--max-model-len" in flags
    assert "--gpu-memory-utilization" in flags
    assert any(s.get("op") == "append" and s.get("arg") == "--enforce-eager" for s in diag["suggestions"])


def test_kv_cache_block_exhaustion():
    diag = _diagnose_serve_output("ERROR No available memory for the cache blocks.")
    assert diag["message"] == "No GPU memory left for KV cache after loading model."
    assert diag["suggestions"][0]["flag"] == "--gpu-memory-utilization"
    assert diag["suggestions"][0]["value"] == "0.95"


def test_tensor_parallel_mismatch():
    diag = _diagnose_serve_output("ValueError: Total number of attention heads must be divisible by tensor parallel size")
    assert "Tensor parallel" in diag["message"]
    assert all(s["flag"] == "--tensor-parallel-size" for s in diag["suggestions"])


def test_port_in_use_suggests_alternate_port():
    diag = _diagnose_serve_output("[Errno 98] Address already in use")
    assert diag["message"] == "Port is already in use."
    assert diag["suggestions"] == [
        {"label": "retry on port 8001", "op": "replace", "flag": "--port", "value": "8001"}
    ]


def test_missing_vllm_is_a_dependency_action():
    diag = _diagnose_serve_output("/bin/bash: line 1: vllm: command not found")
    assert diag["message"] == "vLLM is not installed or not in PATH on this server."
    assert diag["suggestions"] == [
        {"label": "install vLLM in Cookbook Dependencies", "op": "dependency", "package": "vllm"}
    ]


def test_gated_model_is_a_manual_action():
    diag = _diagnose_serve_output("huggingface_hub.errors.GatedRepoError: 403 Forbidden")
    assert diag["message"] == "Model access is gated or unauthorized."
    assert diag["suggestions"][0]["op"] == "manual"


def test_bare_traceback_is_flagged():
    diag = _diagnose_serve_output("Traceback (most recent call last):\n  File ...\nRuntimeError: boom")
    assert diag["message"] == "Python traceback detected during serve startup."
    assert diag["suggestions"][0]["op"] == "manual"


def test_traceback_suppressed_when_server_recovered():
    """A startup traceback the server recovered from (it is now serving /v1
    traffic) must NOT keep flagging — otherwise tmux scrollback pins a stale
    error on a healthy server forever."""
    text = (
        "Traceback (most recent call last):\n  File ...\nWarning: retrying\n"
        "INFO: Application startup complete.\nINFO: GET /v1/models HTTP/1.1 200 OK\n"
    )
    assert _diagnose_serve_output(text) is None


def test_only_reads_recent_tail():
    """Only the last ~6000 chars are scanned, so an error scrolled far out of
    view by later healthy output is not re-surfaced."""
    stale_error = "torch.cuda.OutOfMemoryError: CUDA out of memory.\n"
    later_output = "healthy line\n" * 600  # well over the 6000-char tail window
    assert _diagnose_serve_output(stale_error + later_output) is None


def test_first_matching_pattern_wins():
    """Pattern order is significant — the earliest match in the table is
    returned, so the OOM diagnosis takes precedence over a co-occurring
    port-in-use line."""
    diag = _diagnose_serve_output("CUDA out of memory\nAddress already in use\n")
    assert diag["message"] == "GPU ran out of memory during startup or warmup."
