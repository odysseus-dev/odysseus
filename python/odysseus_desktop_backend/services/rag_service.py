from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from dataclasses import replace
from typing import Any

from odysseus_desktop_backend.services.document_service import DocumentService
from odysseus_desktop_backend.services.embedding_service import EmbeddingService, content_hash
from odysseus_desktop_backend.services.vector_store import SearchResult, VectorChunk, VectorStore


MAX_CHUNK_CHARS = 900
CHUNK_OVERLAP_CHARS = 120
RERANK_MIN_CANDIDATES = 32
TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
STOPWORDS = {
    "a",
    "about",
    "all",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "called",
    "can",
    "context",
    "doc",
    "document",
    "file",
    "for",
    "from",
    "have",
    "i",
    "in",
    "info",
    "information",
    "is",
    "it",
    "me",
    "mention",
    "of",
    "on",
    "or",
    "pdf",
    "please",
    "previous",
    "that",
    "the",
    "this",
    "tell",
    "to",
    "was",
    "what",
    "with",
    "you",
}


@dataclass(frozen=True)
class ChunkDraft:
    content: str
    page_start: int | None
    page_end: int | None
    metadata: dict[str, Any]


class RAGService:
    def __init__(
        self,
        documents: DocumentService,
        embeddings: EmbeddingService,
        vector_store: VectorStore,
    ):
        self.documents = documents
        self.embeddings = embeddings
        self.vector_store = vector_store

    def index_document(self, document_id: str) -> dict[str, Any]:
        document = self.documents.get(document_id)
        if document["is_deleted"]:
            raise ValueError("cannot index a deleted document")
        if document["is_low_text"] or document["index_status"] == "low_text":
            return {
                "document": document,
                "chunks": [],
                "embedded": 0,
                "cached": 0,
                "low_text": True,
            }

        self.documents.mark_indexing(document_id)
        pages = self.documents.pages(document_id)
        drafts = self._chunk_pages(document, pages)
        if not drafts:
            self.documents.mark_low_text(document_id)
            return {
                "document": self.documents.get(document_id),
                "chunks": [],
                "embedded": 0,
                "cached": 0,
                "low_text": True,
            }

        embedding_results = self.embeddings.embed_texts([draft.content for draft in drafts])
        chunks: list[VectorChunk] = []
        cached = 0
        for index, (draft, embedding) in enumerate(zip(drafts, embedding_results)):
            if embedding.from_cache:
                cached += 1
            digest = content_hash(draft.content)
            chunks.append(
                VectorChunk(
                    id=self._chunk_id(document_id, index, digest),
                    document_id=document_id,
                    chunk_index=index,
                    content=draft.content,
                    content_hash=digest,
                    embedding_model=embedding.model,
                    embedding_hash=embedding.content_hash,
                    vector=embedding.vector,
                    page_start=draft.page_start,
                    page_end=draft.page_end,
                    metadata=draft.metadata,
                )
            )

        self.vector_store.reindex_document(document_id, chunks)
        self.documents.mark_indexed(document_id)
        self.documents.link_ocr_chunks(document_id)
        return {
            "document": self.documents.get(document_id),
            "chunks": self.documents.chunks(document_id),
            "embedded": len(chunks) - cached,
            "cached": cached,
            "low_text": False,
        }

    def reindex_document(self, document_id: str) -> dict[str, Any]:
        return self.index_document(document_id)

    def delete_document(self, document_id: str) -> dict[str, Any]:
        self.documents.get(document_id)
        deleted_chunks = self.vector_store.delete_by_document(document_id)
        document = self.documents.mark_deleted(document_id)
        return {**document, "deleted_chunks": deleted_chunks}

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        text = (query or "").strip()
        if not text:
            return []
        query_vector = self.embeddings.embed_query(text)
        candidate_limit = max(limit, RERANK_MIN_CANDIDATES, limit * 8)
        candidates = self.vector_store.similarity_search(
            query_vector,
            limit=candidate_limit,
            metadata_filter=metadata_filter,
        )
        reranked = self._rerank(text, candidates)
        return [
            self._result_dict(result)
            for result in reranked[: max(0, limit)]
        ]

    def health(self) -> dict[str, Any]:
        return self.vector_store.health()

    def build_context(self, query: str, *, limit: int = 4) -> tuple[str, list[dict[str, Any]]]:
        results = self.search(query, limit=limit)
        if not results:
            return "", []
        results = self._expand_focused_document_results(query, results, limit=limit)
        blocks = []
        for index, result in enumerate(results, start=1):
            title = result["metadata"].get("title") or result["metadata"].get("file_name") or "Document"
            page = result["page_start"]
            location = f", page {page}" if page else ""
            blocks.append(f"[{index}] {title}{location}\n{result['content']}")
        return "\n\n".join(blocks), results

    def _chunk_pages(self, document: dict[str, Any], pages: list[dict[str, Any]]) -> list[ChunkDraft]:
        drafts: list[ChunkDraft] = []
        for page in pages:
            text = " ".join((page["text"] or "").split())
            if not text:
                continue
            chunks = self._chunk_text(text)
            for chunk_text in chunks:
                drafts.append(
                    ChunkDraft(
                        content=chunk_text,
                        page_start=page["page_number"],
                        page_end=page["page_number"],
                        metadata={
                            "document_id": document["id"],
                            "title": document["title"],
                            "file_name": document["file_name"],
                            "file_type": document["file_type"],
                            "page_start": page["page_number"],
                            "page_end": page["page_number"],
                        },
                    )
                )
        return drafts

    def _chunk_text(self, text: str) -> list[str]:
        if len(text) <= MAX_CHUNK_CHARS:
            return [text]
        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = min(len(text), start + MAX_CHUNK_CHARS)
            if end < len(text):
                boundary = text.rfind(" ", start, end)
                if boundary > start + 200:
                    end = boundary
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(text):
                break
            start = max(end - CHUNK_OVERLAP_CHARS, start + 1)
        return chunks

    def _chunk_id(self, document_id: str, chunk_index: int, digest: str) -> str:
        seed = f"{document_id}:{chunk_index}:{digest}"
        return str(uuid.UUID(hashlib.md5(seed.encode("utf-8")).hexdigest()))

    def _rerank(self, query: str, results: list[SearchResult]) -> list[SearchResult]:
        query_tokens = important_tokens(query)
        query_phrase = normalized_text(query)
        scored: list[tuple[float, SearchResult]] = []

        for result in results:
            content_tokens = important_tokens(result.content)
            metadata_text = " ".join(
                str(result.metadata.get(key) or "")
                for key in ("title", "file_name", "source_name")
            )
            metadata_tokens = important_tokens(metadata_text)

            lexical_score = overlap_score(query_tokens, content_tokens) * 1.25
            metadata_score = overlap_score(query_tokens, metadata_tokens) * 2.0

            content_phrase = normalized_text(result.content)
            metadata_phrase = normalized_text(metadata_text)
            if query_phrase and query_phrase in content_phrase:
                lexical_score += 0.75
            if query_phrase and query_phrase in metadata_phrase:
                metadata_score += 2.5

            combined_score = result.score + lexical_score + metadata_score
            scored.append((combined_score, replace(result, score=combined_score)))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [result for _score, result in scored]

    def _result_dict(self, result: SearchResult) -> dict[str, Any]:
        return {
            "chunk_id": result.chunk_id,
            "document_id": result.document_id,
            "content": result.content,
            "score": result.score,
            "page_start": result.page_start,
            "page_end": result.page_end,
            "metadata": result.metadata,
        }

    def _expand_focused_document_results(
        self,
        query: str,
        results: list[dict[str, Any]],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        if not self._should_focus_on_top_document(query, results):
            return results

        top = results[0]
        chunks = self.documents.chunks(top["document_id"])
        if not chunks:
            return results

        score_by_id = {result["chunk_id"]: float(result["score"]) for result in results}
        focused: list[dict[str, Any]] = []
        for chunk in chunks[: max(limit, 8)]:
            focused.append(
                {
                    "chunk_id": chunk["id"],
                    "document_id": chunk["document_id"],
                    "content": chunk["content"],
                    "score": score_by_id.get(chunk["id"], 0.0),
                    "page_start": chunk["page_start"],
                    "page_end": chunk["page_end"],
                    "metadata": chunk["metadata"],
                }
            )
        return focused

    def _should_focus_on_top_document(self, query: str, results: list[dict[str, Any]]) -> bool:
        if not results:
            return False

        top = results[0]
        top_document_id = top["document_id"]
        same_document_results = [
            result for result in results if result["document_id"] == top_document_id
        ]
        leading_results = results[: min(3, len(results))]
        leading_same_document = sum(
            1 for result in leading_results if result["document_id"] == top_document_id
        )
        if leading_same_document >= 2:
            return True

        second_score = float(results[1]["score"]) if len(results) > 1 else 0.0
        top_score = float(top["score"])
        if top_score >= second_score + 0.5:
            return True

        query_tokens = important_tokens(query)
        for result in same_document_results:
            metadata_text = " ".join(
                str(result["metadata"].get(key) or "")
                for key in ("title", "file_name", "source_name")
            )
            if overlap_score(query_tokens, important_tokens(metadata_text)) >= 0.5:
                return True
            if overlap_score(query_tokens, important_tokens(result["content"])) >= 0.65:
                return True

        return False


def important_tokens(text: str) -> set[str]:
    tokens = {token.lower() for token in TOKEN_RE.findall(text)}
    important = {token for token in tokens if token not in STOPWORDS}
    return important or tokens


def normalized_text(text: str) -> str:
    return " ".join(token.lower() for token in TOKEN_RE.findall(text))


def overlap_score(query_tokens: set[str], candidate_tokens: set[str]) -> float:
    if not query_tokens or not candidate_tokens:
        return 0.0
    return len(query_tokens & candidate_tokens) / len(query_tokens)
