"""
skill_vector.py

ChromaDB-backed vector store for skill entries (semantic search over when_to_use).
Shares the EmbeddingClient with RAG to save memory.
Stores pre-computed embeddings (ChromaDB does not manage embedding).
"""

import logging
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


class SkillVectorStore:
    """Vector index over skill entries for semantic retrieval via when_to_use."""

    COLLECTION_NAME = "odysseus_skills"

    def __init__(self, data_dir: str, embedding_model=None):
        self._model = embedding_model
        self._collection = None
        self._lanes = []
        self._healthy = False

        self._initialize()

    def _initialize(self):
        try:
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
                "SkillVectorStore ready (lanes=%s entries=%s)",
                [lane.name for lane in self._lanes],
                self.count(),
            )

        except Exception as e:
            logger.error(f"SkillVectorStore init failed: {e}")

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

    def add(self, skill_id: str, when_to_use: str):
        """Add a single skill entry to the vector index."""
        if not self._healthy:
            return
        for lane in self._lanes:
            try:
                existing = lane.collection.get(ids=[skill_id])
                if existing["ids"]:
                    continue
                lane.collection.add(
                    ids=[skill_id],
                    embeddings=lane.encode([when_to_use]),
                    documents=[when_to_use],
                    metadatas=[{"source": "skill"}],
                )
            except Exception as e:
                logger.warning("skill add failed in %s lane for %s: %s", lane.name, skill_id, e)

    def remove(self, skill_id: str):
        """Remove a skill entry. O(1) — no rebuild needed."""
        if not self._healthy:
            return
        for collection in self._collections_for_delete():
            try:
                collection.delete(ids=[skill_id])
            except Exception as e:
                logger.warning(f"skill remove {skill_id}: {e}")

    def search(self, query: str, k: int = 8) -> List[Dict]:
        """Search for the most relevant skill IDs by semantic similarity.
        Returns list of {"skill_id": str, "score": float}.

        ChromaDB cosine distance = 1 - cosine_similarity.
        We convert back: similarity = 1.0 - distance.
        """
        if not self._healthy or self.count() == 0:
            return []

        out = []
        lane_priority = {LANE_CUSTOM: 0, LANE_FASTEMBED: 1}
        for lane in self._lanes:
            try:
                if lane.count() == 0:
                    continue
                results = lane.collection.query(
                    query_embeddings=lane.encode([query]),
                    n_results=min(k, lane.count()),
                    include=["distances"],
                )
                for idx, sid in enumerate(results["ids"][0]):
                    distance = results["distances"][0][idx]
                    out.append({
                        "skill_id": sid,
                        "score": round(1.0 - distance, 4),
                        "embedding_lane": lane.name,
                    })
            except Exception as e:
                logger.warning("skill search failed in %s lane: %s", lane.name, e)
        out.sort(key=lambda row: (-row["score"], lane_priority.get(row["embedding_lane"], 99)))
        return dedupe_results(out, id_key="skill_id", limit=k)

    def rebuild(self, skills: List[Dict]):
        """Rebuild the entire index from a list of skill entries.
        Each entry must have 'name' (used as id) and 'when_to_use' keys."""
        if not self._healthy:
            return

        from src.chroma_client import get_chroma_client

        client = get_chroma_client()
        lane_names = [
            self.COLLECTION_NAME,
            collection_name(self.COLLECTION_NAME, LANE_CUSTOM),
            collection_name(self.COLLECTION_NAME, LANE_FASTEMBED),
        ]
        for name in lane_names:
            try:
                client.delete_collection(name)
            except Exception:
                pass
        # Explicit rebuilds must start from the supplied skill list, so clear
        # legacy unsuffixed collections too.
        self._lanes = build_embedding_lanes(self.COLLECTION_NAME)
        self._collection = next(
            (lane.collection for lane in self._lanes if lane.name == LANE_FASTEMBED),
            self._lanes[0].collection if self._lanes else None,
        )

        texts = []
        ids = []
        for sk in skills:
            text = sk.get("when_to_use", "").strip()
            sid = sk.get("name", "")
            if text and sid:
                texts.append(text)
                ids.append(sid)

        if texts:
            # Batch in chunks of 100 to avoid oversized requests
            failed_lanes = set()
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
                            metadatas=[{"source": "skill"}] * len(batch_ids),
                        )
                    except Exception as e:
                        failed_lanes.add(lane.name)
                        logger.warning("skill rebuild failed in %s lane: %s", lane.name, e)

        logger.info(f"SkillVectorStore rebuilt with {len(ids)} entries across {len(self._lanes)} lanes")

    def get_stats(self) -> Dict:
        return {
            "healthy": self.healthy,
            "count": self.count(),
            "lanes": [lane.stats() for lane in self._lanes],
        }