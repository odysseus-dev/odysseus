from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.cookbook_helpers import (
    _append_sglang_linux_preflight_lines,
    _diagnose_serve_output,
    _validate_serve_cmd,
    build_sglang_install_recipe,
    build_sglang_serve_command,
)
from routes.cookbook_routes import setup_cookbook_routes


def test_sglang_linux_preflight_lines_check_import_version_and_kernel():
    lines: list[str] = []
    _append_sglang_linux_preflight_lines(lines)
    joined = "\n".join(lines)
    assert "import sglang" in joined
    assert "[odysseus] SGLang Python:" in joined
    assert "[odysseus] SGLang version:" in joined
    assert "import sgl_kernel" in joined
    assert "ODYSSEUS_PREFLIGHT_EXIT=127" in joined


def test_build_sglang_install_recipe_across_os_hardware_and_deployment():
    cuda_uv = build_sglang_install_recipe(platform="linux", backend="cuda", method="uv")
    assert cuda_uv["supported"] is True
    assert cuda_uv["package"] == "sglang[all]"
    assert "--torch-backend auto" in cuda_uv["commands"][0]

    rocm_pip = build_sglang_install_recipe(platform="linux", backend="rocm", method="pip")
    assert rocm_pip["supported"] is True
    assert rocm_pip["package"] == "sglang[srt_hip]"
    assert "sglang[srt_hip]" in rocm_pip["commands"][0]

    rocm_docker = build_sglang_install_recipe(platform="linux", backend="rocm", method="docker")
    assert rocm_docker["supported"] is True
    assert rocm_docker["image"] == "lmsysorg/sglang:latest-rocm"
    assert any("--device=/dev/kfd" in c for c in rocm_docker["commands"])

    mac = build_sglang_install_recipe(platform="darwin", backend="metal")
    assert mac["supported"] is False
    assert mac["recommended_alternative"] == "mlx_lm.server"

    win = build_sglang_install_recipe(platform="windows", backend="cuda")
    assert win["supported"] is False
    assert win["recommended_alternative"] == "llama-server"


def test_build_sglang_serve_command_passes_validation_and_multi_gpu_flags():
    cmd = build_sglang_serve_command(
        "Qwen/Qwen3-32B",
        host="0.0.0.0",
        port=30000,
        tp_size=2,
        context_length=32768,
        quantization="fp8",
        mem_fraction_static=0.88,
    )
    assert "sglang.launch_server" in cmd
    assert "--tp-size 2" in cmd
    assert "--context-length 32768" in cmd
    assert "--quantization fp8" in cmd
    assert "--mem-fraction-static 0.88" in cmd
    assert _validate_serve_cmd(cmd) == cmd


def test_sglang_kernel_diagnostic_and_plan_route():
    diag = _diagnose_serve_output("ERROR: SGLang native sgl_kernel check failed: Could not load any common_ops library")
    assert diag is not None
    assert "sglang" in diag["message"].lower()
    assert diag.get("suggestions")

    from core.middleware import require_admin

    app = FastAPI()
    app.include_router(setup_cookbook_routes())
    client = TestClient(app)

    unauth = client.get("/sglang/plan", params={"model_path": "Qwen/Qwen3-32B"})
    assert unauth.status_code == 403

    app.dependency_overrides[require_admin] = lambda: {"role": "admin"}

    resp = client.get(
        "/sglang/plan",
        params={
            "model_path": "Qwen/Qwen3-32B",
            "platform": "linux",
            "backend": "cuda",
            "method": "uv",
            "tp_size": 2,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["recipe"]["supported"] is True
    assert "--tp-size 2" in body["serve_command"]
