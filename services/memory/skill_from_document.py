"""Distill uploaded documents (PDF, text, Office) into SKILL.md bundles."""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from services.memory.skill_importer import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
    SkillImportError,
    _safe_relpath,
)

logger = logging.getLogger(__name__)

DOCUMENT_MAX_BYTES = 10 * 1024 * 1024
MAX_EXTRACT_CHARS = 400_000
SINGLE_PASS_CHARS = 48_000
CHUNK_CHARS = 12_000
MAX_CHUNKS = 16
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
)

_CHUNK_PROMPT = (
    "You extract actionable skill reference material from ONE section of a longer "
    "document. Return ONLY valid JSON with no markdown fences:\n"
    '{"title": "short chapter title", "content": "markdown under 3500 chars"}\n'
    "Focus on mental models, procedures, and key terms — not generic summary."
)

_MERGE_PROMPT = (
    "You create the master SKILL.md router for an agent skill bundle built from "
    "chapter extracts (like book-to-skill). Return ONLY valid JSON:\n"
    "{\n"
    '  "name": "short-slug",\n'
    '  "description": "one line under 200 chars",\n'
    '  "skill_md": "SKILL.md with YAML frontmatter and a chapter index — when to '
    'load each chapters/chNN.md file. Do NOT paste full chapter bodies here.",\n'
    '  "references": {"glossary.md": "optional key terms", "patterns.md": "optional"}\n'
    "}\n"
    "Optional references: at most 2 extra files under 3000 chars each."
    "- If the source is long, put the router/index in skill_md and details in references.\n"
)


def extract_document_text(path: str, ext: str) -> str:
    """Extract plain/markdown text from a document on disk."""
    ext = (ext or "").lower()
    if ext == ".pdf":
        from src.personal_docs import extract_pdf_text

        text = extract_pdf_text(path)
        if not text.strip():
            from src.document_processor import _process_pdf, strip_pdf_content_marker

            text = strip_pdf_content_marker(_process_pdf(path))
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


def _cap_extracted(text: str) -> str:
    text = (text or "").strip()
    if not text:
        raise SkillImportError("document has no readable text")
    if len(text) > MAX_EXTRACT_CHARS:
        return text[:MAX_EXTRACT_CHARS] + "\n\n[document truncated for distillation]"
    return text


def split_document_chunks(text: str, *, chunk_size: int = CHUNK_CHARS) -> List[str]:
    """Split long documents on paragraph boundaries for multi-pass distillation."""
    text = (text or "").strip()
    if len(text) <= chunk_size:
        return [text] if text else []
    chunks: List[str] = []
    start = 0
    length = len(text)
    while start < length and len(chunks) < MAX_CHUNKS:
        end = min(start + chunk_size, length)
        if end < length:
            para = text.rfind("\n\n", start, end)
            if para > start + chunk_size // 3:
                end = para
            else:
                sentence = max(text.rfind(". ", start, end), text.rfind(".\n", start, end))
                if sentence > start + chunk_size // 3:
                    end = sentence + 1
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        start = end if end > start else start + chunk_size
    if start < length and len(chunks) < MAX_CHUNKS:
        tail = text[start:].strip()
        if tail:
            chunks.append(tail)
    return chunks


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
    if not skill_md.startswith("---"):
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
            safe = f"{safe}.md" if safe else "notes.md"
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


def merge_bundle_parts(
    skill_md: str,
    chapter_files: Dict[str, str],
    extra_refs: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Combine SKILL.md, chapter extracts, and optional glossary/patterns."""
    files: Dict[str, str] = {"SKILL.md": skill_md.strip()}
    total = len(skill_md.encode("utf-8"))
    for rel, body in {**(chapter_files or {}), **(extra_refs or {})}.items():
        if len(files) >= MAX_FILES:
            break
        safe = _safe_relpath(rel)
        if not safe.lower().endswith(".md"):
            safe = f"{safe}.md"
        text = (body or "").strip()
        if not text:
            continue
        if len(text.encode("utf-8")) > MAX_FILE_BYTES:
            text = text[: MAX_FILE_BYTES - 64] + "\n[truncated]"
        total += len(text.encode("utf-8"))
        if total > MAX_TOTAL_BYTES:
            break
        files[safe] = text
    return files


async def _llm_json(
    system: str,
    user: str,
    *,
    url: str,
    model: str,
    headers: Optional[dict],
    max_tokens: int = 8192,
) -> Dict[str, Any]:
    from src.llm_core import llm_call_async

    raw = await llm_call_async(
        url,
        model,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.2,
        max_tokens=max_tokens,
        headers=headers,
        timeout=300,
    )
    return _parse_distill_json(raw or "")


async def _distill_single_pass(
    text: str,
    *,
    url: str,
    model: str,
    headers: Optional[dict],
    source_name: str,
) -> Tuple[Dict[str, str], str]:
    clipped = text if len(text) <= SINGLE_PASS_CHARS else (
        text[:SINGLE_PASS_CHARS] + "\n\n[document truncated for distillation]"
    )
    user_msg = f"Source filename: {source_name}\n\n=== DOCUMENT ===\n{clipped}"
    data = await _llm_json(
        url=url, model=model, headers=headers,
        system=_DISTILL_PROMPT, user=user_msg, max_tokens=16384,
    )
    return bundle_from_distill(data), str(data.get("name") or "").strip()


async def _distill_multi_pass(
    text: str,
    *,
    url: str,
    model: str,
    headers: Optional[dict],
    source_name: str,
) -> Tuple[Dict[str, str], str]:
    chunks = split_document_chunks(text)
    if not chunks:
        raise SkillImportError("document has no readable text")

    chapter_files: Dict[str, str] = {}
    outline_lines: List[str] = []

    for idx, chunk in enumerate(chunks, start=1):
        path = f"chapters/ch{idx:02d}.md"
        user_msg = (
            f"Source: {source_name}\n"
            f"Chapter {idx} of {len(chunks)}\n\n=== SECTION ===\n{chunk}"
        )
        try:
            data = await _llm_json(
                url=url, model=model, headers=headers,
                system=_CHUNK_PROMPT, user=user_msg, max_tokens=4096,
            )
        except SkillImportError as e:
            logger.warning("chunk %s distill failed: %s", idx, e)
            continue
        title = str(data.get("title") or f"Chapter {idx}").strip()
        content = str(data.get("content") or "").strip()
        if not content:
            continue
        header = f"# {title}\n\n"
        chapter_files[path] = header + content
        outline_lines.append(f"- `{path}` — {title}")

    if not chapter_files:
        raise SkillImportError("could not distill any chapters from the document")

    merge_user = (
        f"Source: {source_name}\n"
        f"Chapters distilled ({len(chapter_files)}):\n"
        + "\n".join(outline_lines)
        + "\n\nFirst chapter excerpt:\n"
        + next(iter(chapter_files.values()))[:1200]
    )
    merged = await _llm_json(
        url=url, model=model, headers=headers,
        system=_MERGE_PROMPT, user=merge_user, max_tokens=8192,
    )
    skill_md = str(merged.get("skill_md") or "").strip()
    if not skill_md.startswith("---"):
        raise SkillImportError("merge pass did not return valid SKILL.md")

    extra = merged.get("references") if isinstance(merged.get("references"), dict) else {}
    files = merge_bundle_parts(skill_md, chapter_files, extra)
    return files, str(merged.get("name") or "").strip()


async def distill_document_to_bundle(
    text: str,
    *,
    url: str,
    model: str,
    headers: Optional[dict],
    source_name: str = "document",
) -> Tuple[Dict[str, str], str]:
    """LLM-distill document text into a skill file bundle. Returns (files, slug hint)."""
    full = _cap_extracted(text)
    if len(full) <= SINGLE_PASS_CHARS:
        return await _distill_single_pass(
            full, url=url, model=model, headers=headers, source_name=source_name,
        )
    logger.info(
        "multi-pass skill distill: %s chars -> %s chunks",
        len(full), len(split_document_chunks(full)),
    )
    return await _distill_multi_pass(
        full, url=url, model=model, headers=headers, source_name=source_name,
    )


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
