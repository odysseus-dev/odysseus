from pathlib import Path
from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parents[1]
EDITOR = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
STYLE = app_css()


def test_quick_edit_has_persistent_expanded_identity_and_labeled_input():
    assert 'class="ge-ai-command-head"' in EDITOR
    assert 'class="ge-ai-command-title">Quick Edit</span>' in EDITOR
    assert 'aria-label="Describe the image edit"' in EDITOR
    assert 'placeholder="Describe an edit, or choose an action"' in EDITOR
    assert ".ge-ai-command-head" in STYLE


def test_quick_edit_exposes_initial_actions_without_running_them_on_click():
    assert "if (!q) return [];" in EDITOR
    assert ".slice(0, 3)" in EDITOR
    click_handler = EDITOR[
        EDITOR.index("suggestions?.addEventListener('click'") :
        EDITOR.index("const setOpen", EDITOR.index("suggestions?.addEventListener('click'"))
    ]
    assert "pickSuggestion(Number(btn.dataset.aiCommandSuggestion), false);" in click_handler


def test_quick_edit_has_clear_busy_and_action_type_states():
    assert 'id="ge-ai-command-clear"' in EDITOR
    assert "let busy = false;" in EDITOR
    assert "if (busy) return;" in EDITOR
    assert "_waitForExistingButton" in EDITOR
    assert "class=\"ge-ai-command-suggestion-kind\"" in EDITOR
    assert ".ge-ai-command-clear" in STYLE
    assert ".ge-ai-command-busy" in STYLE


def test_quick_edit_owns_escape_without_closing_the_editor():
    assert "ge-ai-command-close" in EDITOR
    assert "quickEdit.dispatchEvent(new CustomEvent('ge-ai-command-close'))" in EDITOR
    assert "top: -2px;" in STYLE[STYLE.index(".ge-ai-command-clear"):STYLE.index(".ge-ai-command-run")]


def test_quick_edit_transforms_do_not_add_a_second_undo_snapshot():
    start = EDITOR.index("if (/\\brotate\\b.*\\b180\\b")
    end = EDITOR.index("if (/\\b(remove|erase", start)
    transform_block = EDITOR[start:end]
    assert "_saveState('Rotate" not in transform_block
    assert "_saveState('Flip" not in transform_block
