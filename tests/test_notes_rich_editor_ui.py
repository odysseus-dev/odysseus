from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NOTES_JS = (ROOT / "static" / "js" / "notes.js").read_text(encoding="utf-8")
STYLE_CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
SW_JS = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")


def test_plain_notes_use_rich_editor_and_serializes_visual_formatting():
    assert "import markdownModule from './markdown.js';" in NOTES_JS
    assert "function _buildRichNoteEditorHtml" in NOTES_JS
    assert "? _buildRichNoteEditorHtml(note?.content || '')" in NOTES_JS
    assert "bodyEl.innerHTML = _buildRichNoteEditorHtml(text);" in NOTES_JS
    assert "function _noteRichExtractFormat" in NOTES_JS
    assert "function _noteRichSerializeEditor" in NOTES_JS
    assert 'data-note-rich-editor ${_noteRichFormatAttrs(format)}' in NOTES_JS
    assert "payload.content = _noteRichSerializeEditor(form.querySelector('.note-rich-editor'))" in NOTES_JS
    assert "d.content = _noteRichSerializeEditor(form.querySelector('.note-rich-editor'))" in NOTES_JS


def test_notes_rich_editor_has_word_style_tools_and_preview():
    assert 'data-rich-font aria-label="Font"' in NOTES_JS
    assert 'data-rich-size aria-label="Font size"' in NOTES_JS
    assert 'data-rich-color aria-label="Text color"' in NOTES_JS
    assert "function _noteRichApplyInlineToken" in NOTES_JS
    assert "['font', 'size', 'color', 'align'].includes(kind)" in NOTES_JS
    assert "_noteRichSetFormat(editor, kind, value);" in NOTES_JS
    assert "_noteRichApplyInlineToken(ta, 'font'" in NOTES_JS
    assert "_noteRichApplyInlineToken(ta, 'size'" in NOTES_JS
    assert "_noteRichApplyInlineToken(ta, 'color'" in NOTES_JS
    assert "_noteRichApplyInlineToken(ta, 'align'" in NOTES_JS
    assert "note-rich-font-${font}" in NOTES_JS
    assert "note-rich-size-${size}" in NOTES_JS
    assert "note-rich-color-${color}" in NOTES_JS
    assert "note-rich-align-${align}" in NOTES_JS
    for action in [
        "bold",
        "italic",
        "underline",
        "strike",
        "highlight",
        "bullet",
        "numbered",
        "checklist",
        "quote",
        "indent",
        "outdent",
        "table",
        "divider",
        "date",
        "clear",
        "preview",
        "align-left",
        "align-center",
        "align-right",
        "align-justify",
        "superscript",
        "subscript",
        "upper",
        "lower",
        "titlecase",
        "sort-lines",
    ]:
        assert f"_noteRichTool('{action}'" in NOTES_JS
    assert "function _noteRichApplyStyle" in NOTES_JS
    assert "function _noteRichSetPreview" in NOTES_JS
    assert "reader.innerHTML = _renderNoteMarkdown(note.content || '');" in NOTES_JS


def test_notes_rich_formatting_renders_outside_the_editor():
    assert "function _noteRichPlainText" in NOTES_JS
    assert "function _renderNoteRichPreview" in NOTES_JS
    assert "const previewHtml = _renderNoteRichPreview(note.content || '', 300);" in NOTES_JS
    assert '<div class="note-goal-desc">${previewHtml}</div>' in NOTES_JS
    assert "const previewHtml = _renderNoteRichPreview(note.content || '', 600);" in NOTES_JS
    assert '<div class="note-content-preview">${previewHtml}</div>' in NOTES_JS
    assert "_noteRichPlainText(n.content || '').toLowerCase().includes(q)" in NOTES_JS
    assert "rawBody = _noteRichPlainText(note.content || '').slice(0, 400);" in NOTES_JS
    assert "const plainContent = _noteRichPlainText(note.content || '').trim();" in NOTES_JS
    assert ".note-content-preview p," in STYLE_CSS
    assert ".note-goal-desc p" in STYLE_CSS


def test_notes_rich_editor_matches_theme_and_fullscreen_layout():
    assert ".note-rich-editor {" in STYLE_CSS
    assert "background: color-mix(in srgb, var(--panel) 72%, var(--bg));" in STYLE_CSS
    assert "border: 1px solid color-mix(in srgb, var(--fg) 11%, var(--border));" in STYLE_CSS
    assert ".note-rich-ribbon {" in STYLE_CSS
    assert ".note-rich-font {" in STYLE_CSS
    assert ".note-rich-size {" in STYLE_CSS
    assert ".note-rich-color {" in STYLE_CSS
    assert ".note-rich-desktop-only" in STYLE_CSS
    assert ".note-rich-font-serif" in STYLE_CSS
    assert ".note-rich-size-xxl" in STYLE_CSS
    assert ".note-rich-color-accent" in STYLE_CSS
    assert ".note-rich-align-center" in STYLE_CSS
    assert '.note-rich-editor[data-rich-font="serif"]' in STYLE_CSS
    assert '.note-rich-editor[data-rich-size="lg"]' in STYLE_CSS
    assert "font-size: calc(var(--note-rich-editor-base-size) * var(--note-rich-editor-scale));" in STYLE_CSS
    assert "overflow-x: auto;" in STYLE_CSS
    assert ".note-fullscreen-body .note-form.note-color-green" in STYLE_CSS
    assert "background: transparent !important;" in STYLE_CSS
    assert ".note-fullscreen-overlay .note-rich-editor {" in STYLE_CSS
    assert ".note-fullscreen-overlay .note-rich-editor .note-form-content" in STYLE_CSS


def test_notes_rich_editor_android_cache_bumped():
    assert "const CACHE_NAME = 'odysseus-v388';" in SW_JS
