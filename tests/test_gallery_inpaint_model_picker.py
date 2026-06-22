from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AI_MODELS_JS = (ROOT / "static" / "js" / "editor" / "ai-models.js").read_text(encoding="utf-8")
AI_INPAINT_JS = (ROOT / "static" / "js" / "editor" / "ai-inpaint.js").read_text(encoding="utf-8")
AI_TOOL_RUNNER_JS = (ROOT / "static" / "js" / "editor" / "ai-tool-runner.js").read_text(encoding="utf-8")
AI_REMBG_JS = (ROOT / "static" / "js" / "editor" / "ai-rembg.js").read_text(encoding="utf-8")
CONTROLS_JS = (ROOT / "static" / "js" / "editor" / "build" / "controls.js").read_text(encoding="utf-8")
GALLERY_EDITOR_JS = (ROOT / "static" / "js" / "galleryEditor.js").read_text(encoding="utf-8")
GALLERY_ROUTES = (ROOT / "routes" / "gallery_routes.py").read_text(encoding="utf-8")
CANVAS_EVENTS_JS = (ROOT / "static" / "js" / "editor" / "canvas-events.js").read_text(encoding="utf-8")
RIGHT_PANEL_JS = (ROOT / "static" / "js" / "editor" / "build" / "right-panel.js").read_text(encoding="utf-8")
SLIDER_UX_JS = (ROOT / "static" / "js" / "editor" / "slider-ux.js").read_text(encoding="utf-8")
STYLE_CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
SW_JS = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")


def test_inpaint_model_picker_wraps_native_select_without_changing_payload_source():
    assert "const SERVE_IMAGE_MODEL_VALUE = '__serve_cookbook__';" in AI_MODELS_JS
    assert "function isInpaintPickerSeparatorOption" in AI_MODELS_JS
    assert "!isInpaintPickerSeparatorOption(opt)" in AI_MODELS_JS
    assert "const fallback = modelId ? modelId.split('/').pop() : 'Image edit model';" in AI_MODELS_JS
    assert "function hasModernImageEditCue" in AI_MODELS_JS
    assert "function hasImageEditCue" in AI_MODELS_JS
    assert "function hasEndpointInpaintSurfaceCue" in AI_MODELS_JS
    assert "function safeEndpointDisplayFromUrl(value)" in AI_MODELS_JS
    assert "function looksSensitiveEndpointLabel(text)" in AI_MODELS_JS
    assert "Alibaba compatible endpoint" in AI_MODELS_JS
    assert "opt.dataset.endpointDisplay = endpointDisplay;" in AI_MODELS_JS
    assert "opt.dataset.modelDisplay = shortModel;" in AI_MODELS_JS
    assert "const endpointRef = ep.id ? `endpoint:${ep.id}` : (ep.base_url || '');" in AI_MODELS_JS
    assert "opt.dataset.endpointId = ep.id || '';" in AI_MODELS_JS
    assert "Dropdown values are encoded as \"endpoint:<id>::<model_id>\"" in GALLERY_EDITOR_JS
    assert "_endpoint_id: sel.endpointId" in AI_INPAINT_JS
    assert "if (sel.endpointId) extraPayload._endpoint_id = sel.endpointId;" in AI_TOOL_RUNNER_JS
    assert "def _visible_image_endpoint_for_id" in GALLERY_ROUTES
    assert 'endpoint_id = (body.pop("_endpoint_id", "") or "").strip()' in GALLERY_ROUTES
    assert "return `${desc.label} ${desc.meta}`.toLowerCase().includes(q);" in AI_MODELS_JS
    assert "`${desc.label} ${desc.meta} ${opt.value}`" not in AI_MODELS_JS
    assert "No image-edit endpoints found. LM Studio/GGUF downloads need a Diffusers or ONNX image endpoint." in AI_MODELS_JS
    assert "LM Studio GGUF models need a Diffusers or ONNX image endpoint for inpaint." in AI_MODELS_JS
    assert "dall-e-2" in AI_MODELS_JS
    assert "function wireInpaintModelPicker" in AI_MODELS_JS
    assert "btn.id = 'ge-ai-inpaint-picker-btn';" in AI_MODELS_JS
    assert "search.id = 'ge-ai-inpaint-picker-search';" in AI_MODELS_JS
    assert "select.classList.add('ge-model-native-select');" in AI_MODELS_JS
    assert "select.insertAdjacentElement('afterend', wrap);" in AI_MODELS_JS
    assert "document.body.appendChild(menu);" in AI_MODELS_JS
    assert "select.dispatchEvent(new Event('change', { bubbles: true }));" in AI_MODELS_JS
    assert "openCookbookForImg2img();" in AI_MODELS_JS
    assert "refreshModels: refreshInpaintModels" in AI_MODELS_JS


def test_inpaint_picker_accepts_common_edit_capable_endpoint_families():
    assert "img2img" in AI_MODELS_JS
    assert "image[-_\\s]*to[-_\\s]*image" in AI_MODELS_JS
    assert "paint[-_\\s]*by[-_\\s]*example" in AI_MODELS_JS
    assert "pix2pix" in AI_MODELS_JS
    assert "automatic1111" in AI_MODELS_JS
    assert "comfy" in AI_MODELS_JS
    assert "fooocus" in AI_MODELS_JS
    assert "modelCaps(modelId, ep.name, ep.model_type, ep)" in AI_MODELS_JS
    assert "endpointCanSurfaceInpaint" in AI_MODELS_JS


def test_inpaint_model_picker_has_mobile_sheet_and_clipping_safe_menu_styles():
    assert ".ge-inpaint-model-picker-menu {" in STYLE_CSS
    assert "position: fixed;" in STYLE_CSS
    assert ".ge-inpaint-model-row #ge-ai-inpaint.ge-model-native-select" in STYLE_CSS
    assert ".ge-inpaint-model-picker-search" in STYLE_CSS
    assert ".ge-inpaint-model-option.is-selected" in STYLE_CSS
    assert "function visiblePickerBounds(pad = 8, options = {})" in AI_MODELS_JS
    assert "container?.closest?.('.gallery-editor')" in AI_MODELS_JS
    assert "container?.closest?.('.gallery-modal-content, .modal-content')" in AI_MODELS_JS
    assert "const bounds = visiblePickerBounds(8, { viewportOnly: true });" in AI_MODELS_JS
    assert "menu.style.minWidth = `${Math.round(Math.min(260, width))}px`;" in AI_MODELS_JS
    assert "menu.style.maxWidth = `${Math.round(availableWidth)}px`;" in AI_MODELS_JS
    mobile_block = STYLE_CSS[STYLE_CSS.index("@media (max-width: 700px) {"):]
    assert ".ge-inpaint-model-picker-menu" in mobile_block
    assert "bottom: max(8px, env(safe-area-inset-bottom)) !important;" in mobile_block
    assert "max-height: min(70dvh, 520px) !important;" in mobile_block


def test_inpaint_popover_reclamps_to_visible_editor_window():
    assert "function _viewportFloatingBounds(pad = 12)" in GALLERY_EDITOR_JS
    assert "function _galleryEditorFloatingBounds(pad = 12, options = {})" in GALLERY_EDITOR_JS
    assert "function _clampInpaintPanelToFloatingBounds(panel, pad = 12, options = {})" in GALLERY_EDITOR_JS
    assert "function _portalInpaintPanel(panel)" in GALLERY_EDITOR_JS
    assert "function _setInpaintPanelViewportPosition(panel, left, top)" in GALLERY_EDITOR_JS
    assert "requestAnimationFrame(() => _clampInpaintPanelToFloatingBounds(panel, 8, { viewportOnly: true }));" in GALLERY_EDITOR_JS
    assert "const bounds = _galleryEditorFloatingBounds(8, { viewportOnly: true });" in GALLERY_EDITOR_JS
    assert "panel.dataset.userMoved = '1';" in GALLERY_EDITOR_JS
    assert "panel.style.maxHeight = `${Math.round(Math.max(160, bounds.bottom - bounds.top))}px`;" in GALLERY_EDITOR_JS


def test_gallery_editor_reparents_controls_across_mobile_orientation():
    assert "function syncControlsPlacement()" in RIGHT_PANEL_JS
    assert "state.container.insertBefore(controls, editorBody);" in RIGHT_PANEL_JS
    assert "rightPanel.insertBefore(controls, layerPanel);" in RIGHT_PANEL_JS
    assert "function restoreInpaintPanelToControls()" in RIGHT_PANEL_JS
    assert "window.addEventListener('orientationchange', scheduleControlsPlacementSync" in RIGHT_PANEL_JS
    assert "window.visualViewport?.addEventListener('resize', scheduleControlsPlacementSync" in RIGHT_PANEL_JS
    assert "screen.orientation?.addEventListener?.('change', scheduleControlsPlacementSync" in RIGHT_PANEL_JS
    assert "state.editorCleanupHandlers.push(cleanupPlacementSync);" in RIGHT_PANEL_JS
    assert "function _installEditorViewportLayoutSync()" in GALLERY_EDITOR_JS
    assert "window.addEventListener('orientationchange', schedule" in GALLERY_EDITOR_JS
    assert "window.visualViewport?.addEventListener('resize', schedule" in GALLERY_EDITOR_JS
    assert "state.editorCleanupHandlers.push(cleanup);" in GALLERY_EDITOR_JS
    assert "while (state.editorCleanupHandlers.length)" in GALLERY_EDITOR_JS
    mobile_block = STYLE_CSS[STYLE_CSS.index("@media (max-width: 700px) {"):]
    assert "max-height: min(60dvh, calc(100dvh - 118px));" in mobile_block
    assert "-webkit-overflow-scrolling: touch;" in mobile_block
    assert "overscroll-behavior: contain;" in mobile_block
    assert "touch-action: pan-y;" in mobile_block
    assert "box-sizing: border-box;" in mobile_block


def test_gallery_tool_sheet_dismiss_restores_layer_peek():
    assert "function revealLayerPeek()" in RIGHT_PANEL_JS
    assert "controls.classList.add('dismissed');\n        revealLayerPeek();" in RIGHT_PANEL_JS
    assert "if (isMobile && hasToolControls) {" in GALLERY_EDITOR_JS
    assert "if (controlsVisible) {\n            rp.classList.remove('expanded');" in GALLERY_EDITOR_JS
    assert "} else {\n            rp.classList.remove('expanded', 'minimized');" in GALLERY_EDITOR_JS


def test_gallery_editor_cache_bumped_for_android_assets():
    assert "const CACHE_NAME = 'odysseus-v416';" in SW_JS


def test_gallery_fit_zoom_uses_visible_canvas_above_mobile_layers_sheet():
    assert "function _getCanvasFitMetrics()" in GALLERY_EDITOR_JS
    assert "state.container.querySelector('.ge-right-panel')" in GALLERY_EDITOR_JS
    assert "Math.min(window.innerHeight || areaRect.bottom, areaRect.bottom)" in GALLERY_EDITOR_JS
    assert "maxH = Math.max(1, maxH - overlap);" in GALLERY_EDITOR_JS
    assert "panY = -overlap / 2;" in GALLERY_EDITOR_JS
    assert "_applyZoom({ panX: fit.panX, panY: fit.panY });" in GALLERY_EDITOR_JS
    assert "canvasArea._resetPan = (x = 0, y = 0) => applyOffset(x, y);" in CANVAS_EVENTS_JS


def test_gallery_layers_reserve_bottom_gesture_area():
    assert "--ge-bottom-gesture-reserve: 36px;" in STYLE_CSS
    assert "padding: 0 0 var(--ge-bottom-gesture-reserve, 36px);" in STYLE_CSS
    assert "scroll-padding-bottom: var(--ge-bottom-gesture-reserve, 36px);" in STYLE_CSS
    mobile_block = STYLE_CSS[STYLE_CSS.index("@media (max-width: 700px) {"):]
    assert "--ge-bottom-gesture-reserve: calc(env(safe-area-inset-bottom, 0px) + 48px);" in mobile_block
    assert "var(--peek-height, 110px) + var(--ge-bottom-gesture-reserve, 48px)" in mobile_block
    assert "72px + var(--ge-bottom-gesture-reserve, 48px)" in mobile_block


def test_gallery_layer_sheet_swipe_hides_slider_bubble():
    assert "window.__geHideSliderBubble = hideSliderBubble;" in SLIDER_UX_JS
    assert "document.addEventListener('ge:hide-slider-bubble', hideSliderBubble);" in SLIDER_UX_JS
    assert "document.addEventListener('pointercancel', hideSliderBubble);" in SLIDER_UX_JS
    assert "window.addEventListener('orientationchange', hideSliderBubble);" in SLIDER_UX_JS
    assert "if (!slider) {" in SLIDER_UX_JS
    assert "hideSliderBubble();" in SLIDER_UX_JS
    assert "const hideSliderBubble = () => {" in RIGHT_PANEL_JS
    assert "document.dispatchEvent(new CustomEvent('ge:hide-slider-bubble'));" in RIGHT_PANEL_JS
    assert RIGHT_PANEL_JS.count("hideSliderBubble();") >= 3


def test_sharpen_and_background_remove_have_image_model_selectors():
    assert 'id="ge-ai-sharpen"' in CONTROLS_JS
    assert 'data-ge-tool-model="sharpen"' in CONTROLS_JS
    assert 'id="ge-ai-rembg"' in CONTROLS_JS
    assert 'data-ge-tool-model="rembg"' in CONTROLS_JS
    assert 'id="ge-rembg-pipeline"' in CONTROLS_JS
    assert '<option value="model">Local/API model</option>' in CONTROLS_JS
    assert '<option value="rembg">Natural rembg</option>' in CONTROLS_JS
    assert '<option value="heuristic">Heuristic sample</option>' in CONTROLS_JS
    assert 'id="ge-rembg-strength"' in CONTROLS_JS
    assert 'id="ge-rembg-strength-label">70%</span>' in CONTROLS_JS
    assert "payload.strength = rembgStrengthValue();" in AI_REMBG_JS
    assert "payload.bg_remove_pipeline = rembgPipelineValue();" in AI_REMBG_JS
    assert "localStorage.setItem('ge-rembg-pipeline'" in AI_REMBG_JS
    assert "function rembgStrengthValue()" in AI_REMBG_JS
    assert "function rembgPipelineValue()" in AI_REMBG_JS
    assert "appendLocalRembgOptions" in AI_MODELS_JS
    assert "ISNet general use · best local" in AI_MODELS_JS
    assert "Silueta · balanced local" in AI_MODELS_JS
    assert "u2netp · fast fallback" in AI_MODELS_JS
