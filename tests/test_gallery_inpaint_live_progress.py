from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AI_INPAINT_JS = (ROOT / "static" / "js" / "editor" / "ai-inpaint.js").read_text(encoding="utf-8")
STYLE_CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
GALLERY_ROUTES = (ROOT / "routes" / "gallery_routes.py").read_text(encoding="utf-8")


def test_inpaint_backend_exposes_live_progress_stream():
    assert '@router.get("/api/image/inpaint/progress/{progress_id}")' in GALLERY_ROUTES
    assert "StreamingResponse(" in GALLERY_ROUTES
    assert 'media_type="text/event-stream"' in GALLERY_ROUTES
    assert "_push_inpaint_progress(" in GALLERY_ROUTES
    assert 'progress("accepted", "Backend received the inpaint request.", percent=52)' in GALLERY_ROUTES
    assert 'progress("model_wait"' in GALLERY_ROUTES
    assert 'progress("backend_complete"' in GALLERY_ROUTES
    assert 'progress("failed"' in GALLERY_ROUTES


def test_inpaint_runner_sends_progress_id_and_updates_live_panel():
    assert "function createInpaintProgress" in AI_INPAINT_JS
    assert "root.setAttribute('role', 'status');" in AI_INPAINT_JS
    assert "root.setAttribute('aria-live', 'polite');" in AI_INPAINT_JS
    assert "new EventSource(`/api/image/inpaint/progress/${encodeURIComponent(id)}`)" in AI_INPAINT_JS
    assert "_progress_id: progress?.id || ''," in AI_INPAINT_JS
    assert "'Preparing crop'" in AI_INPAINT_JS
    assert "'Encoding request'" in AI_INPAINT_JS
    assert "'Model running'" in AI_INPAINT_JS
    assert "'Rendering result'" in AI_INPAINT_JS
    assert "progress?.done(" in AI_INPAINT_JS
    assert "progress?.fail(" in AI_INPAINT_JS
    assert "let localComplete = false;" in AI_INPAINT_JS
    assert "if (!event || destroyed || localComplete) return;" in AI_INPAINT_JS


def test_inpaint_progress_log_keeps_user_scroll_position_and_matches_theme():
    assert "const wasNearBottom =" in AI_INPAINT_JS
    assert "if (wasNearBottom) listEl.scrollTop = listEl.scrollHeight;" in AI_INPAINT_JS
    assert ".ge-inpaint-progress {" in STYLE_CSS
    assert "var(--accent, var(--red))" in STYLE_CSS
    assert "var(--panel)" in STYLE_CSS
    assert "var(--bg)" in STYLE_CSS
    assert ".ge-inpaint-progress-list" in STYLE_CSS
    assert "overscroll-behavior: contain;" in STYLE_CSS
    assert "scrollbar-width: thin;" in STYLE_CSS


def test_inpaint_supports_qwen_dashscope_image_edit_endpoint():
    assert "def _is_qwen_dashscope_image_edit" in GALLERY_ROUTES
    assert "def _qwen_dashscope_generation_url" in GALLERY_ROUTES
    assert "/services/aigc/multimodal-generation/generation" in GALLERY_ROUTES
    assert '"input": {' in GALLERY_ROUTES
    assert '"messages": [{' in GALLERY_ROUTES
    assert '"content": [' in GALLERY_ROUTES
    assert '"image": f"data:image/png;base64,{body.get(\'image\', \'\')}"' in GALLERY_ROUTES
    assert '"text": qwen_prompt' in GALLERY_ROUTES
    assert "The app will apply this result only inside the user's painted mask." in GALLERY_ROUTES
    assert "return _blend_provider_result(raw_b64)" in GALLERY_ROUTES
