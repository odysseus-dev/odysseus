"""Prompt synthesizer — multi-path retrieval and context formatting.

TRACE-inspired surgical retrieval: embed query, search topic summaries,
walk ancestry, deduplicate, rank, and format a compact context block.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from src.embeddings import get_embedding_client
from src.chroma_client import get_chroma_client

logger = logging.getLogger(__name__)

COLLECTION_NAME = "odysseus_episodes"


class PromptSynthesizer:
    """Retrieves episodic context by semantic topic search + tree traversal."""

    def __init__(self, episodic_tree, max_paths: int = 3, max_tokens_per_path: int = 500):
        self.tree = episodic_tree
        self.max_paths = max_paths
        self.max_tokens_per_path = max_tokens_per_path
        self._collection = None
        self._embed = None
        self._init_collection()

    def _init_collection(self) -> None:
        try:
            client = get_chroma_client()
            self._collection = client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
            model = get_embedding_client()
            if model is None:
                raise RuntimeError("No embedding backend available")
            self._embed = model
        except Exception as e:
            logger.warning("PromptSynthesizer ChromaDB init failed: %s", e)

    @property
    def healthy(self) -> bool:
        return self._collection is not None and self._embed is not None

    # --------------------------------------------------------------------- #
    # Embedding helpers
    # --------------------------------------------------------------------- #

    def _embed_text(self, text: str) -> List[float]:
        if self._embed is None:
            raise RuntimeError("Embedding model not available")
        vecs = self._embed.encode([text], normalize_embeddings=True)
        return vecs.tolist()[0]

    def sync_topic_embeddings(self) -> None:
        """Ensure every topic in the tree has an embedding in ChromaDB."""
        if not self.healthy:
            logger.debug("PromptSynthesizer not healthy, skipping embedding sync")
            return

        topics = self.tree.get_all_topic_texts()
        existing_ids = set()
        try:
            result = self._collection.get()
            existing_ids = set(result["ids"])
        except Exception:
            pass

        to_add = []
        for tid, text in topics:
            if tid not in existing_ids and text.strip():
                to_add.append((tid, text))

        if not to_add:
            return

        texts = [t[1] for t in to_add]
        ids = [t[0] for t in to_add]
        embeddings = self._embed.encode(texts, normalize_embeddings=True).tolist()

        # Batch in chunks of 100
        for i in range(0, len(ids), 100):
            batch_ids = ids[i:i + 100]
            batch_embeddings = embeddings[i:i + 100]
            batch_texts = texts[i:i + 100]
            self._collection.add(
                ids=batch_ids,
                embeddings=batch_embeddings,
                documents=batch_texts,
                metadatas=[{"source": "episode_topic"}] * len(batch_ids),
            )
        logger.debug("Synced %d topic embeddings", len(ids))

    # --------------------------------------------------------------------- #
    # Retrieval
    # --------------------------------------------------------------------- #

    async def retrieve(self, query: str, top_k: int = 5) -> str:
        """Return a compact context string for the LLM prompt."""
        paths = self._retrieve_paths(query, top_k=top_k)
        if not paths:
            return ""
        return self._format_paths(paths)

    def _retrieve_paths(
        self,
        query: str,
        top_k: int = 5,
    ) -> List[Tuple[float, List[Dict[str, Any]]]]:
        """Return ranked list of (score, path_nodes) for the query."""
        if not self.healthy:
            # Degrade to keyword search over topic names
            return self._keyword_fallback(query, top_k)

        try:
            embedding = self._embed_text(query)
            actual_k = min(top_k, max(1, self._collection.count()))
            results = self._collection.query(
                query_embeddings=[embedding],
                n_results=actual_k,
            )
        except Exception as e:
            logger.warning("ChromaDB query failed: %s", e)
            return self._keyword_fallback(query, top_k)

        scored_paths = []
        seen: set = set()

        for idx, tid in enumerate(results["ids"][0]):
            distance = results["distances"][0][idx]
            score = round(1.0 - distance, 4)

            ancestors = self.tree.get_ancestors(tid)
            path_key = "->".join(a.id for a in ancestors)
            if path_key in seen:
                continue
            seen.add(path_key)

            path_nodes = [a.to_dict() for a in ancestors]
            scored_paths.append((score, path_nodes))

        scored_paths.sort(key=lambda x: x[0], reverse=True)
        return scored_paths[:self.max_paths]

    def _keyword_fallback(
        self,
        query: str,
        top_k: int = 5,
    ) -> List[Tuple[float, List[Dict[str, Any]]]]:
        """Degrade gracefully when ChromaDB is unavailable."""
        q = query.lower()
        matches = []
        for tid, topic in self.tree.topics.items():
            text = f"{topic.topic_name} {topic.summary}".lower()
            if q in text:
                ancestors = self.tree.get_ancestors(tid)
                path_nodes = [a.to_dict() for a in ancestors]
                matches.append((0.5, path_nodes))
        matches.sort(key=lambda x: x[0], reverse=True)
        return matches[:self.max_paths]

    # --------------------------------------------------------------------- #
    # Formatting
    # --------------------------------------------------------------------- #

    def _format_paths(
        self,
        paths: List[Tuple[float, List[Dict[str, Any]]]],
    ) -> str:
        """Format ranked paths into a compact XML block."""
        lines = ["<episodic_memory>"]
        for score, nodes in paths:
            topic_name = nodes[-1].get("topic_name", "Unknown") if nodes else "Unknown"
            lines.append(f'  <topic score="{score}">')
            for node in nodes:
                name = node.get("topic_name", "")
                summary = node.get("summary", "").strip()
                if summary:
                    lines.append(f"    [{name}] {summary}")
                else:
                    lines.append(f"    [{name}]")
            # Add recent messages from the leaf topic
            leaf_id = nodes[-1]["id"] if nodes else None
            if leaf_id:
                msgs = self.tree.get_topic_messages(leaf_id)
                for m in msgs[-3:]:
                    role = "user" if m.role == "user" else "assistant"
                    lines.append(f"    <{role}>{m.text[:200]}</{role}>")
            lines.append("  </topic>")
        lines.append("</episodic_memory>")
        return "\n".join(lines)
