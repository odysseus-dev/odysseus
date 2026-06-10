from routes.cookbook_helpers import _diagnose_serve_output


def test_cuda_oom_returns_diagnosis():
    out = "torch.cuda.OutOfMemoryError: CUDA out of memory."
    result = _diagnose_serve_output(out)
    assert result is not None
    assert "memory" in result["message"].lower()
    assert any(s["op"] == "replace" for s in result["suggestions"])


def test_port_in_use_returns_diagnosis():
    out = "OSError: [Errno 98] Address already in use"
    result = _diagnose_serve_output(out)
    assert result is not None
    assert "port" in result["message"].lower()
    assert result["suggestions"][0]["flag"] == "--port"


def test_vllm_not_installed_returns_diagnosis():
    out = "No module named vllm"
    result = _diagnose_serve_output(out)
    assert result is not None
    assert "vLLM" in result["message"]
    assert result["suggestions"][0]["package"] == "vllm"


def test_gated_model_returns_diagnosis():
    out = "403 Forbidden\nAccess to model is restricted"
    result = _diagnose_serve_output(out)
    assert result is not None
    assert "gated" in result["message"].lower() or "unauthorized" in result["message"].lower()


def test_traceback_fallback_fires_without_startup_success():
    out = "Traceback (most recent call last):\n  File 'serve.py', line 1\nRuntimeError: bad config"
    result = _diagnose_serve_output(out)
    assert result is not None
    assert "traceback" in result["message"].lower()


def test_traceback_suppressed_when_server_started():
    out = (
        "Traceback (most recent call last):\n  File 'x.py'\nValueError: ...\n"
        "Application startup complete."
    )
    result = _diagnose_serve_output(out)
    assert result is None


def test_clean_output_returns_none():
    out = "INFO: Application startup complete.\nINFO: Uvicorn running on http://0.0.0.0:8000"
    assert _diagnose_serve_output(out) is None


def test_empty_input_returns_none():
    assert _diagnose_serve_output("") is None
    assert _diagnose_serve_output(None) is None


def test_trust_remote_code_pattern():
    out = "Please pass trust_remote_code=True when loading this model."
    result = _diagnose_serve_output(out)
    assert result is not None
    assert "--trust-remote-code" in result["suggestions"][0]["arg"]


def test_no_gguf_found_pattern():
    out = "No GGUF found on this host for model qwen/qwen2-7b"
    result = _diagnose_serve_output(out)
    assert result is not None
    assert "GGUF" in result["message"]
