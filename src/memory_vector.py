"""
memory_vector.py

ChromaDB-backed vector store for memory entries.
Shares the EmbeddingClient with RAG to save memory.
Stores pre-computed embeddings (ChromaDB does not manage embedding).
"""

import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


class MemoryVectorStore:
    """Vector index over memory entries for semantic retrieval."""

    COLLECTION_NAME = "odysseus_memories"

    def __init__(self, data_dir: str, embedding_model=None):
        self._model = embedding_model
        self._collection = None
        self._healthy = False

        self._initialize()

    def _initialize(self):
        try:
            from src.chroma_client import get_chroma_client

            if self._model is None:
                from src.embeddings import get_embedding_client
                self._model = get_embedding_client()
                if self._model is None:
                    raise RuntimeError("No embedding backend available")
                logger.info(f"MemoryVectorStore using embeddings: {self._model.url}")

            client = get_chroma_client()
            self._collection = client.get_or_create_collection(
                name=self.COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )

            self._healthy = True
            count = self._collection.count()
            logger.info(f"MemoryVectorStore ready (entries={count})")

        except Exception as e:
            logger.error(f"MemoryVectorStore init failed: {e}")

    @property
    def healthy(self) -> bool:
        return self._healthy

    def _embed(self, texts: List[str]) -> List[List[float]]:
        vecs = self._model.encode(texts, normalize_embeddings=True)
        return vecs.tolist()

    def count(self) -> int:
        """Return the number of stored vectors."""
        if not self._healthy:
            return 0
        return self._collection.count()

    def add(self, memory_id: str, text: str):
        """Add a single memory entry to the vector index."""
        if not self._healthy:
            return
        # Skip if already exists
        existing = self._collection.get(ids=[memory_id])
        if existing["ids"]:
            return
        embeddings = self._embed([text])
        self._collection.add(
            ids=[memory_id],
            embeddings=embeddings,
            documents=[text],
            metadatas=[{"source": "memory"}],
        )

    def remove(self, memory_id: str):
        """Remove a memory entry. O(1) — no rebuild needed."""
        if not self._healthy:
            return
        try:
            self._collection.delete(ids=[memory_id])
        except Exception as e:
            logger.warning(f"memory remove {memory_id}: {e}")

    def search(self, query: str, k: int = 8) -> List[Dict]:
        """Search for the most relevant memory IDs by semantic similarity.
        Returns list of {"memory_id": str, "score": float}.

        ChromaDB cosine distance = 1 - cosine_similarity.
        We convert back: similarity = 1.0 - distance.
        """
        if not self._healthy or self._collection.count() == 0:
            return []

        embeddings = self._embed([query])
        actual_k = min(k, self._collection.count())
        results = self._collection.query(
            query_embeddings=embeddings,
            n_results=actual_k,
        )

        out = []
        for idx, mid in enumerate(results["ids"][0]):
            distance = results["distances"][0][idx]
            out.append({
                "memory_id": mid,
                "score": round(1.0 - distance, 4),
            })
        return out

    def find_similar(self, text: str, threshold: float = 0.92) -> Optional[str]:
        """Check if a near-duplicate exists. Returns memory_id if found, else None."""
        if not self._healthy or self._collection.count() == 0:
            return None

        embeddings = self._embed([text])
        results = self._collection.query(
            query_embeddings=embeddings,
            n_results=1,
        )

        if results["ids"][0]:
            distance = results["distances"][0][0]
            similarity = 1.0 - distance
            if similarity >= threshold:
                return results["ids"][0][0]
        return None

    def rebuild(self, memories: List[Dict]):
        """Rebuild the entire index from a list of memory entries.
        Each entry must have 'id' and 'text' keys."""
        if not self._healthy:
            return

        from src.chroma_client import get_chroma_client

        # Delete and recreate collection for a clean rebuild
        client = get_chroma_client()
        try:
            client.delete_collection(self.COLLECTION_NAME)
        except Exception:
            pass
        self._collection = client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

        texts = []
        ids = []
        for mem in memories:
            text = mem.get("text", "").strip()
            mid = mem.get("id", "")
            if text and mid:
                texts.append(text)
                ids.append(mid)

        if texts:
            # Batch in chunks of 100 to avoid oversized requests
            for i in range(0, len(texts), 100):
                batch_texts = texts[i:i + 100]
                batch_ids = ids[i:i + 100]
                embeddings = self._embed(batch_texts)
                self._collection.add(
                    ids=batch_ids,
                    embeddings=embeddings,
                    documents=batch_texts,
                    metadatas=[{"source": "memory"}] * len(batch_ids),
                )

        logger.info(f"MemoryVectorStore rebuilt with {len(ids)} entries")

    def clear(self):
        """Remove ALL vectors from the index (used by the admin memory-wipe).

        Implemented as an empty rebuild — delete + recreate the collection — so a
        memory wipe doesn't leave 'ghost' entries retrievable via semantic search.
        Never raises: a no-op when unhealthy, and a logged warning if ChromaDB
        became unreachable after init (rebuild -> get_chroma_client can raise), so
        the caller isn't relied on to swallow it."""
        if not self._healthy:
            return
        try:
            self.rebuild([])
            logger.info("MemoryVectorStore cleared")
        except Exception as e:
            logger.warning(f"MemoryVectorStore clear failed (vectors may remain): {e}")


# Process-wide shared instance. Every MemoryVectorStore backs the same ChromaDB
# collection (via get_chroma_client), so a single shared accessor is enough for
# callers that don't already hold the app's instance — e.g. the admin memory-wipe
# (routes/admin_wipe_routes.py) and the scheduler-driven consolidation.
_store: Optional["MemoryVectorStore"] = None


def get_memory_vector_store(data_dir: str = None) -> "MemoryVectorStore":
    """Return a lazily-constructed, process-wide shared MemoryVectorStore.

    Caches the instance once it is healthy; if a prior attempt was unhealthy
    (e.g. ChromaDB wasn't reachable yet) a later call retries construction.
    Callers must still guard on ``.healthy`` (or ``hasattr``) — this can return an
    unhealthy store when ChromaDB/embeddings are unavailable."""
    global _store
    if _store is not None and _store.healthy:
        return _store
    if data_dir is None:
        from src.constants import DATA_DIR
        data_dir = DATA_DIR
    _store = MemoryVectorStore(data_dir)
    return _store
