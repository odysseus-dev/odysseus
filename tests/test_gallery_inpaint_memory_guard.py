from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AI_INPAINT_JS = (ROOT / "static/js/editor/ai-inpaint.js").read_text(encoding="utf-8")
MASK_UTILS_JS = (ROOT / "static/js/editor/mask-utils.js").read_text(encoding="utf-8")
GALLERY_ROUTES = (ROOT / "routes/gallery_routes.py").read_text(encoding="utf-8")


def test_inpaint_posts_bounded_work_canvas_instead_of_full_photo():
    assert "const INPAINT_MAX_WORK_PIXELS = 1024 * 1024;" in AI_INPAINT_JS
    assert "function prepareInpaintWork" in AI_INPAINT_JS
    assert "width: work.imageCanvas.width" in AI_INPAINT_JS
    assert "height: work.imageCanvas.height" in AI_INPAINT_JS
    assert "const flatCanvas = document.createElement('canvas');" not in AI_INPAINT_JS


def test_inpaint_uses_blob_encoding_and_releases_large_strings():
    assert "function canvasToPngBase64(canvas)" in AI_INPAINT_JS
    assert "canvas.toBlob((blob) =>" in AI_INPAINT_JS
    assert "let requestBody = JSON.stringify(payload);" in AI_INPAINT_JS
    assert "payload.image = '';" in AI_INPAINT_JS
    assert "payload.mask = '';" in AI_INPAINT_JS
    assert "requestBody = null;" in AI_INPAINT_JS


def test_inpaint_scans_mask_bounds_in_tiles():
    assert "const tileH = 128;" in AI_INPAINT_JS
    assert "ctx.getImageData(0, y0, w, th).data" in AI_INPAINT_JS


def test_pc_inpaint_rejects_oversized_request_bodies():
    route = GALLERY_ROUTES.split('async def inpaint_proxy(request: Request):', 1)[1].split(
        '# ---- POST /api/image/harmonize', 1
    )[0]
    assert 'request.headers.get("content-length")' in route
    assert "content_length > 32 * 1024 * 1024" in route
    assert "raise HTTPException(413, \"Inpaint request is too large." in route
    assert "except MemoryError:" in route


def test_inpaint_keeps_cropped_ai_source_for_edge_tuning():
    assert "mask: work.hardMaskCanvas" in AI_INPAINT_JS
    assert "x: work.crop.x" in AI_INPAINT_JS
    assert "y: work.crop.y" in AI_INPAINT_JS
    assert "w: work.crop.w" in AI_INPAINT_JS
    assert "h: work.crop.h" in AI_INPAINT_JS
    assert "const hasCrop = Number.isFinite(layer.inpaintSource.x)" in MASK_UTILS_JS
    assert "ctx.drawImage(ai, dx, dy, dw, dh)" in MASK_UTILS_JS
    assert "ctx.drawImage(softMask, dx, dy, dw, dh)" in MASK_UTILS_JS
