"""Translation of mlx-lm's checkpoint key-mismatch load failure.

mlx-lm rejects a checkpoint whose tensor keys don't match any architecture it
knows — nearly always an mlx-lm older than the model. Raw, that surfaces as a
bare KeyError on a tensor name (or keyNotFound from the Swift bridge), which
reads like an Odysseus bug. The raw output stays in the log either way.
"""

from routes.cookbook_helpers import _diagnose_serve_output, _parse_serve_phase

_KEY_ERROR_LOG = (
    "Traceback (most recent call last):\n"
    '  File "/opt/venv/lib/python3.12/site-packages/mlx_lm/utils.py", line 201, in load_model\n'
    "    model.load_weights(list(weights.items()))\n"
    "KeyError: 'model.layers.0.self_attn.q_proj.weight'\n"
)
_SWIFT_LOG = "mlx_lm.server: fatal error: keyNotFound(CodingKeys(stringValue: \"model.embed_tokens\"))"
_MISSING_PARAMS_LOG = "mlx_lm.server: ValueError: Received parameters not in model: model.layers.0.mlp.gate.weight"


def test_phase_reports_the_load_failure():
    out = _parse_serve_phase(_KEY_ERROR_LOG, "serve")
    assert "mlx-lm too old" in out["phase"]
    # Not "ready" — the caller flips the task to error via the diagnosis.
    assert out["status"] == ""


def test_diagnosis_names_the_real_cause():
    diag = _diagnose_serve_output(_KEY_ERROR_LOG)
    assert diag is not None
    assert "newer mlx-lm" in diag["message"]
    assert any(s.get("package") == "mlx-lm" for s in diag["suggestions"])


def test_swift_key_not_found_is_recognized():
    assert _diagnose_serve_output(_SWIFT_LOG)["message"] == _diagnose_serve_output(_KEY_ERROR_LOG)["message"]


def test_received_parameters_not_in_model_is_recognized():
    assert "newer mlx-lm" in _diagnose_serve_output(_MISSING_PARAMS_LOG)["message"]


def test_a_serving_mlx_process_still_reads_ready():
    """A key mismatch is a load-time failure. If the server got up and served a
    request, the ready signal must win over a stale error further up the pane."""
    log = _KEY_ERROR_LOG + '\nINFO Starting httpd at 127.0.0.1 on port 8080\n'
    assert _parse_serve_phase(log, "serve")["status"] == "ready"


def test_non_mlx_key_error_is_not_claimed():
    """A KeyError from a vLLM/llama.cpp launch must not be blamed on mlx-lm."""
    vllm_log = (
        "Traceback (most recent call last):\n"
        '  File "/opt/venv/lib/python3.12/site-packages/vllm/model_executor/loader.py", line 88\n'
        "KeyError: 'model.layers.0.self_attn.q_proj.weight'\n"
    )
    out = _parse_serve_phase(vllm_log, "serve")
    assert "mlx" not in out.get("phase", "")
    diag = _diagnose_serve_output(vllm_log)
    assert diag is None or "mlx-lm" not in diag["message"]


def test_mlx_community_model_path_alone_does_not_make_it_an_mlx_failure():
    """A CUDA box serving a repo whose PATH contains "mlx" is still vLLM. The
    pane echoes the launch command, so the bare substring "mlx" is everywhere —
    only the engine name (mlx_lm / omlx) may claim the failure."""
    vllm_log = (
        "+ vllm serve /models/mlx-community/Qwen3-4B-4bit --port 8000\n"
        "Traceback (most recent call last):\n"
        '  File "/opt/venv/lib/python3.12/site-packages/vllm/model_executor/loader.py", line 88\n'
        "KeyError: 'model.layers.0.self_attn.q_proj.weight'\n"
    )
    out = _parse_serve_phase(vllm_log, "serve")
    assert "mlx-lm too old" not in out.get("phase", "")
    diag = _diagnose_serve_output(vllm_log)
    assert diag is None or "newer mlx-lm" not in diag["message"]


def test_real_mlx_launch_lines_still_claim_the_failure():
    """The three shapes Cookbook emits all name the engine, so a genuine MLX
    key mismatch is still translated."""
    key_error = "KeyError: 'model.layers.0.self_attn.q_proj.weight'\n"
    for launch in (
        "+ python3 -m mlx_lm.server --model /m/Qwen3-4bit --port 8080\n",
        "+ mlx_lm.server --model /m/Qwen3-4bit --port 8080\n",
        "+ omlx serve --model-dir /m --host 127.0.0.1 --port 8000\n",
    ):
        log = launch + key_error
        assert "mlx-lm too old" in _parse_serve_phase(log, "serve")["phase"], launch
        assert "newer mlx-lm" in _diagnose_serve_output(log)["message"], launch
