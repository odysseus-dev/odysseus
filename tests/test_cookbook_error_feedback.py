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


def test_cookbook_routes_uses_shared_diagnosis_helper():
    # /api/cookbook/tasks/status must use the maintained helper. A nested copy in
    # cookbook_routes.py shadowed the import and silently drifted (missing the
    # ModelOpt, mflux and MLX bridge diagnoses).
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "routes" / "cookbook_routes.py").read_text(encoding="utf-8")
    local_defs = [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_diagnose_serve_output"
    ]
    assert local_defs == []


def test_modelopt_lm_head_checkpoint_returns_diagnosis():
    out = "ValueError: There is no module or parameter named 'lm_head.input_scale' in Qwen3ForCausalLM"
    result = _diagnose_serve_output(out)
    assert result is not None
    assert "ModelOpt" in result["message"]


def test_nvidia_driver_too_old_for_torch_returns_diagnosis():
    # vLLM 0.31 from PyPI ships torch cu130; on driver 570 (CUDA 12.8) the engine dies here.
    out = (
        "(EngineCore pid=1) ERROR [core.py:1433]     self.driver_worker.init_device()\n"
        "(EngineCore pid=1) ERROR [core.py:1433] RuntimeError: The NVIDIA driver on your system is too old "
        "(found version 12080). Please update your GPU driver by downloading and installing a new version\n"
        "Traceback (most recent call last):\n"
        "RuntimeError: Engine core initialization failed. See root cause above. Failed core proc(s): {}\n"
    )
    result = _diagnose_serve_output(out)
    assert result is not None
    assert "driver" in result["message"].lower()
    assert any("cu129" in s["label"] for s in result["suggestions"])


def test_cuda_runtime_newer_than_driver_returns_diagnosis():
    # torch switched to cu128 but the vLLM wheel's own kernels still need CUDA 13.
    out = (
        "RuntimeError: get_cuda_view_from_cpu_tensor, /workspace/csrc/libtorch_stable/cuda_view.cu:38, "
        "cudaHostGetDevicePointer failed: CUDA driver version is insufficient for CUDA runtime version\n"
        "Traceback (most recent call last):\n"
    )
    result = _diagnose_serve_output(out)
    assert result is not None
    assert "driver" in result["message"].lower()


def test_tasks_status_capture_joins_wrapped_tmux_lines():
    # tmux wraps at the pane width (often mid-word), which hid errors like
    # "insufficie|nt for CUDA runtime version" from the diagnosis patterns.
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "routes" / "cookbook_routes.py").read_text(encoding="utf-8")
    assert '_remote_tmux_command("capture-pane", "-t", session_id, "-p", "-J", "-S", "-500")' in source
    assert '["tmux", "capture-pane", "-t", session_id, "-p", "-J", "-S", "-500"]' in source
