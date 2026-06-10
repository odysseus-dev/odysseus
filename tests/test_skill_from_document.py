"""Document → skill distillation helpers."""
import json

import pytest

from services.memory.skill_from_document import (
    SkillImportError,
    _clip_input,
    _parse_distill_json,
    bundle_from_distill,
    extract_upload_to_text,
=======
    _parse_distill_json,
    bundle_from_distill,
    extract_upload_to_text,
    merge_bundle_parts,
    split_document_chunks,
)


def _sample_skill_md():
    return (
        "---\n"
        "name: thinking-fast\n"
        "description: Decision-making frameworks\n"
        "category: imported\n"
        "---\n\n"
        "## When to use\n\nBias and choice problems.\n\n"
        "## Procedure\n\n- Slow down on high-stakes calls.\n"
    )


def test_clip_input_rejects_empty():
    with pytest.raises(SkillImportError, match="no readable text"):
        _clip_input("   ")


def test_clip_input_truncates_long_docs():
    out = _clip_input("x" * 60_000)
    assert len(out) <= 48_000 + 64
    assert "truncated" in out
=======
def test_split_document_chunks_single():
    assert split_document_chunks("short doc") == ["short doc"]


def test_split_document_chunks_multiple():
    text = ("Paragraph one.\n\n" * 50) + ("Paragraph two.\n\n" * 50)
    chunks = split_document_chunks(text, chunk_size=500)
    assert len(chunks) >= 2
    assert "".join(chunks).replace("\n", "")[:40] == text.replace("\n", "")[:40]


def test_merge_bundle_parts():
    files = merge_bundle_parts(
        _sample_skill_md(),
        {"chapters/ch01.md": "# Ch1\n"},
        {"glossary.md": "# Terms\n"},
    )
    assert "SKILL.md" in files
    assert "chapters/ch01.md" in files
    assert "glossary.md" in files


def test_parse_distill_json_accepts_fenced():
    raw = "```json\n" + json.dumps({
        "name": "demo",
        "skill_md": _sample_skill_md(),
        "references": {},
    }) + "\n```"
    data = _parse_distill_json(raw)
    assert data["name"] == "demo"


def test_bundle_from_distill_builds_files():
    files = bundle_from_distill({
        "skill_md": _sample_skill_md(),
        "references": {"chapters/ch01.md": "# Chapter 1\n"},
    })
    assert "SKILL.md" in files
    assert "chapters/ch01.md" in files


def test_bundle_from_distill_rejects_missing_skill_md():
    with pytest.raises(SkillImportError, match="missing skill_md"):
        bundle_from_distill({"references": {}})


def test_extract_upload_to_text_md():
    text = extract_upload_to_text(b"# Hello\n\nWorld\n", "notes.md")
    assert "Hello" in text


def test_extract_upload_to_text_rejects_unknown_ext():
    with pytest.raises(SkillImportError, match="unsupported"):
        extract_upload_to_text(b"data", "file.exe")
