from pathlib import Path
from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parents[1]
EDITOR = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
AI_SETTINGS = (ROOT / "static/js/editor/ai-settings.js").read_text(encoding="utf-8")
AGENT = (ROOT / "static/js/editor/editor-agent.js").read_text(encoding="utf-8")
STYLE = app_css()


def test_quick_edit_has_persistent_expanded_identity_and_labeled_input():
    assert 'class="ge-ai-command-head"' in EDITOR
    assert 'class="ge-ai-command-title">Quick Edit</span>' in EDITOR
    assert 'aria-label="Describe the image edit"' in EDITOR
    assert 'placeholder="Describe an edit, or choose an action"' in EDITOR
    assert ".ge-ai-command-head" in STYLE


def test_quick_edit_exposes_initial_actions_without_running_them_on_click():
    assert "if (!q) return [];" in EDITOR
    assert ".slice(0, 4)" in EDITOR
    click_handler = EDITOR[
        EDITOR.index("suggestions?.addEventListener('click'") :
        EDITOR.index("const setOpen", EDITOR.index("suggestions?.addEventListener('click'"))
    ]
    assert "pickSuggestion(Number(btn.dataset.aiCommandSuggestion), false);" in click_handler


def test_quick_edit_has_clear_and_action_type_states():
    assert 'id="ge-ai-command-clear"' in EDITOR
    assert "await _runQuickControlOp('ai.generativeUpscale', { scale });" in EDITOR
    assert "not available in this editor state" not in EDITOR
    assert "class=\"ge-ai-command-suggestion-kind\"" in EDITOR
    assert ".ge-ai-command-clear" in STYLE
    assert ".ge-ai-command-busy" in STYLE


def test_quick_edit_owns_escape_without_closing_the_editor():
    assert "ge-ai-command-close" in EDITOR
    assert "quickEdit.dispatchEvent(new CustomEvent('ge-ai-command-close'))" in EDITOR
    assert "top: -2px;" in STYLE[STYLE.index(".ge-ai-command-clear"):STYLE.index(".ge-ai-command-run")]


def test_quick_edit_flip_and_rotate_accept_numeric_degrees():
    assert "(?:flip|rotate|turn)" in EDITOR
    assert "(90|180|270)" in EDITOR
    assert "`Rotated ${deg}\\u00b0.`" in EDITOR
    assert "'flip 90'" in EDITOR
    assert "'flip 180'" in EDITOR


def test_quick_edit_transforms_do_not_add_a_second_undo_snapshot():
    start = EDITOR.index("if (/\\brotate\\b.*\\b180\\b")
    end = EDITOR.index("const _bgRemove = ", start)
    transform_block = EDITOR[start:end]
    assert "_saveState('Rotate" not in transform_block
    assert "_saveState('Flip" not in transform_block


def test_quick_edit_handles_layer_management_commands():
    for op in (
        "layers.addRaster",
        "layers.addAdjustment",
        "layers.duplicate",
        "layers.delete",
        "layers.rename",
        "layers.setVisibility",
        "layers.reorder",
        "layers.mergeDown",
        "layers.flatten",
        "layers.group",
        "layers.ungroup",
    ):
        assert f"'{op}'" in EDITOR, op
    assert "function _detectAdjustmentType(" in EDITOR
    assert "function _layerReorderDirection(" in EDITOR
    assert "async function _runQuickControlOp(" in EDITOR
    assert "'new layer'" in EDITOR
    assert "'delete layer'" in EDITOR


def test_place_image_supports_instruction_difference_mask():
    assert "const _differenceMaskCanvas = (before, after," in EDITOR
    assert "changedFraction" in EDITOR
    assert "differenceMask === true" in EDITOR
    assert "String(p.mode || '').toLowerCase() === 'instruction'" in EDITOR
    assert "const beforeSnap = useDiff ? flatten() : null;" in EDITOR
    # (A) adaptive edge handling: the difference mask's coverage decides whether
    # a thin edge band is dropped. Large coverage (scene/background replacement)
    # keeps generated pixels out to the edge (edge-filling the outer band from
    # nearest interior generated pixels); small local edits drop a 3px band
    # (provider resampling otherwise leaves a spurious frame). The result is
    # drawn back at the exact original document size with high-quality smoothing.
    assert "const diff = _differenceMaskCanvas(beforeSnap, layer.canvas, { threshold: p.diffThreshold, grow: p.diffGrow, feather: p.diffFeather, edgeExcludePx: 0 });" in EDITOR
    assert "const sceneReplace = diff.changedFraction > 0.5;" in EDITOR
    assert "const edgeExclude = sceneReplace ? 0 : (explicitEdge != null ? explicitEdge : 3);" in EDITOR
    assert "if (edgeExclude > 0) _clearMaskEdgeBand(diff.mask, edgeExclude);" in EDITOR
    assert "if (sceneReplace) _edgeFillFromInterior(layer.ctx, dw, dh, 3);" in EDITOR
    assert "imageSmoothingQuality = 'high'" in EDITOR


def test_generative_expand_inpaints_border_and_feathers_seam():
    assert "extend the scene seamlessly, continuing the surrounding content and lighting naturally" in EDITOR
    assert "_bakeMaskIntoLayer(layer.ctx, blendMask)" in EDITOR
    assert "const seam = Math.max(12," in EDITOR
    # Wider feather + a colour-match tint pull the generated border toward the
    # original edge band so the rectangular seam is less visible.
    assert "Math.round(seam * 1.4)" in EDITOR
    assert "function _edgeBandAverage(canvas, b)" in EDITOR
    assert "const edge = _edgeBandAverage(flat, b);" in EDITOR
    # Border is pre-filled by stretching edge pixels before the inpaint, and
    # the request forwards an explicit step count.
    assert "const steps = Math.max(1, Math.round(Number(p && p.steps) || 4));" in EDITOR
    assert "steps })" in EDITOR
    assert "return { width: tw, height: th, layerId: layer.id, seam, steps }" in EDITOR
    # Expand inpaints from the real content bounds, not the whole document, so
    # uniform blank-canvas margins (white Background) aren't stretched into the
    # new border and continued by the model.
    assert "function _contentBounds()" in EDITOR
    assert "function _layerIsUniform(canvas)" in EDITOR
    assert "const flat = flatten(); const b = _contentBounds();" in EDITOR
    assert "mctx.clearRect(b.x, b.y, b.w, b.h);" in EDITOR


def test_quick_edit_stacks_prompts_as_a_job_queue():
    assert 'class="ge-ai-command-stack" id="ge-ai-command-stack"' in EDITOR
    assert "let _quickEditActiveJob = null;" in EDITOR
    assert "const executeQuickEdit = async (prompt, { forceAi = false, _noSelfCheck = false, _retried = false, signal = null } = {})" in EDITOR
    assert "const renderJobStack = ()" in EDITOR
    assert "const pumpQuickEdit = async ()" in EDITOR
    assert "const jobIcon = (status)" in EDITOR
    assert "status: 'queued'" in EDITOR
    assert "_quickEditActiveJob = job;" in EDITOR
    # the old single-flight lock is gone so prompts can be stacked
    assert "let busy" not in EDITOR
    assert "setBusy(" not in EDITOR
    # finished prompts fade out and auto-dismiss (no persistent history row)
    assert "const dismissJob = (job) =>" in EDITOR
    assert "setTimeout(() => dismissJob(job), job.status === 'error' ? 3200 : 1600);" in EDITOR
    assert "ge-ai-job-remove" not in EDITOR
    # queue styling ships with the editor stylesheet; the running row uses the
    # app's whirlpool spinner (canvas), not a CSS ring.
    assert ".ge-ai-command-stack" in STYLE
    assert "spinnerModule.createWhirlpool(16)" in EDITOR
    assert ".ge-ai-job-icon .ai-spinner-whirlpool" in STYLE
    assert ".ge-ai-job-spinner" not in STYLE
    assert "ge-ai-job-leaving" in STYLE
    assert "@keyframes ge-ai-job-in" in STYLE


def test_brush_preset_picker_is_visible_in_the_options_bar():
    # Painting tools surface the preset picker inline instead of hiding it
    # behind the "..." overflow button.
    assert "classList.toggle('ge-has-brush-presets', needsBrush)" in EDITOR
    # Descendant form: `ge-controls--in-options` sits on the parent
    # `.ge-controls` while `ge-has-brush-presets` is on the child
    # `#ge-brush-controls`, so both classes are never on the same element.
    assert ".ge-controls--in-options .ge-has-brush-presets .ge-brush-preset-row" in STYLE
    assert ".ge-controls--in-options.ge-has-brush-presets .ge-brush-preset-row" not in STYLE


def test_pixel_brush_shows_a_square_cursor():
    # The pixel brush draws hard squares, so the cursor preview is a square
    # snapped to whole pixels rather than a feathered circle.
    assert "const pixelBrush = !!state.brushPixel && (state.tool === 'brush' || state.tool === 'eraser');" in EDITOR
    assert "state.cursorEl.classList.toggle('ge-brush-cursor--pixel', pixelBrush);" in EDITOR
    assert ".ge-brush-cursor.ge-brush-cursor--pixel" in STYLE
    assert "border-radius: 0;" in STYLE


def test_quick_edit_is_local_first_and_only_uses_ai_when_needed():
    # LOCAL-FIRST: deterministic client ops run first, then the keyword
    # planner, and only generative / unrecognised prompts reach the server
    # planner (window.geEditorAgent) / AI model.
    assert "_shouldUseAgentEdit" not in EDITOR
    assert "const executeQuickEdit = async (prompt, { forceAi = false, _noSelfCheck = false, _retried = false, signal = null } = {})" in EDITOR
    assert "const _executeQuickEditLocal = async (prompt)" in EDITOR
    local_at = EDITOR.index("const _executeQuickEditLocal = async (prompt)")
    agent_at = EDITOR.index("const executeQuickEdit = async (prompt, { forceAi = false, _noSelfCheck = false, _retried = false, signal = null } = {})")
    assert local_at < agent_at  # deterministic helper defined first, then the wrapper
    wrapper = EDITOR[agent_at:EDITOR.index("// Stacked prompt queue", agent_at)]
    # The deterministic local op runs BEFORE the AI agent. Its contract is
    # "return false only when nothing matched" (deterministic branches bail with
    # a bare `return`, so undefined means handled).
    assert "const done = await _executeQuickEditLocal(prompt);" in wrapper
    assert "if (done !== false) return { ok: true };" in wrapper
    assert "agent.run(_enrichLookPrompt(prompt)" in wrapper
    assert wrapper.index("_executeQuickEditLocal(prompt)") < wrapper.index("agent.run(_enrichLookPrompt(prompt)")
    # The keyword planner is tried before the AI agent too, and is guarded
    # against faking generative requests locally.
    assert "const _runLocalPlan = async (prompt)" in EDITOR
    assert "const _looksGenerative = (text) => {" in EDITOR
    # The generative gate must catch "add <adjectives> <noun>" and scene changes
    # so they are never faked with a local adjustment.
    assert "const GEN_ADD_NOUNS = " in EDITOR
    assert r"add\\s+(?:a\\s+|an\\s+|the\\s+)?(?:\\w+\\s+){0,3}?" in EDITOR
    # Targeted recolours ("recolour ...", "make only ...", "change/turn ... <colour>")
    # must reach the model too, not a global tint.
    assert "const GEN_RECOLOR = " in EDITOR
    assert r"\brecolou?r\b" in EDITOR
    assert r"\bmake\s+only\b" in EDITOR
    # The deterministic fast path must bail out on those recolours instead of
    # satisfying them with a global local adjustment.
    assert "const _looksRecolor = (text) => {" in EDITOR
    assert "if (_looksRecolor(p)) return false;" in EDITOR
    assert "const planned = await _runLocalPlan(prompt);" in wrapper
    assert "if (tryLocal && !_looksGenerative(prompt)) {" in wrapper
    assert wrapper.index("_runLocalPlan(prompt)") < wrapper.index("agent.run(_enrichLookPrompt(prompt)")
    # Routing mode selector: auto / quick / agent / image, with a helper.
    assert "function _getQuickEditMode() {" in EDITOR
    assert "id=\"ge-ai-command-mode\"" in EDITOR
    assert "const mode = _getQuickEditMode();" in wrapper
    # Auto/Image/Tab fall through to the IMAGE MODEL, not a filter planner.
    assert "const _runImageModelEdit = async (prompt, { signal = null } = {}) => {" in EDITOR
    assert "await _runQuickControlOp('ai.generativeFill', { prompt });" in EDITOR
    assert "return await _runImageModelEdit(prompt, { signal });" in wrapper
    # Agent (planner) is used only in Agent mode.
    assert "const forceAgent = !forceAi && mode === 'agent';" in wrapper
    assert "if (forceAgent) {" in wrapper
    # Quick mode is deterministic only — never a model.
    assert "const quickOnly = !forceAi && mode === 'quick';" in wrapper
    assert "No built-in quick action matched" in wrapper
    # Named-look aliases map phrasings onto the server's recipe keywords so
    # "Blade Runner neon night" hits the cyberpunk instruction edit.
    assert "const _LOOK_ALIASES = [" in EDITOR
    assert "const _enrichLookPrompt = (text) => {" in EDITOR
    assert "'cyberpunk'" in EDITOR
    assert "agent.run(_enrichLookPrompt(prompt)," in EDITOR
    # A bare crop is deterministic (trim empty borders), never sent to the AI.
    assert "const b = _contentBounds();" in wrapper
    assert "'Nothing to trim — the image already fills the canvas.'" in wrapper
    assert "invoke('document.crop', { x: b.x, y: b.y, width: b.w, height: b.h })" in wrapper
    # Deterministic ops are skipped in agent/image/Tab mode.
    assert "const tryLocal = !forceAi && !forceAgent && mode !== 'image';" in wrapper
    assert "if (tryLocal && /\\b(?:crop|trim)\\b/i.test(cropText) && !hasRatio && !/\\bselection\\b|\\bselected\\b/i.test(cropText)) {" in wrapper


def test_tab_cycles_the_routing_mode():
    # Tab in the Quick Edit box cycles the routing mode (Auto/Quick/Agent/Image)
    # instead of sending the prompt; advertised as a tooltip.
    assert "if (e.key === 'Tab' && !e.shiftKey) {" in EDITOR
    assert "const opts = Array.from(ms.options).map((o) => o.value);" in EDITOR
    assert "ms.value = opts[(cur + 1) % opts.length];" in EDITOR
    assert "ms.dispatchEvent(new Event('change', { bubbles: true }));" in EDITOR
    assert 'title="Tip: press Tab to switch routing mode"' in EDITOR
    assert 'id="ge-ai-command-hint"' not in EDITOR


def test_image_ops_retry_transient_server_failures():
    # A transient 5xx/network hiccup from the image server must not silently
    # leave the canvas unchanged; retry a couple of times with backoff.
    assert "const _retryable = new Set([429, 500, 502, 503, 504]);" in EDITOR
    assert "const maxAttempts = 3;" in EDITOR
    assert "await new Promise((r) => setTimeout(r, 700 * attempt));" in EDITOR
    assert "if (!_retryable.has(res.status)) break;" in EDITOR


def test_quick_edit_lifts_above_the_mobile_keyboard():
    # Chat-style: the docked Quick Edit bar tracks the on-screen keyboard via
    # visualViewport, polling briefly after focus because iOS fires resize late.
    assert "const _liftQuickEdit = () => {" in EDITOR
    assert "const visibleBottom = vv ? (vv.offsetTop + vv.height) : window.innerHeight;" in EDITOR
    assert "wrap.style.setProperty('--ge-ai-lift', lift + 'px');" in EDITOR
    assert "wrap.classList.toggle('ge-ai-command-lifted', lift > 0);" in EDITOR
    assert "const _trackLift = () => {" in EDITOR
    assert "_liftPoller = setInterval(() => { _liftQuickEdit();" in EDITOR
    assert "input.addEventListener('focus', _trackLift);" in EDITOR


def test_deterministic_commands_never_reach_the_image_model():
    # resize / save-PSD / vignette / punchy / cinematic must run a local control
    # op, never the image model.
    assert "await _runQuickControlOp('document.resizeCanvas', { width: w, height: h });" in EDITOR
    assert "await _runQuickControlOp('document.savePsd', {});" in EDITOR
    assert "await _runQuickControlOp('filters.apply', { id: 'vignette' });" in EDITOR
    assert "Added punch." in EDITOR
    assert "Applied a cinematic grade." in EDITOR
    # "blur the background" is a masked Gaussian blur, never a scene replace.
    assert "await _runQuickControlOp('filters.applyMasked', { id: 'blur-gaussian', radius, invert: true });" in EDITOR
    assert "await _runQuickControlOp('selection.background', {});" in EDITOR
    # Tone adjectives are a deterministic grade, not a creative repaint.
    assert "|cinematic|dramatic|epic|dreamy|ethereal|moody|atmospheric|" not in EDITOR
    # The vague->image fallback must not swallow a prompt a local op can handle.
    assert "A prompt a deterministic control op can handle is never \"vague\"" in EDITOR


def test_quick_edit_has_a_harmonize_action_that_prompts_the_model():
    # A named "Harmonize" action blends every layer into one coherent image via
    # the image model, using a purpose-built prompt and running in every mode.
    assert "const _HARMONIZE_PROMPT = 'Blend all the composited elements into one coherent" in EDITOR
    assert "const _isHarmonizeIntent = (text) => {" in EDITOR
    assert "if (_isHarmonizeIntent(prompt)) {" in EDITOR
    assert "return await _runImageModelEdit(_HARMONIZE_PROMPT, { signal });" in EDITOR
    assert "insert: 'harmonize the layers'" in EDITOR


def test_ai_work_glows_the_canvas():
    assert "function _setCanvasAiBusy(busy)" in EDITOR
    assert "area.classList.toggle('ge-canvas-ai-busy', !!busy);" in EDITOR
    assert "_setCanvasAiBusy(kind === 'running');" in EDITOR
    # The skeleton covers the WHOLE canvas area (edge to edge), not just the
    # letterboxed image, so it reaches the sides instead of reading as a square.
    assert "function _aiBusyTargetRect()" in EDITOR
    assert "width: Math.max(1, area.clientWidth)," in EDITOR
    assert "height: Math.max(1, area.clientHeight)," in EDITOR
    assert "area.style.setProperty('--ge-ai-busy-w'" in EDITOR
    assert ".ge-canvas-area.ge-canvas-ai-busy::after" in STYLE
    assert "background: rgba(6, 14, 9, 0.38);" in STYLE
    assert "left: var(--ge-ai-busy-x, 0px);" in STYLE
    assert "width: var(--ge-ai-busy-w, 100%);" in STYLE
    assert "@keyframes ge-ai-canvas-sheen" in STYLE
    # The busy glow is an ASCII rendering of the fireflies drift (the yoyo
    # theme's ascii-fireflies look), scoped to the same busy rect: the field is
    # simulated offscreen then mapped to a glyph ramp.
    assert "function _aiFirefliesStart()" in EDITOR
    assert "function _aiFirefliesStop()" in EDITOR
    assert "el.className = 'ge-ai-ascii-fireflies';" in EDITOR
    assert "const ramp = ' .,:;irsXA253hMHGS#9B&@';" in EDITOR
    assert "st.el.textContent = lines.join('\\n');" in EDITOR
    assert "_aiFirefliesStart();" in EDITOR
    assert "_aiFirefliesStop();" in EDITOR
    assert ".ge-canvas-area .ge-ai-ascii-fireflies" in STYLE
    assert ".ge-canvas-area.ge-canvas-ai-busy .ge-ai-ascii-fireflies { opacity: 1; }" in STYLE
    # The skeleton is the photo turned into ASCII (transparent glyphs + a glow
    # sweep), stretched across the whole canvas area, not a simulated firefly
    # field.
    assert "sctx.drawImage(main, 0, 0, main.width, main.height, 0, 0, source.width, source.height);" in EDITOR
    assert "background: transparent !important;" in STYLE
    # The old canvas-wide pulse is gone.
    assert ".ge-canvas-area.ge-canvas-ai-busy .ge-main-canvas" not in STYLE
    assert "@keyframes ge-ai-canvas-pulse" not in STYLE


def test_footer_shows_active_edit_model():
    topbar = (ROOT / "static/js/editor/build/topbar.js").read_text()
    models = (ROOT / "static/js/editor/ai-models.js").read_text()
    assert 'id="ge-status-model"' in topbar
    assert "class=\"ge-status-model\"" in topbar
    assert ".ge-status-model" in STYLE
    assert "export function updateFooterEditModel()" in models
    assert "export function resolveQuickEditModel()" in models
    assert "const info = resolveQuickEditModel();" in models
    assert "el.textContent = `Edit: ${label}`;" in models
    # Wired both after load and on inpaint-select change.
    assert "updateFooterEditModel();" in models
    assert "aiInpaintSelect.addEventListener('change', () => updateFooterEditModel());" in models


def test_quick_edit_has_a_model_picker():
    models = (ROOT / "static/js/editor/ai-models.js").read_text()
    # The picker lives in the AI settings dialog, opened from the editor
    # footer (the Quick Edit box itself holds no model UI).
    assert 'id="ge-ai-command-model"' in AI_SETTINGS
    assert 'class="ge-ai-command-model"' in AI_SETTINGS
    assert "ge-ai-command-modelrow" not in EDITOR
    assert 'id="ge-ai-command-settings"' not in EDITOR
    assert "createAiSettings({ container })" in EDITOR
    assert "window.geEditorAiSettings = _aiSettings;" in EDITOR
    assert "aiBtn.id = 'ge-ai-settings-btn';" in EDITOR
    assert "aiBtn.addEventListener('click', () => _aiSettings?.open());" in EDITOR
    # The agent request carries the selected model.
    assert "function _getSelectedQuickEditModel()" in EDITOR
    assert "getModel: () => _getSelectedQuickEditModel()," in EDITOR
    # Populated from the provider list and persisted per user.
    assert "aiCommandSelect.innerHTML = '<option value=\"\">Auto (default)</option>';" in models
    assert "localStorage.setItem('ge-ai-command-model', aiCommandSelect.value)" in models
    assert "localStorage.getItem('ge-ai-command-model')" in models
    # Styled.
    assert ".ge-ai-settings-card" in STYLE
    assert ".ge-ai-command-model" in STYLE
    assert ".ge-ai-command-settings" not in STYLE


def test_quick_edit_has_an_agent_model_picker():
    models = (ROOT / "static/js/editor/ai-models.js").read_text()
    agent = (ROOT / "static/js/editor/editor-agent.js").read_text()
    # The agent (planner) picker sits in the AI settings dialog.
    assert 'id="ge-ai-command-agent-model"' in AI_SETTINGS
    assert "Recommended, not required" in AI_SETTINGS
    assert "function _getSelectedQuickEditAgentModel()" in EDITOR
    assert "getAgentModel: () => _getSelectedQuickEditAgentModel()," in EDITOR
    # Local Qwen3.5-9B VL is the default; server default is the alternative.
    assert "export function resolveQuickEditAgentModel()" in models
    assert "export const QUICK_EDIT_AGENT_OPTIONS" in models
    assert "Qwen3.5-9B VL · local" in models
    assert "http://100.69.120.65:8097/v1::Qwen/Qwen3.5-9B" in models
    assert "localStorage.setItem('ge-ai-command-agent-model', aiCommandAgentSelect.value)" in models
    assert "localStorage.getItem('ge-ai-command-agent-model')" in models
    # The request carries the agent model.
    assert "agent_model: (agentInfo && agentInfo.agent) || ''" in agent
    assert "getAgentModel = null," in agent


def test_quick_edit_hybrid_fast_path_handles_deterministic_intents_without_ai():
    # Deterministic intents bypass the LLM entirely (no model call) so they are
    # fast and reliable regardless of the planner's quality.
    assert "window.geQuickEditRun = (prompt, opts) => executeQuickEdit(prompt, opts || {});" in EDITOR
    # Selection commands.
    assert "await _runQuickControlOp('selection.selectAll', {});" in EDITOR
    assert "await _runQuickControlOp('selection.deselect', {});" in EDITOR
    assert "await _runQuickControlOp('selection.invert', {});" in EDITOR
    assert "await _runQuickControlOp('selection.subject', { label: 'person' });" in EDITOR
    # Fill / paint bucket (canvas-colour targeting + region selection fallback).
    assert "function _namedRgb(name)" in EDITOR
    assert "function _findColorPoint(layer, rgb, tol = 60)" in EDITOR
    assert "await _runQuickControlOp('fill.flood', { x: pt.x, y: pt.y, color, tolerance: 60 });" in EDITOR
    assert "await _runQuickControlOp('selection.fill', { color });" in EDITOR
    # "fill the canvas with <colour>" fills the WHOLE canvas (select all -> fill),
    # not just the connected region around the centre — but NOT when the request
    # is a generative extend/expand ("extend the canvas to 16:9 with generative fill").
    assert "const _fillIsGenerative = /\\b(?:extend|expand|generative|genfill|outpaint|aspect|ratio)\\b|\\d+\\s*:\\s*\\d+/.test(p);" in EDITOR
    assert "if (!_fillIsGenerative && (/\\bfill\\b|\\bflood\\s*fill\\b|\\bpaint\\s*bucket\\b/.test(p)) && /\\b(?:canvas|whole|entire|everything|all|layer)\\b/.test(p)) {" in EDITOR
    assert "const fillAllColor = fillWith ? _colorForName(fillWith[1]) : (_fillFgBg || state.color || '#000000');" in EDITOR
    assert "_setAiCommandStatus('Filled the canvas.', 'done');" in EDITOR
    assert "label: 'Fill canvas'" in EDITOR
    # Align + text edit.
    assert "await _runQuickControlOp('layers.align', { mode });" in EDITOR
    assert "await _runQuickControlOp('text.edit', tp);" in EDITOR
    # The command-box queue runs the same hybrid entry point.
    assert "await executeQuickEdit(job.prompt, { forceAi: !!job.forceAi, signal: controller ? controller.signal : null });" in EDITOR
    # Portrait attributes take dedicated, observable ops instead of the blanket
    # "enhance face" AI pass.
    assert "await _runQuickControlOp('retouch.teeth', {});" in EDITOR
    assert "await _runQuickControlOp('selection.samText', { text: 'eyes' });" in EDITOR
    assert "await _runQuickControlOp('adjustments.applyMasked', { brightness: 18, contrast: 8 });" in EDITOR
    assert "const _portraitAttr = /" in EDITOR
    assert "if (/\\b(face|portrait|selfie)\\b/.test(p) && !_portraitAttr) {" in EDITOR
    assert "blemishes?|acne|pores|blackheads?" in EDITOR
    # Tone branches must not swallow generative prompts (e.g. "replace the
    # background ... and make it cooler", "remove the person ... warm up").
    assert "if (/\\b(warm|warmer|warmth)\\b/.test(p) && !_looksGenerative(p)) {" in EDITOR
    assert "if (/\\b(cool|cooler|colder|moody|teal)\\b/.test(p) && !_looksGenerative(p)) {" in EDITOR
    assert "if (/\\b(brighten|brighter|lighten|exposure|too\\s+dark|backlit|well[-\\s]*lit)\\b/.test(p) && !_looksGenerative(p)) {" in EDITOR
    assert "if (/\\b(darken|darker|dimmer|reduce\\s+exposure|too\\s+bright)\\b/.test(p) && !_looksGenerative(p)) {" in EDITOR



def test_align_does_not_swallow_add_text_requests():
    editor = (ROOT / "static/js/galleryEditor.js").read_text()
    # "add a subtitle ... and align it" must add the text, not just align.
    assert "const alignWantsText = " in editor
    assert "if (/\\balign\\b/.test(p) && alignM && !alignWantsText) {" in editor
    nl = (ROOT / "static/js/editor/nl-edit.js").read_text()
    # add-text wins over effect/align words; only real text-edit attributes exclude it.
    assert "const existingText = " in nl
    assert "if (wantsAddText && !existingText && !/\\b(font|bigger|smaller|larger|colour|color)\\b/.test(p)) {" in nl
    assert "|| /\\bwith\\s+the\\s+(?:text|title|word|caption|label)\\b/i.test(prompt)" in nl


def test_quick_edit_vl_self_check_retries_once():
    # Generative and "look" edits get a VL self-check: the agent VL compares
    # BEFORE/AFTER and may request a single retry.
    assert "const _looksLook = (text) =>" in EDITOR
    assert "const _captureShot = async () => {" in EDITOR
    assert "const _vlJudgeAfter = async (prompt, before, after) => {" in EDITOR
    assert "const selfCheckEligible = !_noSelfCheck" in EDITOR
    assert "&& (_looksGenerative(prompt) || _looksLook(prompt));" in EDITOR
    assert "if (selfCheckEligible && !_retried) {" in EDITOR
    assert "const retryOut = await executeQuickEdit(judge.retryPrompt || prompt, { forceAi: true, _noSelfCheck: true, _retried: true, signal });" in EDITOR
    # A retry that does not succeed keeps the first accepted attempt (ok).
    assert "if (retryOut && retryOut.ok) return retryOut;" in EDITOR
    # The judge talks to the agent VL (OpenAI-compatible chat/completions).
    assert "ep.replace(/\\/$/, '') + '/chat/completions'" in EDITOR
    assert "Reply with EXACTLY ONE word: \"FAIL\"" in EDITOR
    # It prefers a same-origin proxy (the VL server has no CORS headers) and
    # falls back to the direct endpoint.
    assert "fetch('/api/editor/self-check'" in EDITOR
    assert "const parseJudge = (txt) => {" in EDITOR


def test_self_check_is_off_by_default_with_an_opt_in_toggle():
    # Self-check is opt-in: off unless the user enables "Double-check AI edits".
    assert "window.geQuickEditSelfCheck === true" in EDITOR
    # The toggle moved into the AI settings dialog.
    assert 'id="ge-ai-command-selfcheck"' in AI_SETTINGS
    assert "Double-check AI edits" in AI_SETTINGS
    assert "localStorage.getItem('ge-ai-self-check') === '1'" in EDITOR
    assert "localStorage.setItem('ge-ai-self-check', selfCheckToggle.checked ? '1' : '0')" in EDITOR


def test_remove_background_chains_a_tone_step():
    # "remove the background and make it brighter" must do both, not drop the tone.
    assert "await _runQuickControlOp('ai.removeBackground', {});" in EDITOR
    assert "await _runQuickControlOp('adjustments.apply', { type: 'brightness-contrast', params: { brightness: 16, contrast: 4 } });" in EDITOR
    assert "await _runQuickControlOp('adjustments.apply', { type: 'white-balance', params: { temperature: 18 } });" in EDITOR


def test_remove_background_intent_matches_common_phrasings():
    # The bg-removal fast path must catch "removal" (not just "remove"),
    # "bg remov", "remove bg", "no background", "cutout", "isolate the
    # subject" etc. — otherwise these fall through to the AI agent.
    assert "const _bgRemove = " in EDITOR
    assert "remov\\w*|erase|delete|drop|get\\s*rid\\s*of|cut\\s*out|cutout|knock\\s*out|isolate|strip|clear|transparent|no|without|rm" in EDITOR
    assert "if (_bgRemove) {" in EDITOR


def test_remove_background_mask_is_a_layer_mask():
    # The removal cutout must be a real LAYER mask (applied with destination-in),
    # not a selection overlay — otherwise the background is never removed.
    assert "name: 'Background removed', canvas: cut, ctx: cctx, visible: true, mode: 'layer', space: 'layer', linked: true, offset: { x: 0, y: 0 } }" in EDITOR
    assert "mode: 'selection' }" not in EDITOR


def test_masked_adjust_supports_hue_rotation():
    # adjustments.applyMasked must accept a `hue` (degrees) that rotates hue in
    # HSL, preserving lightness/saturation — so a shaded object can be recoloured
    # (red car -> blue) instead of flattened to a fill.
    assert "const hue = Number(opts.hue) || 0;" in EDITOR
    assert "hh = (hh + (hue * rotWeight) / 360) % 1;" in EDITOR
    assert "hue rotation in degrees (0-360) that preserves lightness and saturation" in EDITOR
    # The rotation is limited to pixels near the source hue (soft falloff) and
    # gated by saturation, so windows/chrome keep their colour.
    assert "let hueCenter = null;" in EDITOR
    assert "const satW = Math.max(0, Math.min(1, (s - 0.12) / 0.18));" in EDITOR
    assert "const inner = 0.06, outer = 0.17;" in EDITOR
    assert "sourceHue" in EDITOR


def test_sam_text_falls_back_to_color_range_when_empty():
    # When SAM returns an empty mask, selection.samText falls back to a
    # colour-range selection if the caller supplies fallbackColor.
    assert "selection.samText': { description:" in EDITOR
    assert "fallbackColor" in EDITOR
    assert "if (empty && p.fallbackColor) {" in EDITOR
    assert "Color range selection (SAM fallback)" in EDITOR
    assert "_colorRangeMask(src.data, src.w, src.h, target, fuzz)" in EDITOR


def test_masked_hue_saturation_mode_uses_delta_saturation():
    # HARNESS sends {hue:<delta>, saturation:0, mode:'hue-saturation'} for a
    # recolour; saturation must be read as a delta (0 = unchanged), not a factor
    # (0 = grey), so the hue-rotated object keeps its colour.
    assert "if (opts.mode === 'hue-saturation') saturation = 1 + saturation / 100;" in EDITOR


def test_quick_edit_handles_undo_and_redo_locally():
    # "undo"/"redo" are deterministic actions, never a model call.
    assert "await _runQuickControlOp('history.redo', {});" in EDITOR
    assert "await _runQuickControlOp('history.undo', {});" in EDITOR
    assert "label: 'Undo'" in EDITOR
    # Completion feedback is silenced (no lingering "Done." text).
    assert "Agent applied" not in EDITOR
    assert "_setAiCommandStatus('Done.', 'done');" not in EDITOR
    assert EDITOR.count("_setAiCommandStatus('', '');") >= 2


def test_quick_edit_lists_every_tool_with_its_icon():
    # Every toolbar tool is offered in Quick Edit, carrying its SVG icon.
    assert "const _QUICK_EDIT_TOOL_SUGGESTIONS = Object.entries(_TOOLS).map(([id, t]) => ({" in EDITOR
    assert "const _QUICK_EDIT_CATALOG = [..._QUICK_EDIT_TOOL_SUGGESTIONS, ..._AI_COMMAND_SUGGESTIONS];" in EDITOR
    assert "toolId: id," in EDITOR
    assert "icon: t.icon || ''," in EDITOR
    assert "if (!q) return [];" in EDITOR
    # The suggestion row renders the icon and selecting a tool switches tools.
    assert '<span class="ge-ai-command-suggestion-icon" aria-hidden="true">${item.icon || \'\'}</span>' in EDITOR
    assert "if (item.toolId) {" in EDITOR
    assert "_clickToolButton(item.toolId);" in EDITOR
    # A deterministic tool-switch branch handles "use the eraser tool".
    assert "function _matchTool(text) {" in EDITOR
    assert "const toolId = _matchTool(p);" in EDITOR
    assert "_clickToolButton(toolId);" in EDITOR
    style = (ROOT / "static/css/13-image-editor.css").read_text(encoding="utf-8")
    assert ".ge-ai-command-suggestion-icon" in style
    assert "grid-template-columns: 22px 38px minmax(82px, auto) minmax(0, 1fr);" in style


def test_quick_edit_can_cancel_an_in_flight_ai_request():
    # A running Quick Edit job shows a Cancel button that aborts the request and
    # clears the row + status + canvas glow.
    assert 'class="ge-ai-job-cancel"' in EDITOR
    assert "const cancelJob = (job) => {" in EDITOR
    assert "job.cancelled = true;" in EDITOR
    assert "job.controller?.abort();" in EDITOR
    assert "_setAiCommandStatus('', '');" in EDITOR
    # A cancelled request must not fall through and silently re-run on the
    # client planner.
    assert "if (signal && signal.aborted) {" in EDITOR
    assert "return { ok: false, cancelled: true };" in EDITOR
    # Each job owns an AbortController threaded through executeQuickEdit/agent.run.
    assert "const controller = (typeof AbortController === 'function') ? new AbortController() : null;" in EDITOR
    assert "signal: controller ? controller.signal : null" in EDITOR
    assert "if (job.cancelled) continue;" in EDITOR
    # The agent forwards the abort signal to fetch.
    assert "async function run(prompt, { onStep: stepCb = null, signal = null } = {})" in AGENT
    assert "signal: signal || undefined," in AGENT


def test_quick_edit_repaints_creative_prompts_with_the_model():
    # Creative / artistic intent must repaint the image (select-all + generative
    # fill), not apply a generic vibrance/brightness tweak.
    assert "const _CREATIVE_STYLES = " in EDITOR
    assert "const _looksCreative = (text) =>" in EDITOR
    assert "cyberpunk|vaporwave|synthwave" in EDITOR
    assert "if (_looksCreative(p) && !/\\b(?:remove|erase|delete|replace)\\b/.test(p)) {" in EDITOR
    assert "await _runQuickControlOp('selection.selectAll', {});" in EDITOR
    assert "await _runQuickControlOp('ai.generativeFill', { prompt });" in EDITOR
    assert "await _runQuickControlOp('selection.deselect', {});" in EDITOR


def test_quick_edit_falls_back_to_the_image_model_for_vague_prompts():
    assert "const _VAGUE_PATTERNS = " in EDITOR
    assert "const _isVague = (text) => {" in EDITOR
    # A recognized generative prompt ("make it look snowy") must NOT be treated
    # as a vague look just because it contains the word "look".
    assert "if (_looksLook(s) && !_NAMED_LOOK.test(s) && !_looksGenerative(s)) return true;" in EDITOR
    # A vague/unrecognised prompt is handed to the image model, not dead-ended.
    assert "if (mode === 'auto' && !forceAi && _isVague(prompt)) {" in EDITOR
    assert "Interpreting with the image model" in EDITOR
    assert "return await _runImageModelEdit(prompt, { signal });" in EDITOR


def test_auto_scopes_generative_edits_to_named_regions():
    # In Auto mode a generative prompt that names a region (sky/clouds/moon/
    # background) selects that region first, so the image model only repaints
    # that area (a whole-frame unmasked edit was the commonest weak case).
    assert "const _regionSelectionOp = (text) => {" in EDITOR
    assert "return 'selection.sky';" in EDITOR
    assert "return 'selection.background';" in EDITOR
    assert "const regionOp = _regionSelectionOp(prompt);" in EDITOR
    assert "scoped = !!(state.wandMask && state.wandMask.width && state.wandMask.height);" in EDITOR
    assert "if (!scoped) await _runQuickControlOp('selection.selectAll', {});" in EDITOR
    # Generative prompts skip the client keyword planner (so a sky/foreground
    # "add X" reaches the model, not a local region toning).
    assert "if (tryLocal && !_looksGenerative(prompt)) {" in EDITOR
    # Extend/outpaint to a ratio grows the canvas and generative-fills the border.
    assert "const ratioAsk = prompt.match(" in EDITOR
    assert "await _runQuickControlOp('ai.outpaint', { prompt, ratio: `${ratioAsk[1]}:${ratioAsk[2]}`, size: `${tw}x${th}`, steps: 4 });" in EDITOR
    # The dedicated server outpaint pipeline (pad + strip inpaint + tone match).
    assert "const _aiOutpaint = async (prompt, opts = {}) => {" in EDITOR
    assert "await _aiEditCanvas('/api/image/outpaint', payload, { keepNaturalSize: true });" in EDITOR
    assert "const { canvas } = await _aiEditCanvas('/api/image/outpaint', payload, { keepNaturalSize: true });" in EDITOR
    assert "'ai.outpaint': { description:" in EDITOR


def test_background_replacement_uses_the_dedicated_server_pipeline():
    # 'put her on a beach' / 'replace the background with mountains' route to the
    # dedicated replace-background endpoint (server derives the subject mask,
    # repaints the inverse, harmonises + contact shadow, pastes the subject back)
    # instead of a masked generative fill.
    assert "const _aiReplaceBackground = async (prompt) => {" in EDITOR
    assert "await _aiEditCanvas('/api/image/replace-background', {" in EDITOR
    assert "const { canvas } = await _aiEditCanvas('/api/image/replace-background', {" in EDITOR
    assert "_placeResultLayer(canvas, 'Background replaced')" in EDITOR
    assert "'ai.replaceBackground': { description:" in EDITOR
    assert "run: (p) => _aiReplaceBackground((p && p.prompt) || '')" in EDITOR
    assert "if (regionOp === 'selection.background') {" in EDITOR
    assert "await _runQuickControlOp('ai.replaceBackground', { prompt, steps: 4 });" in EDITOR


def test_quick_edit_can_ai_fill_the_empty_canvas():
    # "ai fill" / "fill the empty areas" / "complete the canvas" builds a mask
    # from the transparent pixels and inpaints them with the image model.
    assert "const _aiFillEmpty = async (prompt) => {" in EDITOR
    assert "const on = data[i + 3] < 16 ? 255 : 0;" in EDITOR
    assert "'the canvas has no empty areas to fill'" in EDITOR
    assert "state.wandMask = mask;" in EDITOR
    assert "await _aiInpaintSelection(prompt || 'continue the surrounding scene" in EDITOR
    assert "'ai.fillEmpty': { description:" in EDITOR
    assert "label: 'AI fill'" in EDITOR
    assert "await _runQuickControlOp('ai.fillEmpty'," in EDITOR
    # An extend/resize prompt ("extend to 16:9 with generative fill") must NOT
    # be swallowed by the AI-fill branch; it falls through to the extend logic.
    assert "const _extendAsk = " in EDITOR
    assert "\\boutpaint\\b/i.test(p) && !_extendAsk && !_fillRegion)" in EDITOR


def test_quick_edit_shows_a_real_progress_bar():
    # The Quick Edit box carries a progress bar (kept alongside the skeleton).
    assert 'id="ge-ai-command-progress"' in EDITOR
    assert 'id="ge-ai-command-progress-fill"' in EDITOR
    assert 'id="ge-ai-command-progress-label"' in EDITOR
    # Poller helpers and the poll itself.
    assert "function _startAiProgress(requestId)" in EDITOR
    assert "function _stopAiProgress()" in EDITOR
    assert "function _setAiProgress(frac, label)" in EDITOR
    assert "fetch(`/api/image/edit-progress?request_id=${encodeURIComponent(requestId)}`" in EDITOR
    # The edit request carries the request_id the server keys progress on.
    assert "const requestId = 'qe-' + Date.now().toString(36)" in EDITOR
    assert "const body = JSON.stringify({ image, request_id: requestId, ...payload });" in EDITOR
    assert "_startAiProgress(requestId);" in EDITOR
    assert "_stopAiProgress();" in EDITOR
    # Styling exists.
    assert ".ge-ai-command-progress {" in STYLE
    assert ".ge-ai-command-progress-fill {" in STYLE


def test_rulers_default_off_on_phones():
    # Audit 2026-10-11 #6: the rulers eat scarce canvas width on a phone, so
    # the editor opens with them hidden (View > Rulers can still enable them).
    assert "window.matchMedia('(max-width: 700px)').matches" in EDITOR
    assert "state.rulersVisible = false;" in EDITOR


def test_generation_503_is_handled_gracefully():
    # Image generation (/v1/images/generations) can be disabled server-side
    # (returns 503). Surface a clear message and do not retry immediately.
    assert "if (status === 503) return _GEN_DISABLED_MSG;" in EDITOR
    assert "_GEN_DISABLED_MSG = 'Image generation is switched off on the server at the moment.'" in EDITOR
    assert "window.__geGenerationDisabled" in EDITOR
