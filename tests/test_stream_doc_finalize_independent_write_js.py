"""Regression guard for PR #4661 review: `streamDocFinalize` gated the final
content flush on `if (textarea && codeEl)`, requiring BOTH DOM elements to be
present. `streamDocDelta` already updates each element independently, so a
document editor showing only one of the two panes (e.g. code view without a
raw textarea, or vice versa) would silently drop the final flush entirely for
whichever pane was in the DOM. Fixed to update each element independently,
same as streamDocDelta.
"""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "static/js/document.js"


def _function_body(name: str) -> str:
    text = SRC.read_text(encoding="utf-8")
    match = re.search(rf"\n\s*(?:export\s+)?(?:async\s+)?function\s+{name}\([^)]*\)\s*\{{", text)
    assert match, f"{name} not found"

    start = match.end()
    depth = 1
    i = start
    while i < len(text) and depth:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    assert depth == 0, f"{name} body did not close"
    return text[start : i - 1]


def test_final_content_flush_does_not_require_both_elements():
    body = _function_body("streamDocFinalize")

    assert "if (textarea && codeEl)" not in body
    assert "if (textarea) textarea.value = finalContent;" in body
    assert "if (codeEl) codeEl.textContent = finalContent + '\\n';" in body


def test_final_content_flush_still_precedes_unconditional_sync_highlighting():
    body = _function_body("streamDocFinalize")

    write_idx = body.index("if (codeEl) codeEl.textContent")
    sync_idx = body.index("syncHighlighting();")
    assert write_idx < sync_idx

    # syncHighlighting() itself must remain unconditional (not gated on either
    # element existing) — it already was, this just pins that it stays so.
    around_sync = body[max(0, sync_idx - 200) : sync_idx]
    assert "if (textarea || codeEl)" not in around_sync
