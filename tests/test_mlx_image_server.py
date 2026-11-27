"""Regression tests for scripts/mlx_image_server.py CLI routing (#6454)."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_mlx_image_server():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "mlx_image_server.py"
    spec = importlib.util.spec_from_file_location("mlx_image_server_under_test", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("model", "base_model", "expected_cli"),
    [
        ("mlx-community/FLUX.2-klein-4B-bf16", "", "mflux-generate-flux2"),
        ("mlx-community/flux2-klein-9b-8bit", "", "mflux-generate-flux2"),
        ("local/custom-weights", "flux2-klein", "mflux-generate-flux2"),
        ("Tongyi-MAI/Z-Image-Turbo", "", "mflux-generate-z-image-turbo"),
        ("mlx-community/z_image_turbo-4bit", "", "mflux-generate-z-image-turbo"),
        ("Qwen/Qwen-Image", "", "mflux-generate-qwen"),
        ("black-forest-labs/FLUX.1-dev", "", "mflux-generate"),
        ("mlx-community/FLUX.1-schnell-4bit", "", "mflux-generate"),
    ],
)
def test_cli_for_model_routes_mflux_families(model, base_model, expected_cli):
    srv = _load_mlx_image_server()
    assert srv._cli_for_model(model, base_model) == expected_cli


def test_generate_invokes_mflux_generate_flux2_for_flux2_klein(monkeypatch):
    srv = _load_mlx_image_server()
    srv._args = SimpleNamespace(
        model="mlx-community/FLUX.2-klein-4B-bf16",
        steps=4,
        width=512,
        height=512,
        base_model="",
        lora_style="",
        lora_paths=[],
        lora_scales=[],
        vlm_model="",
    )

    resolved_clis = []
    executed_cmds = []

    def fake_resolve_cli(name):
        resolved_clis.append(name)
        return f"/venv/bin/{name}"

    def fake_run(cmd, env=None, text=True, stdout=None, stderr=None):
        executed_cmds.append(cmd)
        out_idx = cmd.index("--output") + 1
        Path(cmd[out_idx]).write_bytes(b"fake-png-bytes")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(srv, "_resolve_cli", fake_resolve_cli)
    monkeypatch.setattr(srv.subprocess, "run", fake_run)

    res = srv.generate(srv.ImageRequest(prompt="a red vintage bicycle", size="512x512", quality="low"))
    assert resolved_clis == ["mflux-generate-flux2"]
    assert executed_cmds[0][0] == "/venv/bin/mflux-generate-flux2"
    assert "--width" in executed_cmds[0] and "--height" in executed_cmds[0]
    assert len(res["data"]) == 1
    assert res["data"][0]["b64_json"]
