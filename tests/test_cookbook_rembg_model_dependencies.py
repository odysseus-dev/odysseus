from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHELL_ROUTES = (ROOT / "routes" / "shell_routes.py").read_text(encoding="utf-8")
COOKBOOK_JS = (ROOT / "static" / "js" / "cookbook.js").read_text(encoding="utf-8")


def test_cookbook_lists_rembg_onnx_model_dependencies():
    assert "REMBG_MODEL_DEPENDENCIES = {" in SHELL_ROUTES
    assert '"name": "rembg"' in SHELL_ROUTES
    assert '"pip": "rembg"' in SHELL_ROUTES
    assert '"pip": "rembg[gpu]"' not in SHELL_ROUTES.split('"name": "rembg"', 1)[1].split('"name": "onnxruntime"', 1)[0]
    assert '"name": "onnxruntime"' in SHELL_ROUTES
    assert '"pip": "onnxruntime"' in SHELL_ROUTES
    assert '"name": "onnxruntime-directml"' in SHELL_ROUTES
    assert '"pip": "onnxruntime-directml"' in SHELL_ROUTES
    assert "GPU ONNX Runtime via DirectML" in SHELL_ROUTES
    assert '"silueta"' in SHELL_ROUTES
    assert '"isnet-general-use"' in SHELL_ROUTES
    assert "https://github.com/danielgatis/rembg/releases/download/v0.0.0/silueta.onnx" in SHELL_ROUTES
    assert "https://github.com/danielgatis/rembg/releases/download/v0.0.0/isnet-general-use.onnx" in SHELL_ROUTES
    assert '"name": "rembg-silueta"' in SHELL_ROUTES
    assert '"name": "rembg-isnet-general-use"' in SHELL_ROUTES
    assert '"kind": "file"' in SHELL_ROUTES
    assert "_rembg_model_status(pkg.get(\"model\") or \"\")" in SHELL_ROUTES


def test_cookbook_rembg_model_install_endpoint_is_allowlisted():
    assert '@router.post("/api/cookbook/rembg-models/install")' in SHELL_ROUTES
    install_route = SHELL_ROUTES.split('@router.post("/api/cookbook/rembg-models/install")', 1)[1].split(
        '@router.post("/api/cookbook/rebuild-engine")',
        1,
    )[0]
    assert "_require_admin(request)" in install_route
    assert "_reject_cross_site(request)" in install_route
    assert "if model not in REMBG_MODEL_DEPENDENCIES:" in install_route
    assert "await asyncio.to_thread(_download_rembg_model, model)" in install_route


def test_cookbook_ui_downloads_file_dependencies_without_pip():
    assert "isFileDep = pkg.kind === 'file'" in COOKBOOK_JS
    assert "data-dep-model" in COOKBOOK_JS
    assert "data-dep-endpoint" in COOKBOOK_JS
    assert "async function _installModelDep(modelName, pkgName, statusEl, endpoint)" in COOKBOOK_JS
    assert "endpoint || '/api/cookbook/rembg-models/install'" in COOKBOOK_JS
    assert "const modelName = btn.dataset.depModel || '';" in COOKBOOK_JS
    assert "const endpoint = btn.dataset.depEndpoint || '';" in COOKBOOK_JS
    assert "await _installModelDep(modelName, pkgName || modelName, btn, endpoint);" in COOKBOOK_JS


def test_cookbook_onnx_diffusion_serves_with_directml_defaults():
    assert "const isOnnxModel = /onnx/i.test" in COOKBOOK_JS
    assert "cmd += ' --backend onnx';" in COOKBOOK_JS
    assert "cmd += ' --provider DmlExecutionProvider';" in COOKBOOK_JS
    assert "if (!f.diff_width) cmd += ' --width 512';" in COOKBOOK_JS
    assert "if (!f.diff_height) cmd += ' --height 512';" in COOKBOOK_JS
