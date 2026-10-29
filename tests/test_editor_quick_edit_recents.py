from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EDITOR = ROOT / "static/js/galleryEditor.js"


def test_recent_prompt_helpers_exist():
    src = EDITOR.read_text(encoding="utf-8")
    assert "_AI_RECENT_KEY = 'ge-ai-command-recent'" in src
    assert "const _getRecentPrompts = () => {" in src
    assert "const _pushRecentPrompt = (prompt) => {" in src
    # Most-recent-first, de-duplicated (case-insensitive), capped.
    assert "_AI_RECENT_MAX = 8" in src
    assert "v.toLowerCase() !== p.toLowerCase()" in src
    assert "list.unshift(p);" in src
    assert "list.slice(0, _AI_RECENT_MAX)" in src


def test_recent_prompts_surface_as_suggestions_when_input_empty():
    src = EDITOR.read_text(encoding="utf-8")
    assert "if (!input.value.trim()) {" in src
    assert "const recents = _getRecentPrompts();" in src
    assert "recents.length" in src
    assert "recents.map((p) => ({ label: p, insert: p, hint: 'Recent prompt', kind: 'Recent' }))" in src


def test_example_prompts_offer_starters_when_no_recents():
    src = EDITOR.read_text(encoding="utf-8")
    assert "const _AI_EXAMPLE_PROMPTS = [" in src
    # A handful of concrete starters that map to real routes.
    assert src.count("hint: '") >= 6
    assert "insert: 'remove background'" in src
    assert "insert: 'make it cinematic'" in src
    # Empty box + no history -> Example chips.
    assert ": _AI_EXAMPLE_PROMPTS.map((e) => ({ label: e.label, insert: e.insert, hint: e.hint, kind: 'Example' }))" in src


def test_submitting_a_prompt_records_it_as_recent():
    src = EDITOR.read_text(encoding="utf-8")
    assert "_pushRecentPrompt(input.value);" in src
    # It must be recorded right before the job is enqueued.
    idx = src.index("form.addEventListener('submit'")
    block = src[idx:idx + 400]
    assert "_pushRecentPrompt(input.value);" in block
    assert "enqueueQuickEditJob(input.value, false);" in block
