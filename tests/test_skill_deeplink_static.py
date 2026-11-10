from pathlib import Path
import re
from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parents[1]


def test_skill_deeplink_clears_filters_and_retries_scroll():
    src = (ROOT / "static/js/skills.js").read_text()

    assert "function _resetSkillDeepLinkFilters()" in src
    assert "skills-search" in src
    assert "_showDraftsOnly = false;" in src
    assert "_showPublishedOnly = false;" in src
    assert "_confMax = null;" in src
    assert "_skillsQuickFilter = null;" in src
    assert "attempt < 12" in src
    assert "setTimeout(() => _focusSkillRow(targetName, attempt + 1), 120)" in src
    assert "scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' })" in src
    assert "if (!card.classList.contains('doclib-card-expanded')) await _expandSkillCard" in src
    assert "decodeURIComponent(String(name || '').replace" in src


def test_ui_control_open_panel_supports_theme_and_settings_module_open():
    stream_src = (ROOT / "static/js/chatStream.js").read_text()
    ai_src = (ROOT / "src/ai_interaction.py").read_text()
    schema_src = (ROOT / "src/tool_schemas.py").read_text()
    index_src = (ROOT / "src/tool_index.py").read_text()

    assert "panel === 'theme' || panel === 'themes'" in stream_src
    assert "mod.togglePopup" in stream_src
    assert "mod.open || (mod.default && mod.default.open)" in stream_src
    assert '"theme": "theme"' in ai_src
    assert '"themes": "theme"' in ai_src
    assert "settings, theme, cookbook" in schema_src
    assert "settings, theme, cookbook" in index_src


def test_skill_list_summary_persists_clickable_skill_anchors():
    from src.agent_loop import _skills_list_summary_from_tool_output

    summary = _skills_list_summary_from_tool_output(
        "\n".join(
            [
                "## Published",
                "- **open-a-youtube-creator-s-latest-video** (general): Open a YouTube creator's latest video",
                "## Drafts",
                "- **audit-fixture-20260828-skills-search** [draft]: domain audit fixture",
            ]
        )
    )

    assert "[open-a-youtube-creator-s-latest-video](#skill-open-a-youtube-creator-s-latest-video)" in summary
    assert "[audit-fixture-20260828-skills-search](#skill-audit-fixture-20260828-skills-search)" in summary
    assert "**open-a-youtube-creator-s-latest-video**" not in summary


def test_memory_list_summary_persists_clickable_memory_anchors():
    from src.agent_loop import _memory_list_summary_from_tool_output

    summary = _memory_list_summary_from_tool_output(
        "\n".join(
            [
                "Found 2 memory entries:",
                "",
                "- [preference] `abc12345` — Use short replies.",
                "- [project] `def67890` — Working on Odysseus SFT traces.",
            ]
        )
    )

    assert "[preference abc12345](#memory-abc12345)" in summary
    assert "[project def67890](#memory-def67890)" in summary
    assert "`abc12345`" not in summary


def test_saved_chat_renderer_routes_memory_and_skill_anchors():
    renderer_src = (ROOT / "static/js/chatRenderer.js").read_text()

    assert "decodeURIComponent(String(rawId || '').replace" in renderer_src
    assert "kind === 'memory'" in renderer_src
    assert "mod.openMemory" in renderer_src
    assert "import('./memory.js')" in renderer_src
    assert "kind === 'skill'" in renderer_src
    assert "mod.openSkill" in renderer_src
    assert re.search(r"import\('\./skills\.js\?v=[A-Za-z0-9_-]+'\)", renderer_src)


def test_markdown_keeps_encoded_deeplink_hashes_clickable():
    markdown_src = (ROOT / "static/js/markdown.js").read_text()

    assert r"^#[A-Za-z0-9_.~%:@-]*$" in markdown_src


def test_markdown_flattens_legacy_notes_more_details():
    markdown_src = (ROOT / "static/js/markdown.js").read_text()

    assert "function flattenLegacyNoteMoreDetails" in markdown_src
    assert "more\\s+notes?" in markdown_src
    assert "let s = extractMoreListPayloads(flattenLegacyNoteMoreDetails(src ?? ''));" in markdown_src


def test_markdown_expands_note_and_skill_more_links_without_details():
    markdown_src = (ROOT / "static/js/markdown.js").read_text()
    style_src = app_css()

    assert "function extractMoreListPayloads" in markdown_src
    assert "ody-more-(notes|skills|memories|events|sessions)" in markdown_src
    assert "a.chat-link[href^=\"#notes-more-\"]" in style_src
    assert "a.chat-link[href^=\"#skills-more-\"]" in style_src
    assert "a.chat-link[href^=\"#memories-more-\"]" in style_src
    assert "a.chat-link[href^=\"#events-more-\"]" in style_src
    assert "a.chat-link[href^=\"#sessions-more-\"]" in style_src
    assert "more-list-expanded" in markdown_src
    assert "more-list-expanded" in style_src
    assert "insertionPoint.insertAdjacentElement('beforebegin', expanded)" in markdown_src
    assert "parent.insertAdjacentElement('afterend', expanded)" not in markdown_src


def test_terminal_skill_listing_uses_clickable_bounded_formatter():
    agent_src = (ROOT / "src/agent_loop.py").read_text()
    style_src = app_css()

    assert "_qwen_skills_terminal_summary = _skills_list_summary_from_tool_output(" in agent_src
    assert '.msg-ai .body a[href^="#skill-"]' in style_src
    assert '.msg-ai .body a[href^="#memory-"]' in style_src


def test_list_links_share_compact_typography():
    style_src = app_css()

    assert 'a.chat-link[href^="#events-more-"]' in style_src
    assert "font-size: 11px;" in style_src
    assert '.msg-ai .body a[href^="#note-"]' in style_src
    assert '.msg-ai .body a[href^="#session-"]:hover' in style_src
    assert 'p:has(> .emoji:first-child):has(> a.chat-link[href^="#note-"])' in style_src


def test_open_skills_also_persists_skill_list():
    agent_src = (ROOT / "src/agent_loop.py").read_text()

    assert 're.search(r"\\bopen_panel\\s+skills\\b"' in agent_src
    assert 'tool_blocks.append(ToolBlock("manage_skills", json.dumps({"action": "list"})))' in agent_src


def test_memory_deeplink_scrolls_and_flashes_even_when_filtered():
    memory_src = (ROOT / "static/js/memory.js").read_text()
    style_src = app_css()

    assert "function _resetMemoryDeepLinkFilters()" in memory_src
    assert "activeCategory = 'all';" in memory_src
    assert "memory-search" in memory_src
    assert "attempt < 12" in memory_src
    assert "setTimeout(() => openMemory(wanted, attempt + 1), 120)" in memory_src
    assert "scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' })" in memory_src
    assert "memory-item-focus" in style_src
    assert "animation: anchor-item-flash 2.2s ease-out" in style_src


def test_skill_and_memory_links_use_polished_chat_link_style():
    style_src = app_css()

    assert '.msg-ai .body a[href^="#skill-"]' in style_src
    assert '.msg-ai .body a[href^="#memory-"]' in style_src
    assert '.msg-ai .body a[href^="#skill-"]:hover' in style_src
    assert '.msg-ai .body a[href^="#memory-"]:hover' in style_src
