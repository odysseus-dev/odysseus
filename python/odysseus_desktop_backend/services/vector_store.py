from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from odysseus_desktop_backend.storage import Database, utc_ms


@dataclass(frozen=True)
class VectorChunk:
    id: str
    document_id: str
    chunk_index: int
    content: str
    content_hash: str
    embedding_model: str
    embedding_hash: str
    vector: np.ndarray
    page_start: int | None = None
    page_end: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchResult:
    chunk_id: str
    document_id: str
    content: str
    score: float
    page_start: int | None
    page_end: int | None
    metadata: dict[str, Any]


class VectorStore(ABC):
    @abstractmethod
    def upsert_chunks(self, chunks: list[VectorChunk]) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def delete_by_document(self, document_id: str) -> int:
        raise NotImplementedError

    @abstractmethod
    def similarity_search(
        self,
        query_vector: np.ndarray,
        *,
        limit: int = 5,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        raise NotImplementedError

    @abstractmethod
    def reindex_document(self, document_id: str, chunks: list[VectorChunk]) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def health(self) -> dict[str, Any]:
        raise NotImplementedError


class SQLiteNumPyVectorStore(VectorStore):
    version = "sqlite-numpy-v1"

    def __init__(self, db: Database):
        self.db = db

    def upsert_chunks(self, chunks: list[VectorChunk]) -> list[str]:
        now = utc_ms()
        ids: list[str] = []
        for chunk in chunks:
            self.db.conn.execute(
                """
                INSERT INTO rag_chunks(
                    id, document_id, chunk_index, content, content_hash, page_start, page_end,
                    metadata_json, embedding_model, embedding_hash, is_deleted, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(document_id, chunk_index) DO UPDATE SET
                    id = excluded.id,
                    content = excluded.content,
                    content_hash = excluded.content_hash,
                    page_start = excluded.page_start,
                    page_end = excluded.page_end,
                    metadata_json = excluded.metadata_json,
                    embedding_model = excluded.embedding_model,
                    embedding_hash = excluded.embedding_hash,
                    is_deleted = 0,
                    updated_at = excluded.updated_at
                """,
                (
                    chunk.id,
                    chunk.document_id,
                    chunk.chunk_index,
                    chunk.content,
                    chunk.content_hash,
                    chunk.page_start,
                    chunk.page_end,
                    json.dumps(chunk.metadata),
                    chunk.embedding_model,
                    chunk.embedding_hash,
                    now,
                    now,
                ),
            )
            ids.append(chunk.id)
        self.db.conn.commit()
        return ids

    def delete_by_document(self, document_id: str) -> int:
        now = utc_ms()
        cur = self.db.conn.execute(
            """
            UPDATE rag_chunks
            SET is_deleted = 1, updated_at = ?
            WHERE document_id = ? AND is_deleted = 0
            """,
            (now, document_id),
        )
        self.db.conn.commit()
        return cur.rowcount

    def similarity_search(
        self,
        query_vector: np.ndarray,
        *,
        limit: int = 5,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        rows = self.db.conn.execute(
            """
            SELECT
                c.id, c.document_id, c.content, c.page_start, c.page_end,
                c.metadata_json, e.vector_blob, e.dimensions
            FROM rag_chunks c
            JOIN documents d ON d.id = c.document_id
            JOIN embedding_cache e
                ON e.content_hash = c.embedding_hash
                AND e.embedding_model = c.embedding_model
            WHERE c.is_deleted = 0 AND d.is_deleted = 0 AND d.index_status = 'indexed'
            """
        ).fetchall()

        query = query_vector.astype(np.float32)
        query_norm = float(np.linalg.norm(query))
        if query_norm == 0:
            return []

        candidates: list[SearchResult] = []
        for row in rows:
            metadata = json.loads(row["metadata_json"] or "{}")
            if not self._metadata_matches(metadata, metadata_filter):
                continue
            vector = np.frombuffer(row["vector_blob"], dtype=np.float32)
            if vector.shape[0] != row["dimensions"]:
                continue
            denom = float(np.linalg.norm(vector)) * query_norm
            score = 0.0 if denom == 0 else float(np.dot(query, vector) / denom)
            candidates.append(
                SearchResult(
                    chunk_id=row["id"],
                    document_id=row["document_id"],
                    content=row["content"],
                    score=score,
                    page_start=row["page_start"],
                    page_end=row["page_end"],
                    metadata=metadata,
                )
            )
        candidates.sort(key=lambda item: item.score, reverse=True)
        return candidates[: max(0, limit)]

    def reindex_document(self, document_id: str, chunks: list[VectorChunk]) -> list[str]:
        self.delete_by_document(document_id)
        return self.upsert_chunks(chunks)

    def health(self) -> dict[str, Any]:
        row = self.db.conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM documents WHERE is_deleted = 0) AS documents,
                (SELECT COUNT(*) FROM rag_chunks WHERE is_deleted = 0) AS chunks,
                (SELECT COUNT(*) FROM embedding_cache) AS cached_embeddings
            """
        ).fetchone()
        return {
            "ok": True,
            "version": self.version,
            "documents": row["documents"],
            "chunks": row["chunks"],
            "cached_embeddings": row["cached_embeddings"],
        }

    def _metadata_matches(
        self,
        metadata: dict[str, Any],
        metadata_filter: dict[str, Any] | None,
    ) -> bool:
        if not metadata_filter:
            return True
        for key, expected in metadata_filter.items():
            if metadata.get(key) != expected:
                return False
        return True
