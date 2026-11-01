import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "static" / "js" / "galleryEditor.js").read_text()
ROUTES = (ROOT / "routes" / "gallery" / "gallery_routes.py").read_text()
SW = (ROOT / "static" / "sw.js").read_text()


def test_client_gen_mode_generates():
    assert "if (mode === 'gen')" in JS
    assert "_runQuickControlOp('ai.generateImage'" in JS
    assert "Generating an image…" in JS
    assert "Generated an image." in JS
    assert "Could not generate an image:" in JS


def test_client_gen_mode_runs_after_harmonize():
    harm = JS.index("_isHarmonizeIntent(prompt))")
    img = JS.index("if (mode === 'gen')")
    assert harm < img


def test_editing_status_is_distinct_from_generating_status():
    # Image mode makes a brand-new picture; every other mode edits the canvas.
    assert "_setAiCommandStatus('Editing the image…', 'running');" in JS
    assert "_setAiCommandStatus('Generating an image…', 'running');" in JS
    assert "_setAiCommandStatus('Generating…', 'running');" not in JS


def test_server_generate_route_exists():
    assert '@router.post("/api/image/generate")' in ROUTES
    assert "do_generate_image" in ROUTES
    assert "can_generate_images" in ROUTES.split('@router.post("/api/image/generate")', 1)[1][:1200]


def test_sw_cache_bumped():
    m = re.search(r"CACHE_NAME = '(odysseus-v\d+-pr40-editor-precache)'", SW)
    assert m and re.fullmatch(r"odysseus-v\d+-pr40-editor-precache", m.group(1))


def test_generate_route_forwards_the_picked_model():
    """The model picker's choice must reach do_generate_image.

    Settings defaults may otherwise point at the local Qwen server and the
    user's chosen OpenRouter model is never used.
    """
    route = ROUTES.split('@router.post("/api/image/generate")', 1)[1][:2600]
    assert 'model_choice = str((body or {}).get("model") or "").strip()' in route
    assert 'payload["model"] = model_choice' in route
    assert "content = _json.dumps(payload)" in route
    assert "localStorage.getItem('ge-ai-command-model')" in JS


def test_cloud_image_model_is_resolved_before_the_local_fallback():
    """A gateway model must not be hijacked onto the local image endpoint."""
    ai = (ROOT / "src" / "ai_interaction.py").read_text()
    block = ai.split("async def _resolve_image_model(model_name: str):", 1)[1][:900]
    plain = block.index("_resolve_model(model_name, owner=owner)")
    typed = block.index('model_type="image"')
    assert plain < typed
