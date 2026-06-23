"""ProjectRagAdapter — ChromaDB per-project resource collection.

Each project owns a single collection named
``project_resources_<project_id>``. The adapter delegates embedding to
the shared embedding client (see ``src.embeddings``) so adding new
projects never instantiates a new embedding model.

Why per-project collections (not a ``where`` filter):
ChromaDB `where` filters scan the collection per query — O(n) per call.
With multiple projects × multiple resources, the scan dominates latency.
Per-project collections give O(1) scoping at query time.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from src.embeddings import get_embedding_client


@dataclass
class Chunk:
    text: str
    source: str          # resource_id
    chunk_index: int
    score: float = 0.0


def _collection_name(project_id: str) -> str:
    return f"project_resources_{project_id}"


class ProjectRagAdapter:
    def __init__(self, project_id: str, chroma_client=None) -> None:
        self.project_id = project_id
        self._client = chroma_client
        self._collection = None
        self._embedder = None

    def _ensure(self) -> None:
        if self._collection is not None:
            return
        if self._client is None:
            from src.chroma_client import get_chroma_client
            self._client = get_chroma_client()
        self._collection = self._client.get_or_create_collection(
            _collection_name(self.project_id)
        )
        self._embedder = get_embedding_client()

    def _encode(self, texts: List[str]) -> List[List[float]]:
        if self._embedder is None:
            return []
        vecs = self._embedder.encode(list(texts), normalize_embeddings=True)
        return vecs.tolist() if hasattr(vecs, "tolist") else [list(v) for v in vecs]

    def add_chunks(self, resource_id: str, chunks: List[str],
                   metadata: Optional[dict] = None) -> None:
        self._ensure()
        if not chunks:
            return
        ids = [f"{resource_id}:{i}" for i in range(len(chunks))]
        metadatas = [
            {"resource_id": resource_id, "chunk_index": i, **(metadata or {})}
            for i in range(len(chunks))
        ]
        self._collection.add(
            ids=ids,
            embeddings=self._encode(chunks),
            documents=chunks,
            metadatas=metadatas,
        )

    def delete_chunks(self, resource_id: str) -> None:
        self._ensure()
        self._collection.delete(where={"resource_id": resource_id})

    def query(self, text: str, top_k: int = 5) -> List[Chunk]:
        self._ensure()
        results = self._collection.query(
            query_embeddings=self._encode([text]),
            n_results=top_k,
        )
        ids = (results.get("ids") or [[]])[0]
        docs = (results.get("documents") or [[]])[0]
        metas = (results.get("metadatas") or [[]])[0]
        out: List[Chunk] = []
        for cid, doc, meta in zip(ids, docs, metas):
            # ChromaDB distance is cosine distance; score = 1 - d.
            out.append(Chunk(
                text=doc,
                source=meta.get("resource_id", ""),
                chunk_index=int(meta.get("chunk_index", 0)),
            ))
        return out[:top_k]