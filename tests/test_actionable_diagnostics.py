from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.cookbook_routes as cookbook_routes
from core.middleware import require_admin
from routes.cookbook_helpers import _diagnose_serve_output
from routes.cookbook_output import diagnose_task_output


def test_actionable_diagnostics_for_failed_downloads():
    zero = diagnose_task_output(
        "download",
        "Fetching 0 files: 0it [00:00, ?it/s]\nDOWNLOAD_OK\n",
        status="error",
        zero_files=True,
        cmd="hf download org/model --include *Q4_K_M.gguf",
        log_path="/tmp/dl-123.log",
    )
    assert zero is not None
    assert zero["category"] == "download"
    assert "No matching files" in zero["message"]
    assert zero["cmd"].startswith("hf download")
    assert zero["log_path"] == "/tmp/dl-123.log"
    assert zero["suggestions"]

    gated = diagnose_task_output(
        "download",
        "huggingface_hub.errors.GatedRepoError: 403 Client Error: Access to model meta-llama/Llama-3.3-70B is restricted.\nDOWNLOAD_FAILED\n",
        status="error",
        exit_code=1,
    )
    assert gated is not None
    assert gated["category"] == "download"
    assert "authentication" in gated["message"].lower()
    assert any("HF_TOKEN" in s["label"] for s in gated["suggestions"])


def test_actionable_diagnostics_for_failed_dependencies():
    dep = diagnose_task_output(
        "dependency",
        "Building wheel for llama-cpp-python (pyproject.toml) ... error\nfatal error: Python.h: No such file or directory\n",
        status="error",
        exit_code=1,
        cmd="pip install llama-cpp-python",
        log_path="/tmp/dep-456.log",
    )
    assert dep is not None
    assert dep["category"] == "dependency"
    assert "native c/c++" in dep["message"].lower()
    assert any("build-essential" in s["label"] for s in dep["suggestions"])
    assert "Python.h" in dep["output_tail"]


def test_actionable_diagnostics_for_preflight_failures():
    preflight = diagnose_task_output(
        "serve",
        "ERROR: vllm is not installed in this environment.\nODYSSEUS_PREFLIGHT_EXIT=127\n",
        status="error",
        exit_code=127,
        cmd="vllm serve Qwen/Qwen3-32B",
    )
    assert preflight is not None
    assert preflight["category"] == "preflight"
    assert preflight["exit_code"] == 127
    assert preflight["suggestions"]


def test_actionable_diagnostics_for_failed_serve_jobs():
    raw_oom = _diagnose_serve_output("torch.cuda.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.00 GiB")
    serve_diag = diagnose_task_output(
        "serve",
        "torch.cuda.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.00 GiB\n",
        status="error",
        exit_code=1,
        cmd="vllm serve Qwen/Qwen3-32B --max-model-len 32768",
        log_path="/tmp/serve-789.log",
        base_diagnosis=raw_oom,
    )
    assert serve_diag is not None
    assert serve_diag["category"] == "serve"
    assert "out of memory" in serve_diag["message"].lower()
    assert serve_diag["log_path"] == "/tmp/serve-789.log"
    assert any(s.get("flag") == "--max-model-len" for s in serve_diag["suggestions"])


def test_diagnostics_endpoint_exposes_cmd_logs_and_next_steps(monkeypatch, tmp_path):
    monkeypatch.setattr(cookbook_routes, "TMUX_LOG_DIR", tmp_path)
    (tmp_path / "dl-abc123.log").write_text(
        "OSError: [Errno 28] No space left on device\nDOWNLOAD_FAILED\n",
        encoding="utf-8",
    )
    (tmp_path / "dl-abc123_run.sh").write_text(
        "#!/usr/bin/env bash\nODYSSEUS_SERVE_CMD='hf download Qwen/Qwen3-32B'\n",
        encoding="utf-8",
    )

    app = FastAPI()
    app.include_router(cookbook_routes.setup_cookbook_routes())
    client = TestClient(app)

    unauth = client.get("/diagnostics/dl-abc123")
    assert unauth.status_code == 403

    app.dependency_overrides[require_admin] = lambda: {"role": "admin"}
    resp = client.get("/diagnostics/dl-abc123")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "dl-abc123"
    assert body["type"] == "download"
    assert body["cmd"] == "hf download Qwen/Qwen3-32B"
    assert "No space left on device" in body["output_tail"]
    assert "disk space" in body["diagnosis"]["message"].lower()
    assert body["diagnosis"]["suggestions"]
