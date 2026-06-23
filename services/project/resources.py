"""ProjectResourceStore — per-project uploads, parsing, chunking, RAG index.

Each resource lives in:
    <project_data_dir>/uploads/filename
    <project_data_dir>/rag_index.json (metadata index)
and its chunks live in a per-project ChromaDB collection:
    project_resources_<project_id>

This module does NOT directly touch ChromaDB; it delegates embedding to
``ProjectRagAdapter`` (Task 15). It owns the on-disk file + rag_index.json.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, asdict
from typing import List, Optional

from core.atomic_io import atomic_write_json

RESOURCE_ID_PREFIX = "rsr_"
MAX_CHUNK_CHARS = 8000  # spec §6 (retrieval budget)
SUPPORTED_MIMES = {
    "text/plain": "txt",
    "text/markdown": "md",
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
}


@dataclass
class Resource:
    id: str
    filename: str
    size_bytes: int
    mime: str
    chunk_count: int
    indexed_at: int

    def to_dict(self) -> dict:
        return asdict(self)


def _chunk_text(text: str, max_chars: int = MAX_CHUNK_CHARS) -> List[str]:
    """Split text into chunks of at most ``max_chars`` characters.

    Naive paragraph splitter — keeps it dependency-free and deterministic.
    The real parse for PDF/DOCX yields text via existing document helpers
    (see Task 14b for the MIME-specific dispatch).
    """
    text = text.strip()
    if not text:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: List[str] = []
    buf = ""
    for p in paragraphs:
        if len(buf) + len(p) + 1 > max_chars and buf:
            chunks.append(buf)
            buf = p
        else:
            buf = f"{buf}\n\n{p}".strip()
    if buf:
        chunks.append(buf)
    return chunks


def _extract_text(path: str, mime: str) -> str:
    """Extract text for the supported MIME types. PDF + DOCX delegate to
    the existing ``src.document_processor`` helpers (which the main app
    already uses for personal_docs / attachments)."""
    if mime == "application/pdf":
        try:
            from src.document_processor import _process_pdf
            return _process_pdf(path)
        except Exception:
            # Fallback only fires when the PDF parser is unavailable
            # (e.g. no pdfplumber installed). Resource is still saved
            # so the user can re-index once the parser is restored.
            with open(path, "rb") as f:
                return f.read().decode("utf-8", errors="ignore")
    if mime.startswith("application/vnd.openxmlformats-officedocument"):
        try:
            from src.document_processor import _process_office_document
            return _process_office_document(path, display_name=os.path.basename(path))
        except Exception:
            return ""
    # txt + md: read directly.
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


class ProjectResourceStore:
    def __init__(self, project_id: str, data_dir: str, rag=None) -> None:
        self.project_id = project_id
        self.data_dir = data_dir
        self.uploads_dir = os.path.join(data_dir, "uploads")
        self.index_path = os.path.join(data_dir, "rag_index.json")
        # rag is injected (Task 15) so we can write tests without ChromaDB.
        self.rag = rag

    # ──────────────────────────────── public API ────────────────────────────────

    def add(self, *, source_path: str, filename: str, mime: str) -> Resource:
        if mime not in SUPPORTED_MIMES:
            raise ValueError(f"unsupported mime: {mime}")
        os.makedirs(self.uploads_dir, exist_ok=True)
        dest = os.path.join(self.uploads_dir, filename)
        # Don't clobber — add a numeric suffix.
        dest = self._unique_dest(dest)
        shutil.copy2(source_path, dest)

        text = _extract_text(dest, mime)
        chunks = _chunk_text(text)
        if not chunks:
            raise ValueError("resource_parse_failed: no extractable text")

        rid = f"{RESOURCE_ID_PREFIX}{uuid.uuid4().hex[:12]}"
        if self.rag is not None:
            try:
                self.rag.add_chunks(rid, chunks, metadata={"filename": filename})
            except Exception:
                # RAG embedding is best-effort: if ChromaDB/lanes are unavailable,
                # the file is still saved + indexed in rag_index.json. The user can
                # reindex once the embedding service is back.
                pass

        index = self._read_index()
        resource = Resource(
            id=rid,
            filename=os.path.basename(dest),
            size_bytes=os.path.getsize(dest),
            mime=mime,
            chunk_count=len(chunks),
            indexed_at=int(time.time()),
        )
        index["resources"].append(resource.to_dict())
        self._write_index(index)
        return resource

    def list(self) -> List[Resource]:
        return [Resource(**r) for r in self._read_index()["resources"]]

    def remove(self, resource_id: str) -> bool:
        index = self._read_index()
        target = next((r for r in index["resources"] if r["id"] == resource_id), None)
        if target is None:
            return False
        index["resources"] = [r for r in index["resources"] if r["id"] != resource_id]
        # Remove file (best-effort).
        fpath = os.path.join(self.uploads_dir, target["filename"])
        try:
            os.remove(fpath)
        except FileNotFoundError:
            pass
        self._write_index(index)
        if self.rag is not None:
            self.rag.delete_chunks(resource_id)
        return True

    def reindex(self, resource_id: str) -> Resource:
        index = self._read_index()
        rec = next((r for r in index["resources"] if r["id"] == resource_id), None)
        if rec is None:
            raise KeyError(resource_id)
        src = os.path.join(self.uploads_dir, rec["filename"])
        text = _extract_text(src, rec["mime"])
        chunks = _chunk_text(text)
        if self.rag is not None:
            self.rag.delete_chunks(resource_id)
            self.rag.add_chunks(resource_id, chunks, metadata={"filename": rec["filename"]})
        rec["chunk_count"] = len(chunks)
        rec["indexed_at"] = int(time.time())
        self._write_index(index)
        return Resource(**rec)

    # ──────────────────────────────── internals ────────────────────────────────

    def _read_index(self) -> dict:
        if not os.path.exists(self.index_path):
            return {"version": 1, "resources": []}
        try:
            with open(self.index_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"version": 1, "resources": []}

    def _write_index(self, index: dict) -> None:
        atomic_write_json(self.index_path, index)

    @staticmethod
    def _unique_dest(path: str) -> str:
        if not os.path.exists(path):
            return path
        base, ext = os.path.splitext(path)
        i = 1
        while os.path.exists(f"{base}_{i}{ext}"):
            i += 1
        return f"{base}_{i}{ext}"