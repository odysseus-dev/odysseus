"""Distill uploaded documents (PDF, text, Office) into SKILL.md bundles."""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from typing import Any, Dict, Optional, Tuple

from services.memory.skill_importer import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
    SkillImportError,
    _safe_relpath,
)

logger = logging.getLogger(__name__)

DOCUMENT_MAX_BYTES = 10 * 1024 * 1024
MAX_INPUT_CHARS = 48_000
ALLOWED_EXTENSIONS = frozenset({".pdf", ".md", ".txt", ".markdown", ".epub", ".docx"})

_DISTILL_PROMPT = (
    "You convert technical documents into an Odysseus agent skill bundle "
    "(like book-to-skill). Return ONLY valid JSON with no markdown fences:\n"
    "{\n"
    '  "name": "short-slug",\n'
    '  "description": "one line under 200 chars",\n'
    '  "skill_md": "full SKILL.md with YAML frontmatter (name, description, '
    'category: imported) and sections: When to use, Procedure (bullet steps), '
    'optional pitfalls/verification",\n'
    '  "references": {"chapters/ch01-topic.md": "markdown...", '
    '"glossary.md": "..."}\n'
    "}\n\n"
    "Rules:\n"
    "- Capture actionable mental models and procedures, not a generic summary.\n"
    "- references is optional; use at most 8 files, each under 4000 chars.\n"
    "- Use safe relative paths (letters, numbers, slashes, hyphens).\n"
    "- If the source is long, put the router/index in skill_md and details in references.\n"
)


def extract_document_text(path: str, ext: str) -> str:
    """Extract plain/markdown text from a document on disk."""
    ext = (ext or "").lower()
    if ext == ".pdf":
        from src.document_processor import _process_pdf, strip_pdf_content_marker

        raw = _process_pdf(path)
        text = strip_pdf_content_marker(raw)
        if not text.strip():
            from src.personal_docs import extract_pdf_text

            text = extract_pdf_text(path)
        return (text or "").strip()

    if ext in {".docx", ".epub"}:
        from src.markitdown_runtime import convert_to_markdown, is_markitdown_format

        if is_markitdown_format(path):
            md = convert_to_markdown(path)
            if md and md.strip():
                return md.strip()
        raise SkillImportError(
            "Office/EPUB extraction requires markitdown "
            "(pip install -r requirements-optional.txt)"
        )

    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except UnicodeDecodeError:
        from charset_normalizer import detect

        with open(path, "rb") as f:
            raw = f.read()
        encoding = (detect(raw) or {}).get("encoding") or "utf-8"
        return raw.decode(encoding, errors="replace").strip()


def _clip_input(text: str) -> str:
    text = (text or "").strip()
    if not text:
        raise SkillImportError("document has no readable text")
    if len(text) > MAX_INPUT_CHARS:
        return text[:MAX_INPUT_CHARS] + "\n\n[document truncated for distillation]"
    return text


def _parse_distill_json(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    text = re.sub(r"```(?:json)?\s*", "", text, flags=re.I)
    text = text.replace("```", "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise SkillImportError("model did not return skill JSON")
    frag = text[start : end + 1]
    for cand in (frag, re.sub(r",(\s*[}\]])", r"\1", frag)):
        try:
            data = json.loads(cand)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    raise SkillImportError("could not parse skill JSON from model output")


def bundle_from_distill(data: Dict[str, Any]) -> Dict[str, str]:
    """Turn distill JSON into a path → content bundle."""
    skill_md = str(data.get("skill_md") or "").strip()
    if not skill_md:
        raise SkillImportError("model output missing skill_md")
    if "skill.md" not in skill_md.lower()[:200] and not skill_md.startswith("---"):
        raise SkillImportError("skill_md must be valid SKILL.md with frontmatter")

    files: Dict[str, str] = {"SKILL.md": skill_md}
    refs = data.get("references") or {}
    if not isinstance(refs, dict):
        refs = {}

    total = len(skill_md.encode("utf-8"))
    for rel, content in refs.items():
        if len(files) >= MAX_FILES:
            break
        if not isinstance(rel, str) or not isinstance(content, str):
            continue
        safe = _safe_relpath(rel)
        if not safe.lower().endswith(".md"):
            safe += ".md" if not safe.endswith("/") else "notes.md"
        body = content.strip()
        if not body:
            continue
        if len(body.encode("utf-8")) > MAX_FILE_BYTES:
            body = body[: MAX_FILE_BYTES - 64] + "\n[truncated]"
        total += len(body.encode("utf-8"))
        if total > MAX_TOTAL_BYTES:
            break
        files[safe] = body

    if not any(p.lower().endswith("skill.md") for p in files):
        raise SkillImportError("bundle has no SKILL.md")
    return files


async def distill_document_to_bundle(
    text: str,
    *,
    url: str,
    model: str,
    headers: Optional[dict],
    source_name: str = "document",
) -> Tuple[Dict[str, str], str]:
    """LLM-distill document text into a skill file bundle. Returns (files, slug hint)."""
    from src.llm_core import llm_call_async

    clipped = _clip_input(text)
    user_msg = f"Source filename: {source_name}\n\n=== DOCUMENT ===\n{clipped}"
    raw = await llm_call_async(
        url,
        model,
        [
            {"role": "system", "content": _DISTILL_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.2,
        max_tokens=16384,
        headers=headers,
        timeout=300,
    )
    data = _parse_distill_json(raw or "")
    files = bundle_from_distill(data)
    slug = str(data.get("name") or "").strip()
    return files, slug


def extract_upload_to_text(data: bytes, filename: str) -> str:
    """Write upload bytes to a temp file and extract text."""
    name = (filename or "upload").strip()
    _, ext = os.path.splitext(name.lower())
    if ext not in ALLOWED_EXTENSIONS:
        raise SkillImportError(
            f"unsupported file type {ext or '(none)'} — "
            f"use {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )
    if not data:
        raise SkillImportError("empty upload")

    suffix = ext or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        return extract_document_text(tmp_path, ext)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
