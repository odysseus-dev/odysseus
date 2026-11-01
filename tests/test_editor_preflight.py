from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel):
    return (ROOT / rel).read_text()


def test_preflight_module_has_the_four_setup_steps():
    src = _read("static/js/editor/preflight.js")
    assert "export function createPreflight(" in src
    assert "export const PREFLIGHT_STEPS" in src
    # The four steps the user asked for.
    assert "id: 'provider'" in src
    assert "id: 'editModel'" in src
    assert "id: 'agentModel'" in src
    assert "id: 'bgRemover'" in src
    # Automatic detection + manual override.
    assert "getAIModelsStatus()" in src
    assert "resolveQuickEditModel()" in src
    assert "resolveQuickEditAgentModel()" in src
    assert "ge-preflight-done" in src  # manual override store
    assert "ge-preflight-dismissed" in src  # don't-show-on-open store
    # The agent model is optional (encouraged, not required).
    assert "optional: true" in src
    # The model steps link straight to the AI settings dialog.
    assert "window.geEditorAiSettings?.open('ge-ai-command-model')" in src
    assert "window.geEditorAiSettings?.open('ge-ai-command-agent-model')" in src
    # A done row no longer offers an action ("why does it say Retry?").
    assert "const actions = done ? '' :" in src
    assert "required ready" in src


def test_preflight_is_wired_into_the_editor():
    editor = _read("static/js/galleryEditor.js")
    assert "import { createPreflight } from './editor/preflight.js';" in editor
    assert "createPreflight({ canvasArea })" in editor
    assert "_preflight?.maybeOpen();" in editor
    assert "setupBtn.id = 'ge-setup-btn';" in editor
    assert "_preflight?.destroy();" in editor


def test_ai_models_exposes_status_for_the_checklist():
    src = _read("static/js/editor/ai-models.js")
    assert "export function getAIModelsStatus()" in src
    assert "_aiModelsStatus = { ready: true, models: loaded" in src
    assert "_aiModelsStatus = { ready: false, models: [], error:" in src
    assert "ge:ai-models-status" in src


def test_preflight_is_precached_and_styled():
    sw = _read("static/sw.js")
    assert "'/static/js/editor/preflight.js'" in sw
    assert "'/static/js/editor/ai-settings.js'" in sw
    css = _read("static/css/13-image-editor.css")
    assert ".ge-preflight-card" in css
    assert ".ge-preflight-row.is-done" in css
    assert ".ge-preflight-row.is-todo" in css
    assert ".ge-preflight-row.is-optional" in css
    assert ".ge-setup-btn" in css


def test_ai_settings_dialog_is_wired_and_precached():
    editor = _read("static/js/galleryEditor.js")
    settings = _read("static/js/editor/ai-settings.js")
    assert "import { createAiSettings } from './editor/ai-settings.js';" in editor
    assert "createAiSettings({ container })" in editor
    assert "window.geEditorAiSettings = _aiSettings;" in editor
    assert "_aiSettings?.destroy();" in editor
    assert "export function createAiSettings(" in settings
    assert 'id="ge-ai-command-model"' in settings
    assert 'id="ge-ai-command-agent-model"' in settings
    assert 'id="ge-ai-command-selfcheck"' in settings
    assert "Recommended, not required" in settings
