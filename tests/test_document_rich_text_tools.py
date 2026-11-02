"""Regression guards for the Rich Text document editor toolset."""

from pathlib import Path

from tests.helpers.stylesheets import app_css
from tests.helpers.document_source import document_source, function_body


ROOT = Path(__file__).resolve().parents[1]
DOC_JS = document_source()
STYLE = app_css()


def test_rich_text_toolbar_exposes_document_formatting_controls():
    for control in (
        'data-dd="font"',
        'data-dd="textsize"',
        'data-dd="align"',
        'data-dd="color"',
        'data-dd="highlight"',
        'data-md="superscript"',
        'data-md="subscript"',
        'data-dd="table"',
        'data-md="unlink"',
        'data-md="removeformat"',
        'data-dd="spacing"',
    ):
        assert control in DOC_JS


def test_heading_menu_includes_normal_text_and_all_heading_levels():
    dropdown = DOC_JS.split("function _showMdDropdown", 1)[1].split(
        "function initMdToolbar", 1
    )[0]

    assert "['paragraph', 'Paragraph', 'P']" in dropdown
    for level in range(1, 7):
        assert f"['h{level}', 'Heading {level}', 'H{level}']" in dropdown
    assert "document.execCommand('formatBlock', false, 'p')" in DOC_JS
    assert "/^h[1-6]$/.test(action)" in DOC_JS
    assert "h4: '#### '" in DOC_JS
    assert ".doc-email-richbody h4" in STYLE


def test_line_spacing_menu_exposes_common_document_intervals():
    for action in (
        "linespacing:normal",
        "linespacing:1",
        "linespacing:1.15",
        "linespacing:1.5",
        "linespacing:2",
    ):
        assert action in DOC_JS


def test_line_spacing_preserves_selection_and_uses_one_undoable_snapshot():
    spacing = DOC_JS.split("const _richSpacingBlockSelector", 1)[1].split(
        "function _focusRichTextOffset", 1
    )[0]

    assert "function _richSelectionTextOffsets(root)" in spacing
    assert "function _restoreRichSelectionTextOffsets(root, offsets)" in spacing
    assert "function _richClosestSpacingBlock(root, node)" in spacing
    assert "function _richSelectedSpacingBlockIndexes(root)" in spacing
    assert "document.createTreeWalker(root, NodeFilter.SHOW_TEXT)" in spacing
    assert "const clone = rich.cloneNode(true)" in spacing
    assert "block.style.lineHeight = value" in spacing
    assert "block.style.removeProperty('line-height')" in spacing
    assert "document.execCommand('insertHTML', false, clone.innerHTML)" in spacing
    assert "_restoreRichSelectionTextOffsets(rich, offsets)" in spacing


def test_rich_text_commands_sync_and_schedule_save():
    apply_format = DOC_JS.split("function applyMdFormat(action)", 1)[1]
    command_branch = apply_format.split("const _rich = _emailRichbodyActive();", 1)[1].split(
        "const ta = document.getElementById('doc-editor-textarea');", 1
    )[0]

    assert "fontName" in command_branch
    assert "fontSize" in command_branch
    assert "_applyRichTableAction(_rich, action)" in command_branch
    assert "document.execCommand('insertHTML'" in DOC_JS
    assert "_syncEmailRichbody(_rich);" in command_branch
    assert "_scheduleEmailRichbodySave();" in command_branch


def test_rich_text_paste_uses_document_allowlist_and_drops_embedded_media():
    paste_cleaner = function_body("_cleanRichTextPasteHtml")

    assert "const allowedTags = new Set" in paste_cleaner
    assert "'TABLE'" in paste_cleaner
    assert "querySelectorAll('img, video, audio, canvas')" in paste_cleaner
    assert "el.replaceWith(...Array.from(el.childNodes))" in paste_cleaner
    assert "keepLink" in paste_cleaner


def test_document_image_paste_and_drop_stop_global_chat_attachment_handlers():
    rich_handlers = DOC_JS.split("function _wireEmailRichbody", 1)[1].split(
        "function _richSelectionElement", 1
    )[0]
    markdown_handlers = DOC_JS.split("ta.addEventListener('paste'", 1)[1].split(
        "ta.addEventListener('scroll'", 1
    )[0]

    assert "e.stopPropagation();" in rich_handlers
    assert "e.stopPropagation();" in markdown_handlers
    app_js = (ROOT / "static/app.js").read_text(encoding="utf-8")
    assert "e.defaultPrevented || e.target?.closest?.('#doc-editor-pane, [contenteditable=\"true\"]')" in app_js
    assert "e.target?.closest?.('#doc-editor-pane')" in app_js


def test_empty_table_is_preserved_and_visually_editable():
    assert "rich.querySelector('img, hr, table')" in DOC_JS
    assert "clone.insertRow(-1)" in DOC_JS
    assert "row.insertCell(-1)" in DOC_JS
    assert ".doc-email-richbody.richtext-mode table" in STYLE
    assert ".doc-email-richbody.richtext-mode th" in STYLE


def test_table_menu_exposes_insert_and_contextual_operations():
    for action in (
        "table:insert:2:2",
        "table:insert:3:3",
        "table:insert:4:4",
        "table:toggle-header-row",
        "table:toggle-header-column",
        "table:cell-align:top",
        "table:cell-align:middle",
        "table:cell-align:bottom",
        "table:row-above",
        "table:row-below",
        "table:column-left",
        "table:column-right",
        "table:delete-row",
        "table:delete-column",
        "table:delete",
    ):
        assert action in DOC_JS

    assert 'data-dd="table"' in DOC_JS
    assert "_richSelectionCell(rich)" in DOC_JS


def test_table_mutations_are_undoable_and_restore_the_caret():
    table_section = DOC_JS.split("function _replaceRichTable", 1)[1].split(
        "function applyMdFormat", 1
    )[0]

    assert "document.execCommand('insertHTML'" in table_section
    assert "_focusRichTableCell(rich, cell)" in table_section
    assert "clone.deleteRow(rowIndex)" in table_section
    assert "row.deleteCell(cellIndex)" in table_section


def test_temporary_table_tokens_are_not_persisted():
    sanitizer = function_body("_sanitizedRichTextHtml")

    assert "data-editor-(?:table|checklist|image|inline-code|link)-token" in sanitizer
    assert ".replace(" in sanitizer


def test_rich_text_checklists_support_conversion_and_checked_state():
    checklist = DOC_JS.split("function _richSelectionChecklistItem", 1)[1].split(
        "function _cleanRichTextPasteHtml", 1
    )[0]

    assert "function _toggleRichChecklist(rich)" in checklist
    assert "function _setRichChecklistItemChecked" in checklist
    assert "document.execCommand('insertUnorderedList')" in checklist
    assert "document.execCommand('insertHTML'" in checklist
    assert "item.dataset.checked" in checklist
    assert "aria-checked" in checklist


def test_checklist_interactions_cover_pointer_and_keyboard_users():
    rich_wiring = function_body("_wireEmailRichbody")

    assert "rich.addEventListener('pointerdown'" in rich_wiring
    assert "mod && key === 'enter'" in rich_wiring
    assert "_setRichChecklistItemChecked" in rich_wiring


def test_checklist_markup_survives_paste_save_and_export():
    paste_cleaner = function_body("_cleanRichTextPasteHtml")

    assert "keepChecklistClass" in paste_cleaner
    assert "keepChecklistState" in paste_cleaner
    assert "ul.rich-checklist>li[data-checked" in DOC_JS
    assert ".doc-email-richbody.richtext-mode ul.rich-checklist" in STYLE


def test_email_list_menu_does_not_offer_app_specific_checklists():
    dropdown = DOC_JS.split("function _showMdDropdown", 1)[1].split(
        "function initMdToolbar", 1
    )[0]

    assert "['check', 'Checklist'" in dropdown
    assert "activeDoc?.language === 'email'" in dropdown
    assert "action !== 'check'" in dropdown


def test_rich_text_image_menu_exposes_management_controls():
    for control in (
        'data-dd="image"',
        "image:size:auto",
        "image:size:100",
        "image:size:60",
        "image:size:35",
        "image:align:left",
        "image:align:center",
        "image:align:right",
        "image:caption",
        "image:alt",
        "image:delete",
    ):
        assert control in DOC_JS


def test_rich_text_image_insertion_and_edits_are_undoable():
    insertion = DOC_JS.split("function _insertRichTextImages", 1)[1].split(
        "async function _uploadMarkdownImages", 1
    )[0]
    replacement = insertion.split("function _replaceRichImage", 1)[1].split(
        "function _deleteRichImage", 1
    )[0]

    assert "document.execCommand('insertHTML'" in insertion
    assert "range.insertNode" not in insertion
    assert "range.selectNode(original)" in replacement
    assert "replacement.outerHTML" in replacement
    assert "range.selectNode(original.closest('figure" not in replacement


def test_rich_text_image_selection_markers_are_not_persisted():
    sanitizer = function_body("_sanitizedRichTextHtml")

    assert "(?:table|checklist|image|inline-code|link)-token" in sanitizer
    assert "data-editor-image-selected" in sanitizer


def test_rich_text_image_styles_are_available_in_editor_and_export():
    assert "img.richtext-image-size-60" in STYLE
    assert "img.richtext-image-align-center" in STYLE
    assert "img.richtext-image[data-editor-image-selected]" in STYLE
    assert "img.richtext-image-size-60" in DOC_JS
    assert "img.richtext-image-align-center" in DOC_JS
    assert "figure.richtext-image .richtext-image-caption" in STYLE
    assert "figure.richtext-image .richtext-image-caption" in DOC_JS


def test_existing_figure_wrapped_images_are_normalized_on_load():
    normalizer = function_body("_normalizeRichTextImages")

    assert "figure.richtext-image" in normalizer
    assert "image.classList.add('richtext-image')" in normalizer
    assert "richtext-image-size-" in normalizer
    assert "richtext-image-align-" in normalizer


def test_inline_code_and_code_blocks_use_distinct_rich_text_commands():
    apply_format = DOC_JS.split("function applyMdFormat(action)", 1)[1]
    rich_branch = apply_format.split("const _rich = _emailRichbodyActive();", 1)[1].split(
        "const ta = document.getElementById('doc-editor-textarea');", 1
    )[0]

    assert "_toggleRichInlineCode(_rich)" in rich_branch
    assert "action === 'codeblock'" in rich_branch
    assert "document.execCommand('formatBlock'" in rich_branch


def test_inline_code_supports_selection_toggle_and_future_typing():
    inline_code = DOC_JS.split("function _richSelectionInlineCode", 1)[1].split(
        "function _cleanRichTextPasteHtml", 1
    )[0]

    assert "function _toggleRichInlineCode(rich)" in inline_code
    assert "document.execCommand('removeFormat')" in inline_code
    assert "document.execCommand('styleWithCSS', false, true)" in inline_code
    assert "document.execCommand('fontName'" in inline_code
    assert "OdysseusInlineCode" in inline_code
    assert "document.execCommand('insertHTML'" in inline_code


def test_inline_code_live_marker_is_saved_as_semantic_code():
    sanitizer = function_body("_sanitizedRichTextHtml")

    assert "_isRichInlineCodeMarker(span)" in sanitizer
    assert "document.createElement('code')" in sanitizer
    assert "span.replaceWith(code)" in sanitizer


def test_rich_code_shortcuts_and_active_state_are_wired():
    rich_wiring = function_body("_wireEmailRichbody")

    assert "action = 'codeblock'" in rich_wiring
    assert "action = 'code'" in rich_wiring
    assert "_richInlineCodeTypingArmed" in rich_wiring
    assert "_richSelectionInlineCode(rich)" in rich_wiring


def test_rich_code_styles_are_scoped_and_exported():
    assert ".doc-email-richbody.richtext-mode code" in STYLE
    assert 'span[style*="OdysseusInlineCode"]' in STYLE
    assert ".doc-email-richbody.richtext-mode pre" in STYLE
    assert "'code{padding:" in DOC_JS
    assert "'pre code{padding:0" in DOC_JS


def test_rich_link_dialog_supports_edit_open_and_remove_actions():
    link_dialog = DOC_JS.split("function _normalizeRichLinkUrl", 1)[1].split(
        "function _promptImageAlt", 1
    )[0]

    assert "editing ? 'Edit link' : 'Insert link'" in link_dialog
    assert 'id="doc-link-open"' in link_dialog
    assert 'id="doc-link-remove"' in link_dialog
    assert "editing ? 'Save' : 'Insert'" in link_dialog
    assert "action: 'open'" in link_dialog
    assert "action: 'remove'" in link_dialog
    assert ".doc-link-remove-btn" in STYLE


def test_rich_links_validate_protocols_during_paste_save_and_editing():
    normalizer = DOC_JS.split("function _normalizeRichLinkUrl", 1)[1].split(
        "function _promptLink", 1
    )[0]
    sanitizer = function_body("_sanitizedRichTextHtml")
    paste_cleaner = function_body("_cleanRichTextPasteHtml")

    assert "https?:|mailto:|tel:" in normalizer
    assert "https:${url}" in normalizer
    assert "https://${url}" in normalizer
    assert "_normalizeRichLinkUrl(link.getAttribute('href'))" in sanitizer
    assert "link.replaceWith(...Array.from(link.childNodes))" in sanitizer
    assert "_normalizeRichLinkUrl(el.getAttribute('href'))" in paste_cleaner
    assert "'H4'" in paste_cleaner


def test_rich_link_edits_and_removal_use_native_undoable_commands():
    link_commands = DOC_JS.split("function _richLinkAtRange", 1)[1].split(
        "function _richSelectionCell", 1
    )[0]

    assert "function _removeRichLink(rich, link)" in link_commands
    assert "document.execCommand('insertHTML', false, link.innerHTML)" in link_commands
    assert "existingLink.cloneNode(true)" in link_commands
    assert "savedRange.cloneContents()" in link_commands
    assert "document.execCommand('insertHTML', false, a.outerHTML)" in link_commands
    assert "inserted.removeAttribute('data-editor-link-token')" in link_commands
    assert "window.open(res.url, '_blank', 'noopener,noreferrer')" in link_commands


def test_link_toolbar_toggles_link_when_selection_is_already_linked():
    rich_wiring = function_body("_wireEmailRichbody")

    assert "const currentLink = _richLinkAtRange(rich, selectionRange)" in rich_wiring
    assert "set('[data-md=\"link\"]', !!currentLink)" in rich_wiring
    assert "const existingLink = _richLinkAtRange(_rich, range)" in DOC_JS
    assert "if (!_removeRichLink(_rich, existingLink)) return" in DOC_JS
    assert "data-md=\"unlink\"" in DOC_JS


def test_rich_text_exports_include_structural_styles():
    assert "function _richTextExportCss()" in DOC_JS
    assert "<style>${_richTextExportCss()}</style>" in DOC_JS
    assert "style.textContent = _richTextExportCss();" in DOC_JS


def test_header_history_controls_persist_rich_text_changes():
    history = DOC_JS.split("// Undo button in header", 1)[1].split(
        "// Diff toggle button", 1
    )[0]

    assert "doc-undo-btn" in history
    assert "doc-redo-btn" in history
    assert "document.execCommand('undo')" in history
    assert "document.execCommand('redo')" in history
    assert history.count("_scheduleEmailRichbodySave();") >= 2


def test_find_uses_dom_ranges_for_visible_rich_text():
    find_section = DOC_JS.split("// ── In-document find (Ctrl+F) ──", 1)[1].split(
        "// Delete (or Backspace)", 1
    )[0]

    assert "function _buildRichFindRanges" in find_section
    assert "document.createTreeWalker(rich, NodeFilter.SHOW_TEXT)" in find_section
    assert "document.createRange()" in find_section
    assert "CSS.highlights.set(_richFindAllName" in find_section
    assert "CSS.highlights.set(_richFindCurrentName" in find_section
    assert "if (rich)" in find_section
    assert find_section.index("if (rich)") < find_section.index("const text = ta.value")


def test_rich_find_highlights_are_non_persistent_and_cleaned_up():
    assert "::highlight(doc-find-results)" in STYLE
    assert "::highlight(doc-find-current)" in STYLE
    assert "CSS.highlights?.delete('doc-find-results')" in DOC_JS
    assert "CSS.highlights?.delete('doc-find-current')" in DOC_JS


def test_find_and_replace_has_visible_controls_and_keyboard_entry_points():
    for control in (
        'id="doc-find-toolbar-btn"',
        'id="doc-find-replace-toggle"',
        'id="doc-replace-input"',
        'id="doc-replace-current"',
        'id="doc-replace-all"',
    ):
        assert control in DOC_JS

    assert "const key = e.key.toLowerCase()" in DOC_JS
    assert "key === 'h'" in DOC_JS
    assert "_openFindBar(key === 'h')" in DOC_JS
    assert ".doc-replace-row[hidden]" in STYLE
    assert ".doc-find-action" in STYLE


def test_rich_find_replace_preserves_structure_and_uses_native_undo_commands():
    replace_section = DOC_JS.split("function _replaceAllLiteral", 1)[1].split(
        "function _doFind", 1
    )[0]

    assert "function _replaceFindCurrent()" in replace_section
    assert "document.execCommand('insertText'" in replace_section
    assert "function _replaceFindAll()" in replace_section
    assert "const clone = rich.cloneNode(true)" in replace_section
    assert "[...ranges].reverse()" in replace_section
    assert "document.execCommand('insertHTML', false, clone.innerHTML)" in replace_section
    assert "_syncEmailRichbody(rich)" in replace_section
    assert "_scheduleEmailRichbodySave()" in replace_section


def test_source_find_replace_uses_literal_matching_and_shared_undo_path():
    replace_section = DOC_JS.split("function _replaceAllLiteral", 1)[1].split(
        "function _doFind", 1
    )[0]

    assert "lowerText.indexOf(lowerQuery, cursor)" in replace_section
    assert "_replaceRange(ta, match, match + query.length" in replace_section
    assert "_replaceRange(ta, 0, ta.value.length, result.text)" in replace_section
    assert "pos = i + Math.max(1, q.length)" in DOC_JS


def test_visible_rich_text_selection_feeds_ai_editing_context():
    selection_section = DOC_JS.split("// ---- Selection-based AI editing ----", 1)[1].split(
        "// ── Inline Suggestion Comments", 1
    )[0]

    assert "function updateRichSelectionState(rich)" in selection_section
    assert "kind: 'rich'" in selection_section
    assert "function _richRangeFromOffsets" in selection_section
    assert "CSS.highlights.set(_richSelectionHighlightName" in selection_section
    assert "const source = s.kind === 'rich' ? richText : text;" in selection_section
    assert "rich.addEventListener('mouseup'" in DOC_JS


def test_rich_selection_highlight_is_cleared_without_mutating_document_html():
    assert "::highlight(doc-ai-selections)" in STYLE
    assert "CSS.highlights?.delete(_richSelectionHighlightName)" in DOC_JS
    assert "CSS.highlights?.delete('doc-ai-selections')" in DOC_JS
