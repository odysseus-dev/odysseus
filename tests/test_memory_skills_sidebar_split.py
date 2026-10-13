import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
MEMORY_JS = (ROOT / "static" / "js" / "memory.js").read_text(encoding="utf-8")
CHAT_STREAM_JS = (ROOT / "static" / "js" / "chatStream.js").read_text(encoding="utf-8")
INIT_JS = (ROOT / "static" / "js" / "init.js").read_text(encoding="utf-8")

TOOL_ORDER = [
    "tool-calendar-btn",
    "tool-compare-btn",
    "tool-cookbook-btn",
    "tool-research-btn",
    "tool-gallery-btn",
    "tool-library-btn",
    "tool-memory-btn",
    "tool-notes-btn",
    "tool-skills-btn",
    "tool-tasks-btn",
    "tool-theme-btn",
]

RAIL_ORDER = [
    "rail-calendar",
    "rail-compare",
    "rail-cookbook",
    "rail-research",
    "rail-email",
    "rail-gallery",
    "rail-archive",
    "rail-memory",
    "rail-notes",
    "rail-skills",
    "rail-tasks",
    "rail-theme",
]


def _element_by_id(source: str, tag: str, element_id: str) -> str:
    match = re.search(
        rf"<{tag}\b(?=[^>]*\bid=\"{re.escape(element_id)}\")[\s\S]*?</{tag}>",
        source,
    )
    assert match, f"missing <{tag}>#{element_id}"
    return match.group(0)


def test_memory_modal_and_sidebar_are_renamed_from_brain():
    assert 'role="dialog" aria-label="Memory"' in INDEX
    assert 'id="memory-modal-title-text">Memory</span>' in INDEX

    memory_row = _element_by_id(INDEX, "div", "tool-memory-btn")
    assert "<span class=\"grow\">Memory</span>" in memory_row
    assert "Brain" not in memory_row

    assert 'id="rail-memory" title="Memory"' in INDEX


def test_skills_has_its_own_sidebar_and_rail_launcher():
    skills_row = _element_by_id(INDEX, "div", "tool-skills-btn")
    assert "<span class=\"grow\">Skills</span>" in skills_row
    assert "<polygon" in skills_row
    assert 'id="rail-skills" title="Skills"' in INDEX
    assert 'data-ui-key="tool-skills"' in INDEX


def test_launchers_open_the_shared_modal_to_the_right_tab():
    assert "export function openMemoryModal(tabName = 'browse')" in MEMORY_JS
    assert "const mode = tabName === 'skills' ? 'skills' : 'memory';" in MEMORY_JS
    assert "scope !== activeMemoryModalMode" in MEMORY_JS
    assert "el.classList.toggle('hidden', scopedOut)" in MEMORY_JS
    assert "tab.hidden || tab.classList.contains('hidden')" in MEMORY_JS
    assert "memoryModule.openMemoryModal('browse')" in APP_JS
    assert "memoryModule.openMemoryModal('skills')" in APP_JS
    assert "'rail-skills':    'tool-skills-btn'" in APP_JS
    assert "skills: 'tool-skills-btn'" in CHAT_STREAM_JS


def test_memory_privilege_hides_both_memory_and_skills_launchers():
    selector = "#tool-memory-btn, #rail-memory, #tool-skills-btn, #rail-skills"
    assert selector in INIT_JS


def test_memory_and_skills_modal_sections_are_scoped_by_launcher():
    assert 'data-memory-tab="browse" data-memory-scope="memory"' in INDEX
    assert 'data-memory-tab="skills" data-memory-scope="skills"' in INDEX
    assert 'data-memory-panel="browse" data-memory-scope="memory"' in INDEX
    assert 'data-memory-panel="skills" data-memory-scope="skills"' in INDEX
    assert '<div class="admin-card" data-memory-scope="memory">' in INDEX
    assert '<div class="admin-card" data-memory-scope="skills">' in INDEX


def test_sidebar_tools_and_icon_rail_are_alphabetically_ordered():
    tool_positions = [INDEX.index(f'id="{tool_id}"') for tool_id in TOOL_ORDER]
    assert tool_positions == sorted(tool_positions)

    rail_positions = [INDEX.index(f'id="{rail_id}"') for rail_id in RAIL_ORDER]
    assert rail_positions == sorted(rail_positions)
