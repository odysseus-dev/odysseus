"""The heavy image-edit CPU work must run off the event loop, and the app must
expose in-flight heavy-op counts + a graceful shutdown so a deploy restart does
not drop a 60-150 s edit."""
import base64
import io
from pathlib import Path

from PIL import Image

from routes.gallery import gallery_routes as gr

ROOT = Path(__file__).resolve().parents[1]


def _png_b64(w, h, color=(120, 160, 210)):
    buf = io.BytesIO()
    Image.new("RGBA", (w, h), color).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_prepare_local_edit_caps_large_input():
    src, mask, mw, mh, orig = gr._prepare_local_edit(_png_b64(4000, 3000), None, 768)
    assert orig == (4000, 3000)
    assert max(mw, mh) <= 768 and mw % 16 == 0 and mh % 16 == 0
    assert mask  # a full-white mask is synthesised when none is supplied
    assert Image.open(io.BytesIO(src)).size == (mw, mh)


def test_prepare_local_edit_leaves_small_input_unchanged():
    _src, _mask, mw, mh, orig = gr._prepare_local_edit(_png_b64(96, 96), _png_b64(96, 96, (255, 255, 255)), 768)
    assert orig == (96, 96) and (mw, mh) == (96, 96)


def test_restore_local_b64_resizes_back_to_original():
    raw = _png_b64(768, 576)
    out = gr._restore_local_b64(raw, (4000, 3000), (768, 576))
    assert Image.open(io.BytesIO(base64.b64decode(out))).size == (4000, 3000)
    # a same-size result is returned untouched
    assert gr._restore_local_b64(raw, (768, 576), (768, 576)) == raw


def test_inpaint_offloads_cpu_work_to_a_thread():
    src = (ROOT / "routes/gallery/gallery_routes.py").read_text()
    assert "await asyncio.to_thread(\n                        _prepare_local_edit" in src
    assert "asyncio.to_thread(\n                                _restore_local_b64" in src


def test_app_exposes_busy_count_and_graceful_shutdown():
    src = (ROOT / "app.py").read_text()
    assert "busy_image_ops" in src
    assert '@app.get("/api/busy")' in src
    assert "_HEAVY_IMAGE_OP_PREFIXES" in src
    assert "timeout_graceful_shutdown" in src
    assert "activity.begin_image_op" in src and "activity.end_image_op" in src
