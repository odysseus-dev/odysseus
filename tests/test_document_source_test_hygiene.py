"""Tests must reason about the document editor's module set, not one file.

``static/js/document.js`` is being decomposed. It stays the URL the browser
requests, so a browser test that imports ``/static/js/document.js`` keeps
working. What does not survive is reading the file off disk: a test that greps
the entry file alone silently covers less as soon as the behaviour it names
moves into a module, and it keeps passing while doing so.

``tests/helpers/document_source`` is the way to read it. This fails on the two
habits that break, both of which existed here before the helper did.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).name

# The helper itself names the file, because being the one place that does is
# the point.
ALLOWED = {SELF, "document_source.py", "document_source.mjs"}

# Every test language the assertions can hide in. A Python-only glob is what
# let the JS references to ``static/style.css`` outlive the file they named.
_SUFFIXES = ("*.py", "*.mjs", "*.js", "*.html")

# Reading the entry file off disk. Not matched: `/static/js/document.js` as a
# request URL or a dynamic `import()`, which stay correct through the wrapper,
# and which `test_frontend_module_version_parity.py` already pins.
_DIRECT_READ = re.compile(
    r'["\']static/js/document\.js["\']'            # string literal
    r'|"static"\s*/\s*"js"\s*/\s*"document\.js"'   # pathlib join
)

# Slices of the form `src.split(A, 1)[1].split(B, 1)[0]` -- "the region between
# A and B". That region is only what the test means while A and B are
# neighbours in one file. Several also hard-code the entry file's two-space
# indentation, which no extracted module reproduces.
_ADJACENCY_SLICE = re.compile(
    r'(?P<var>\b[A-Za-z_]\w*)\.split\(\s*\n?\s*(?P<q1>["\'])(?P<a>(?:[^"\'\\]|\\.)+?)(?P=q1)'
    r'\s*,\s*1\s*\)\[1\]\s*\n?\s*\.split\(\s*\n?\s*(?P<q2>["\'])(?P<b>(?:[^"\'\\]|\\.)+?)(?P=q2)'
    r'\s*,\s*1\s*\)\[0\]',
    re.S,
)

# The adjacency slices still in the tree, each spanning a whole family of
# functions rather than one construct -- "everything from _docxHexColor to
# exportAsDocx". Collapsing one to its first member drops what the assertions
# look for, so they cannot be rewritten mechanically: each is converted when
# the family it spans becomes a module, and its entry deleted here then.
#
# This list may only shrink. A new entry means a new adjacency-dependent slice
# was written, which is the habit the helper exists to end.
KNOWN_ADJACENCY_SLICES = {
    ('test_document_active_restore.py',
     'for (const doc of activeDocs)',
     '_syncDocIndicator'),
    ('test_document_rich_checklist_enter.py',
     'function _handleRichChecklistEnter',
     'let _richInlineCodeTypingArmed'),
    ('test_document_rich_docx_export.py',
     'async function exportAsDocx',
     '/** Delete the active document'),
    ('test_document_rich_docx_export.py',
     'function _docxHexColor',
     'async function exportAsDocx'),
    ('test_document_rich_structure_tools.py',
     'function _showMdDropdown',
     'function initMdToolbar'),
    ('test_document_rich_table_header_preservation.py',
     'function _applyRichTableAction',
     'function applyMdFormat'),
    ('test_document_rich_table_header_preservation.py',
     'function _richTableHeaderModes',
     'function _replaceRichTable'),
    ('test_document_rich_table_headers.py',
     'function _applyRichTableAction',
     'function applyMdFormat'),
    ('test_document_rich_table_merge_split.py',
     'function _showMdDropdown',
     'function initMdToolbar'),
    ('test_document_rich_text_tools.py',
     '// ---- Selection-based AI editing ----',
     '// ── Inline Suggestion Comments'),
    ('test_document_rich_text_tools.py',
     '// Undo button in header',
     '// Diff toggle button'),
    ('test_document_rich_text_tools.py',
     '// ── In-document find (Ctrl+F) ──',
     '// Delete (or Backspace)'),
    ('test_document_rich_text_tools.py',
     'const _richSpacingBlockSelector',
     'function _focusRichTextOffset'),
    ('test_document_rich_text_tools.py',
     'function _insertRichTextImages',
     'async function _uploadMarkdownImages'),
    ('test_document_rich_text_tools.py',
     'function _normalizeRichLinkUrl',
     'function _promptImageAlt'),
    ('test_document_rich_text_tools.py',
     'function _normalizeRichLinkUrl',
     'function _promptLink'),
    ('test_document_rich_text_tools.py',
     'function _replaceAllLiteral',
     'function _doFind'),
    ('test_document_rich_text_tools.py',
     'function _replaceRichTable',
     'function applyMdFormat'),
    ('test_document_rich_text_tools.py',
     'function _richLinkAtRange',
     'function _richSelectionCell'),
    ('test_document_rich_text_tools.py',
     'function _richSelectionChecklistItem',
     'function _cleanRichTextPasteHtml'),
    ('test_document_rich_text_tools.py',
     'function _richSelectionInlineCode',
     'function _cleanRichTextPasteHtml'),
    ('test_document_rich_text_tools.py',
     'function _showMdDropdown',
     'function initMdToolbar'),
    ('test_document_rich_text_tools.py',
     'function _wireEmailRichbody',
     'function _richSelectionElement'),
    ('test_document_rich_toolbar_menus.py',
     'function _showMdDropdown',
     'function initMdToolbar'),
    ('test_document_rich_toolbar_menus.py',
     'function initMdToolbar',
     'function _applyDocFont'),
    ('test_document_toolbar_order.py',
     'const _DOCUMENT_TOOLBAR_GROUPS',
     'function _orderDocumentToolbar'),
    ('test_review_docx_async_identity.py',
     '  let _docxPreviewRequest = 0;',
     '  /** Parse CSV'),
}


def _test_sources() -> list[Path]:
    found: list[Path] = []
    for suffix in _SUFFIXES:
        found.extend((ROOT / "tests").rglob(suffix))
    return [p for p in sorted(set(found)) if p.name not in ALLOWED]


def test_sources_are_discoverable() -> None:
    """Guard the guard: a layout change must not make this vacuous."""
    sources = _test_sources()
    assert len(sources) > 100
    suffixes = {p.suffix for p in sources}
    assert {".py", ".mjs", ".js"} <= suffixes, suffixes
    assert any(p.parent != ROOT / "tests" for p in sources), "walk is not recursive"


def test_no_test_reads_the_document_entry_file_off_disk() -> None:
    offenders = [
        str(p.relative_to(ROOT))
        for p in _test_sources()
        if _DIRECT_READ.search(p.read_text(encoding="utf-8"))
    ]

    assert not offenders, (
        "read the document editor through tests.helpers.document_source instead "
        "of static/js/document.js, which is becoming a re-export wrapper: "
        f"{offenders}"
    )


def test_adjacency_slices_only_shrink() -> None:
    """No new "region between two declarations" slice enters the tree."""
    found = set()
    for path in _test_sources():
        if path.suffix != ".py":
            continue
        source = path.read_text(encoding="utf-8")
        if "tests.helpers.document_source" not in source:
            continue
        bound = {
            m.group(1)
            for m in re.finditer(r"(\w+)\s*=\s*document_source\(\)", source)
        }
        for m in _ADJACENCY_SLICE.finditer(source):
            if m.group("var") in bound:
                found.add((path.name, m.group("a"), m.group("b")))

    added = found - KNOWN_ADJACENCY_SLICES
    assert not added, (
        "these slices depend on two declarations being neighbours in one file, "
        "which decomposition breaks; use function_body()/declaration() or "
        "assert against the owning module: " + repr(sorted(added))
    )

    removed = KNOWN_ADJACENCY_SLICES - found
    assert not removed, (
        "these adjacency slices are gone -- delete them from "
        f"KNOWN_ADJACENCY_SLICES so the list keeps shrinking: {sorted(removed)}"
    )
