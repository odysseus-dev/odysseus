import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "static" / "js" / "galleryEditor.js").read_text()
ROUTES = (ROOT / "routes" / "gallery" / "gallery_routes.py").read_text()
SW = (ROOT / "static" / "sw.js").read_text()


def test_client_image_mode_generates():
    assert "if (mode === 'image')" in JS
    assert "_runQuickControlOp('ai.generateImage'" in JS
    assert "Generating an image…" in JS
    assert "Generated an image." in JS
    assert "Could not generate an image:" in JS


def test_client_image_mode_runs_after_harmonize():
    harm = JS.index("_isHarmonizeIntent(prompt))")
    img = JS.index("if (mode === 'image')")
    assert harm < img


def test_server_generate_route_exists():
    assert '@router.post("/api/image/generate")' in ROUTES
    assert "do_generate_image" in ROUTES
    assert "can_generate_images" in ROUTES.split('@router.post("/api/image/generate")', 1)[1][:1200]


def test_sw_cache_bumped():
    m = re.search(r"CACHE_NAME = '(odysseus-v\d+-pr40-editor-precache)'", SW)
    assert m and re.fullmatch(r"odysseus-v\d+-pr40-editor-precache", m.group(1))
