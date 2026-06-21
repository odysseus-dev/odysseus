from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIFFUSION_SERVER = (ROOT / "scripts" / "diffusion_server.py").read_text(encoding="utf-8")
COOKBOOK_ROUTES = (ROOT / "routes" / "cookbook_routes.py").read_text(encoding="utf-8")


def test_diffusion_server_loads_onnx_inpaint_with_directml_provider():
    assert "from optimum.onnxruntime import (" in DIFFUSION_SERVER
    assert "ORTStableDiffusionInpaintPipeline" in DIFFUSION_SERVER
    assert "\"DmlExecutionProvider\"" in DIFFUSION_SERVER
    assert "provider_options = {\"device_id\": int(_args.device_id)}" in DIFFUSION_SERVER
    assert "kwargs[\"provider_options\"] = provider_options" in DIFFUSION_SERVER
    assert "_pipe_backend = \"onnx\"" in DIFFUSION_SERVER


def test_diffusion_server_rejects_lm_studio_gguf_for_inpaint():
    assert "def _reject_unsupported_gguf_image_model" in DIFFUSION_SERVER
    assert "LM Studio/GGUF diffusion checkpoints" in DIFFUSION_SERVER
    assert "image+mask" in DIFFUSION_SERVER
    assert "stable-diffusion-3.5-medium" in DIFFUSION_SERVER
    assert "_reject_unsupported_gguf_image_model(model_path)" in DIFFUSION_SERVER


def test_onnx_inpaint_uses_static_512_work_size_and_valid_step_count():
    assert "legacy ONNX SD1.5 inpaint exports are" in DIFFUSION_SERVER
    assert "target_w = int(_args.width or 512)" in DIFFUSION_SERVER
    assert "target_h = int(_args.height or target_w)" in DIFFUSION_SERVER
    assert "min_steps_for_strength = max(1, math.ceil(1.0 / strength))" in DIFFUSION_SERVER
    assert "Adjusted inpaint steps from %s to %s" in DIFFUSION_SERVER


def test_cookbook_preflight_checks_onnx_directml_dependencies():
    assert "ONNX/DirectML diffusion serving requires PyTorch + diffusers + optimum-onnx + onnxruntime-directml" in COOKBOOK_ROUTES
    assert "from optimum.onnxruntime import ORTDiffusionPipeline" in COOKBOOK_ROUTES
    assert "DmlExecutionProvider" in COOKBOOK_ROUTES
    assert "ort.get_available_providers()" in COOKBOOK_ROUTES
