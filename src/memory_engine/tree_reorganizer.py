"""Tree reorganizer — background consolidation, merge, prune.

Runs when the agent is idle or after N new messages.  Merges semantically
related topic branches and prunes trivial leaves.  Updates ChromaDB embeddings.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from src.memory_engine.episodic_tree import EpisodicTree, TopicNode

logger = logging.getLogger(__name__)


class TreeReorganizer:
    """Background task for episodic tree maintenance."""

    def __init__(
        self,
        episodic_tree: EpisodicTree,
        *,
        similarity_threshold: float = 0.7,
        max_branch_depth: int = 8,
        min_messages_per_topic: int = 2,
        llm_summarizer: Optional[Callable[..., Any]] = None,
    ):
        self.tree = episodic_tree
        self.similarity_threshold = similarity_threshold
        self.max_branch_depth = max_branch_depth
        self.min_messages_per_topic = min_messages_per_topic
        self.llm_summarizer = llm_summarizer

    # --------------------------------------------------------------------- #
    # Public entry point
    # --------------------------------------------------------------------- #

    async def run(self) -> Dict[str, Any]:
        """Perform one pass of tree reorganization.

        Returns stats dict with counts of merged/pruned topics.
        """
        stats = {"merged": 0, "pruned": 0, "summarized": 0}

        try:
            stats["merged"] = await self._merge_related_topics()
            stats["pruned"] = self._prune_trivial_leaves()
            stats["summarized"] = await self._summarize_merged_branches()
        except Exception as e:
            logger.error("TreeReorganizer error: %s", e, exc_info=True)

        if any(stats.values()):
            self.tree.save()

        return stats

    # --------------------------------------------------------------------- #
    # Merge
    # --------------------------------------------------------------------- #

    async def _merge_related_topics(self) -> int:
        """Find sibling topic pairs with high semantic overlap and merge them.

        Returns number of topics merged.
        """
        merged_count = 0
        topics = list(self.tree.topics.values())

        # Group by parent
        by_parent: Dict[str, List[TopicNode]] = {}
        for t in topics:
            if t.id == self.tree._root_id:
                continue
            parent = t.parent_id or self.tree._root_id
            by_parent.setdefault(parent, []).append(t)

        for parent_id, siblings in by_parent.items():
            if len(siblings) < 2:
                continue

            # Pairwise similarity using Jaccard on topic text
            for i in range(len(siblings)):
                for j in range(i + 1, len(siblings)):
                    a, b = siblings[i], siblings[j]
                    if a.id not in self.tree.topics or b.id not in self.tree.topics:
                        continue

                    sim = self._topic_similarity(a, b)
                    if sim >= self.similarity_threshold:
                        self._merge_two(a, b)
                        merged_count += 1

        return merged_count

    def _topic_similarity(self, a: TopicNode, b: TopicNode) -> float:
        """Compute overlap between two topics."""
        from src.memory_engine.episodic_tree import _jaccard

        text_a = f"{a.topic_name} {a.summary}"
        text_b = f"{b.topic_name} {b.summary}"
        sim = _jaccard(text_a, text_b)

        # Boost by keyword overlap
        if a.keywords and b.keywords:
            overlap = len(a.keywords & b.keywords) / max(len(a.keywords), len(b.keywords))
            sim = max(sim, overlap)

        return sim

    def _merge_two(self, keeper: TopicNode, victim: TopicNode) -> None:
        """Merge victim into keeper: combine messages, update children."""
        # Combine summaries
        parts = [p for p in [keeper.summary, victim.summary] if p.strip()]
        keeper.summary = " | ".join(parts)[:500]

        # Combine keywords
        keeper.keywords.update(victim.keywords)

        # Update message range
        msgs = self.tree.get_topic_messages(victim.id)
        for m in msgs:
            m.topic_id = keeper.id

        if victim.message_start and victim.message_end:
            if keeper.message_start == 0:
                keeper.message_start = victim.message_start
            else:
                keeper.message_start = min(keeper.message_start, victim.message_start)
            keeper.message_end = max(keeper.message_end, victim.message_end)

        # Adopt children
        for child_id in victim.children_ids:
            child = self.tree.topics.get(child_id)
            if child:
                child.parent_id = keeper.id
                if child_id not in keeper.children_ids:
                    keeper.children_ids.append(child_id)

        # Remove victim from parent's children list
        parent = self.tree.topics.get(victim.parent_id)
        if parent and victim.id in parent.children_ids:
            parent.children_ids.remove(victim.id)

        # Delete victim
        del self.tree.topics[victim.id]
        logger.debug("Merged topic %s into %s", victim.id, keeper.id)

    # --------------------------------------------------------------------- #
    # Prune
    # --------------------------------------------------------------------- #

    def _prune_trivial_leaves(self) -> int:
        """Remove single-message leaves with no children.

        Returns number of topics pruned.
        """
        pruned = 0
        to_remove = []

        for tid, topic in list(self.tree.topics.items()):
            if tid == self.tree._root_id:
                continue
            if not topic.children_ids:
                msgs = self.tree.get_topic_messages(tid)
                if len(msgs) < self.min_messages_per_topic:
                    to_remove.append(tid)

        for tid in to_remove:
            topic = self.tree.topics.get(tid)
            if not topic:
                continue
            parent = self.tree.topics.get(topic.parent_id)
            if parent and tid in parent.children_ids:
                parent.children_ids.remove(tid)
            del self.tree.topics[tid]
            pruned += 1
            logger.debug("Pruned trivial topic %s", tid)

        return pruned

    # --------------------------------------------------------------------- #
    # Summarize
    # --------------------------------------------------------------------- #

    async def _summarize_merged_branches(self) -> int:
        """Generate LLM summaries for topics that lack them.

        Returns number of topics summarized.
        """
        if not self.llm_summarizer:
            return 0

        summarized = 0
        for tid, topic in list(self.tree.topics.items()):
            if topic.summary.strip():
                continue

            msgs = self.tree.get_topic_messages(tid)
            if len(msgs) < self.min_messages_per_topic:
                continue

            snippet = "\n".join(f"{m.role}: {m.text[:200]}" for m in msgs[-5:])
            prompt = (
                "Summarize the following conversation topic in 1-2 sentences.\n\n"
                f"{snippet}\n\nSummary:"
            )
            try:
                summary = await self.llm_summarizer(prompt)
                topic.summary = summary.strip()[:500]
                summarized += 1
            except Exception as e:
                logger.warning("Failed to summarize topic %s: %s", tid, e)

        return summarized
