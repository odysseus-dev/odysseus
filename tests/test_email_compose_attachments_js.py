"""Regression guard for email compose paperclip attachments (#2227)."""

import re
from pathlib import Path


SRC = Path(__file__).resolve().parent.parent / "static/js/document.js"


def _function_body(name: str) -> str:
    text = SRC.read_text(encoding="utf-8")
    match = re.search(rf"\n\s*(?:async\s+)?function\s+{name}\([^)]*\)\s*\{{", text)
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


def test_file_picker_snapshots_files_before_resetting_input():
    body = _function_body("_handleAttachUpload")

    snapshot = "const files = Array.from(e.target.files || []);"
    reset = "e.target.value = '';"
    upload = "await _uploadComposeFiles(files);"

    assert snapshot in body
    assert reset in body
    assert upload in body
    assert body.index(snapshot) < body.index(reset) < body.index(upload)
    assert "const files = e.target.files;" not in body
