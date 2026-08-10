"""MLX serve backends (mlx-lm + oMLX): allow-list, readiness, platform guard."""

import platform
import sys

import pytest

from routes.cookbook_helpers import (
    _SERVE_CMD_ALLOWLIST,
    _check_serve_binary,
    _parse_serve_phase,
    _is_apple_silicon,
    _guard_mlx_platform,
)

MLX_LM_CMD = "KMP_DUPLICATE_LIB_OK=TRUE mlx_lm.server --model ~/m --host 127.0.0.1 --port 8080"
OMLX_CMD = "omlx serve --model-dir ~/models --port 8000"
# The shape the Cookbook UI actually emits for the default mlx-lm engine.
MLX_LM_MODULE_CMD = "python3 -m mlx_lm.server --model '/m' --host 127.0.0.1 --port 8080"


def test_both_mlx_binaries_allowlisted():
    assert "omlx" in _SERVE_CMD_ALLOWLIST
    assert "mlx_lm.server" in _SERVE_CMD_ALLOWLIST


def test_mlx_serve_cmds_pass_validation():
    # Leading env-var assignments (KMP_DUPLICATE_LIB_OK) are skipped; the real
    # binary is the one that must be allow-listed.
    _check_serve_binary(OMLX_CMD)
    _check_serve_binary(MLX_LM_CMD)


def test_ready_via_uvicorn_startup():
    # oMLX is a FastAPI/uvicorn server.
    out = _parse_serve_phase("INFO:     Application startup complete.", "serve")
    assert out.get("status") == "ready"


def test_ready_via_models_access_log():
    out = _parse_serve_phase('INFO: 127.0.0.1 - "GET /v1/models HTTP/1.1" 200 OK', "serve")
    assert out.get("status") == "ready"


def test_ready_via_mlx_lm_httpd_line():
    # mlx-lm's built-in server prints this on bind.
    out = _parse_serve_phase("INFO Starting httpd at 127.0.0.1 on port 8080...", "serve")
    assert out.get("status") == "ready"


def test_apple_silicon_helper(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    assert _is_apple_silicon() is True
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    assert _is_apple_silicon() is False


@pytest.mark.parametrize("cmd", [OMLX_CMD, MLX_LM_CMD, MLX_LM_MODULE_CMD])
def test_mlx_rejected_off_apple_silicon(monkeypatch, cmd):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    with pytest.raises(Exception):  # HTTPException(400)
        _guard_mlx_platform(cmd, remote_host=None)


@pytest.mark.parametrize("cmd", [OMLX_CMD, MLX_LM_CMD, MLX_LM_MODULE_CMD])
def test_mlx_allowed_on_remote_host(monkeypatch, cmd):
    # A remote Mac is reachable over SSH; don't gate the local machine then.
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    _guard_mlx_platform(cmd, remote_host="mac.local")  # no raise


def test_non_mlx_cmd_not_guarded(monkeypatch):
    # A non-MLX serve command must never be rejected by the MLX guard.
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    _guard_mlx_platform("vllm serve org/model --port 8000", remote_host=None)  # no raise
