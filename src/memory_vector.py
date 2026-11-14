"""
memory_vector.py

ChromaDB-backed vector store for memory entries.
Shares the EmbeddingClient with RAG to save memory.
Stores pre-computed embeddings (ChromaDB does not manage embedding).
"""

import logging
from pathlib import Path
from typing import List, Dict, Optional

from src.embedding_lanes import (
    LANE_CUSTOM,
    LANE_FASTEMBED,
    build_embedding_lanes,
    collection_name,
    dedupe_results,
    lane_count,
    migrate_legacy_collection,
)

logger = logging.getLogger(__name__)


def _is_missing_collection_error(error: Exception) -> bool:
    """Return whether Chroma reported that the named collection is absent."""
    error_type = type(error)
    return (
        error_type.__module__ == "chromadb.errors"
        and error_type.__name__ in {"NotFoundError", "InvalidCollectionException"}
    )


class MemoryVectorStore:
    """Vector index over memory entries for semantic retrieval."""

    COLLECTION_NAME = "odysseus_memories"
    REBUILD_MARKER = ".memory-vector-rebuild-required"

    def __init__(self, data_dir: str, embedding_model=None):
        self._model = embedding_model
        self._rebuild_marker = Path(data_dir) / self.REBUILD_MARKER
        self._collection = None
        self._lanes = []
        self._healthy = False

        self._initialize()

    def _initialize(self):
        try:
            if self._rebuild_marker.exists():
                # A previous rebuild did not finish durably. Never adopt its
                # non-empty collections as authoritative merely because they
                # survived a process restart. Reset them so startup sees an
                # empty index and rebuilds from the JSON source of truth.
                logger.warning("Discarding incomplete memory-vector rebuild")
                self._replace_collections()
                self._clear_rebuild_marker()
                return

            self._lanes = build_embedding_lanes(self.COLLECTION_NAME)
            if not self._lanes:
                raise RuntimeError("No embedding lanes available")

            self._healthy = True
            self._collection = next(
                (lane.collection for lane in self._lanes if lane.name == LANE_FASTEMBED),
                self._lanes[0].collection,
            )
            migrate_legacy_collection(self.COLLECTION_NAME, self._lanes)
            logger.info(
                "MemoryVectorStore ready (lanes=%s entries=%s)",
                [lane.name for lane in self._lanes],
                self.count(),
            )

        except Exception as e:
            self._healthy = False
            self._lanes = []
            self._collection = None
            logger.error(f"MemoryVectorStore init failed: {e}")

    def _mark_rebuild_incomplete(self) -> None:
        """Durably poison collections until a complete rebuild is committed."""
        self._rebuild_marker.write_text("rebuild required\n", encoding="utf-8")

    def _clear_rebuild_marker(self) -> None:
        self._rebuild_marker.unlink(missing_ok=True)

    def _delete_collection_names(self, names) -> None:
        """Delete named collections, ignoring only explicit not-found errors."""
        from src.chroma_client import get_chroma_client

        client = get_chroma_client()
        for name in names:
            try:
                client.delete_collection(name)
            except Exception as e:
                if _is_missing_collection_error(e):
                    continue
                raise

    @property
    def healthy(self) -> bool:
        return self._healthy

    def _embed(self, texts: List[str]) -> List[List[float]]:
        if not self._lanes:
            return []
        return self._lanes[0].encode(texts)

    def count(self) -> int:
        """Return the number of stored vectors."""
        if not self._healthy:
            return 0
        return lane_count(self._lanes)

    def _collections_for_delete(self):
        collections = []
        seen = set()

        def add(collection) -> None:
            if collection is None:
                return
            key = getattr(collection, "name", None) or id(collection)
            if key in seen:
                return
            seen.add(key)
            collections.append(collection)

        for lane in self._lanes:
            add(lane.collection)

        try:
            from src.chroma_client import get_chroma_client

            client = get_chroma_client()
            for lane_name in (LANE_CUSTOM, LANE_FASTEMBED):
                try:
                    add(client.get_collection(collection_name(self.COLLECTION_NAME, lane_name)))
                except Exception:
                    pass
        except Exception:
            pass

        return collections

    def add(self, memory_id: str, text: str):
        """Add a single memory entry to the vector index."""
        if not self._healthy:
            return
        for lane in self._lanes:
            try:
                existing = lane.collection.get(ids=[memory_id])
                if existing["ids"]:
                    continue
                lane.collection.add(
                    ids=[memory_id],
                    embeddings=lane.encode([text]),
                    documents=[text],
                    metadatas=[{"source": "memory"}],
                )
            except Exception as e:
                logger.warning("memory add failed in %s lane for %s: %s", lane.name, memory_id, e)

    def remove(self, memory_id: str):
        """Remove a memory entry. O(1) — no rebuild needed."""
        if not self._healthy:
            return
        for collection in self._collections_for_delete():
            try:
                collection.delete(ids=[memory_id])
            except Exception as e:
                logger.warning(f"memory remove {memory_id}: {e}")

    def search_with_status(self, query: str, k: int = 8) -> tuple[List[Dict], bool]:
        """Search for the most relevant memory IDs by semantic similarity.
        Return ``(results, query_healthy)`` so callers can distinguish a real
        empty result from a runtime vector outage.

        ChromaDB cosine distance = 1 - cosine_similarity.
        We convert back: similarity = 1.0 - distance.
        """
        if not self._healthy:
            return [], False

        out = []
        successful_lane = False
        lane_priority = {LANE_CUSTOM: 0, LANE_FASTEMBED: 1}
        for lane in self._lanes:
            try:
                if lane.count() == 0:
                    successful_lane = True
                    continue
                results = lane.collection.query(
                    query_embeddings=lane.encode([query]),
                    n_results=min(k, lane.count()),
                    include=["distances"],
                )
                successful_lane = True
                for idx, mid in enumerate(results["ids"][0]):
                    distance = results["distances"][0][idx]
                    out.append({
                        "memory_id": mid,
                        "score": round(1.0 - distance, 4),
                        "embedding_lane": lane.name,
                    })
            except Exception as e:
                logger.warning("memory search failed in %s lane: %s", lane.name, e)
        out.sort(key=lambda row: (-row["score"], lane_priority.get(row["embedding_lane"], 99)))
        return dedupe_results(out, id_key="memory_id", limit=k), successful_lane

    def search(self, query: str, k: int = 8) -> List[Dict]:
        """Compatibility search API returning only result rows."""
        results, _query_healthy = self.search_with_status(query, k=k)
        return results

    def find_similar(self, text: str, threshold: float = 0.92) -> Optional[str]:
        """Check if a near-duplicate exists. Returns memory_id if found, else None."""
        if not self._healthy or self.count() == 0:
            return None

        for lane in self._lanes:
            try:
                if lane.count() == 0:
                    continue
                results = lane.collection.query(
                    query_embeddings=lane.encode([text]),
                    n_results=1,
                    include=["distances"],
                )
                if results["ids"][0]:
                    distance = results["distances"][0][0]
                    similarity = 1.0 - distance
                    if similarity >= threshold:
                        return results["ids"][0][0]
            except Exception as e:
                logger.warning("memory similarity search failed in %s lane: %s", lane.name, e)
        return None

    def _replace_collections(self) -> None:
        """Delete every memory-vector collection and create fresh lanes."""
        names = {
            self.COLLECTION_NAME,
            collection_name(self.COLLECTION_NAME, LANE_CUSTOM),
            collection_name(self.COLLECTION_NAME, LANE_FASTEMBED),
        }
        # Older Chroma clients and the repository's supported lightweight
        # client contract do not expose ``list_collections``.  The collection
        # names are deterministic, so deleting each known name is both more
        # compatible and sufficient to prevent a stale inactive lane (or the
        # legacy collection) from being migrated back into a rebuild.
        self._delete_collection_names(names)

        self._lanes = build_embedding_lanes(self.COLLECTION_NAME)
        if not self._lanes:
            self._healthy = False
            self._collection = None
            raise RuntimeError("No embedding lanes available after memory-vector reset")
        self._collection = next(
            (lane.collection for lane in self._lanes if lane.name == LANE_FASTEMBED),
            self._lanes[0].collection,
        )
        self._healthy = True

    def clear(self, *, strict: bool = False) -> bool:
        """Clear all memory vectors.

        ``strict=True`` is used by persistence transactions: failures propagate
        so SQL/JSON state is not committed while stale vectors remain.
        """
        try:
            self._replace_collections()
            return True
        except Exception as e:
            self._healthy = False
            logger.error("memory vector clear failed: %s", e)
            if strict:
                raise
            return False

    def rebuild(self, memories: List[Dict], *, strict: bool = False) -> bool:
        """Rebuild the entire index from a list of memory entries.
        Each entry must have 'id' and 'text' keys."""
        try:
            # Set this before mutating Chroma. A crash, strict exception, or
            # failed cleanup must remain visible to the next process.
            self._mark_rebuild_incomplete()
            self._replace_collections()
        except Exception as e:
            self._healthy = False
            logger.error("memory rebuild reset failed: %s", e)
            if strict:
                raise
            return False

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
            failed_lanes = set()
            primary_error = None
            for i in range(0, len(texts), 100):
                batch_texts = texts[i:i + 100]
                batch_ids = ids[i:i + 100]
                for lane in self._lanes:
                    if lane.name in failed_lanes:
                        continue
                    try:
                        lane.collection.add(
                            ids=batch_ids,
                            embeddings=lane.encode(batch_texts),
                            documents=batch_texts,
                            metadatas=[{"source": "memory"}] * len(batch_ids),
                        )
                    except Exception as e:
                        failed_lanes.add(lane.name)
                        logger.warning("memory rebuild failed in %s lane: %s", lane.name, e)
                        if strict:
                            primary_error = e
                            break
                if primary_error is not None:
                    break

            if primary_error is not None:
                # Strict mode remains fail-fast, but first make every lane
                # non-authoritative: each may contain earlier successful
                # batches. Preserve the embedding/write exception even if
                # best-effort cleanup also fails; the marker then protects the
                # next process from adopting the residue.
                all_names = {
                    collection_name(self.COLLECTION_NAME, lane.name)
                    for lane in self._lanes
                }
                self._healthy = False
                self._lanes = []
                self._collection = None
                try:
                    self._delete_collection_names(all_names)
                except Exception as cleanup_error:
                    logger.error(
                        "memory rebuild cleanup failed after %s: %s",
                        primary_error,
                        cleanup_error,
                    )
                else:
                    try:
                        self._clear_rebuild_marker()
                    except Exception as marker_error:
                        logger.error("memory rebuild marker cleanup failed: %s", marker_error)
                raise primary_error

            if failed_lanes:
                # A non-strict lifecycle rebuild may degrade to any lane that
                # was rebuilt completely.  Exclude failed/partially populated
                # lanes and delete their durable collections so a restart
                # cannot mix incomplete state back in.
                surviving_lanes = [
                    lane for lane in self._lanes if lane.name not in failed_lanes
                ]
                failed_names = {
                    collection_name(self.COLLECTION_NAME, name)
                    for name in failed_lanes
                }
                try:
                    self._delete_collection_names(failed_names)
                except Exception as cleanup_error:
                    logger.error("memory rebuild partial-lane cleanup failed: %s", cleanup_error)
                    self._lanes = []
                    self._collection = None
                    self._healthy = False
                    return False
                self._lanes = surviving_lanes
                self._collection = next(
                    (lane.collection for lane in self._lanes if lane.name == LANE_FASTEMBED),
                    self._lanes[0].collection if self._lanes else None,
                )
                self._healthy = bool(self._lanes)

        try:
            self._clear_rebuild_marker()
        except Exception as e:
            # The index may be complete in this process, but leaving a durable
            # poison marker means it cannot be treated as committed.
            self._healthy = False
            self._lanes = []
            self._collection = None
            logger.error("memory rebuild marker cleanup failed: %s", e)
            if strict:
                raise
            return False

        logger.info(f"MemoryVectorStore rebuilt with {len(ids)} entries across {len(self._lanes)} lanes")
        return self._healthy

    def get_stats(self) -> Dict:
        return {
            "healthy": self.healthy,
            "count": self.count(),
            "lanes": [lane.stats() for lane in self._lanes],
        }
