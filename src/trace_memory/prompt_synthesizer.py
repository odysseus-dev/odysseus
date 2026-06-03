"""
PromptSynthesizer — TRACE's Multi-Path RAG Prompt Builder
==========================================================
Assembles the enriched system prompt that is injected before every LLM
call, weaving together:

  1. **Surgical Memory Context** — Cosine-searched topic summaries from the
     B+Tree. All nodes above the base similarity threshold are collected,
     ancestors are deduplicated, then the top-3 highest-scoring paths are
     surfaced for multi-path cross-branch retrieval.
  2. **Cross-Thread Conversation Recall** — Semantically similar past
     messages retrieved from the conversation vector table.
  3. **Verbatim Recent Log** — The last N raw messages for immediate context.

Usage
-----
    from trace_memory import CTree, VectorDatabase, PromptSynthesizer

    tree  = CTree(api_key="sk-...", model="gpt-4o-mini")
    vdb   = VectorDatabase("session.db")
    synth = PromptSynthesizer(tree, vdb)

    system_prompt = synth.synthesize_prompt(
        user_query      = "How do neutron stars form?",
        query_vector    = embed("How do neutron stars form?"),
        active_node     = tree.current_node,
        recent_messages = tree.conversation[-6:],
    )
    # Pass system_prompt as the system message to your LLM.
"""

import time
from datetime import datetime

from .ctree import CTree, TopicNode, MessageNode
from .vector_db import VectorDatabase, ConversationVector


class PromptSynthesizer:
    """
    Builds an enriched, RAG-grounded system prompt for every LLM turn.

    Parameters
    ----------
    ctree     : A live ``CTree`` instance.
    vector_db : The ``VectorDatabase`` associated with the session.
    """

    def __init__(self, ctree: CTree, vector_db: VectorDatabase):
        self.tree = ctree
        self.vdb  = vector_db

    # ── Internal: full-tree narrative (fallback) ──────────────────────────────

    def _traverse(self, node, depth: int = 0):
        lines  = []
        indent = "  " * depth
        if isinstance(node, TopicNode) and node.topic_name != "ROOT":
            summary = (node.summary or "Active discussion in progress.").strip()
            lines.append(f"{indent}• {node.topic_name} [msgs {node.start_index}–{node.end_index}]")
            lines.append(f"{indent}  ↳ {summary}")
        for child in node.children:
            if isinstance(child, TopicNode):
                lines.extend(self._traverse(child, depth + 1))
        return lines

    def _get_active_narrative_context(self, active_node) -> str:
        blocks = ["── GLOBAL CONVERSATION MEMORY INDEX ──"]
        summaries = self._traverse(self.tree.root)
        blocks.extend(summaries if summaries else ["  (No topics indexed yet)"])
        blocks.append("\n── ACTIVE THEMATIC CONTEXT PATH ──")
        ancestors = self.tree.get_ancestors(active_node, include_self=True, exclude_root=True)
        if ancestors:
            path = " → ".join(n.topic_name for n in ancestors)
            blocks.append(f"Thread: {path}")
            for n in ancestors:
                s = (n.summary or "Expanding context…").strip()
                blocks.append(f"  • {n.topic_name}: {s}")
        else:
            blocks.append("  Thread: ROOT (first topic in progress)")
        return "\n".join(blocks)

    # ── Internal: surgical multi-path retrieval ───────────────────────────────

    def _build_node_id_map(self) -> dict:
        result = {}
        def _walk(node):
            if isinstance(node, TopicNode) and hasattr(node, "node_id"):
                result[node.node_id] = node
            for child in node.children:
                _walk(child)
        _walk(self.tree.root)
        return result

    def _get_surgical_context(self, query_vector) -> str:
        """
        Multi-path surgical retrieval (the heart of TRACE):

        1. Cosine-search the VDB for ALL topic summaries above the base threshold.
        2. Walk full ancestry for every qualifying node.
        3. Deduplicate shared ancestor nodes across all paths.
        4. Rank deduplicated paths by similarity and take the top-3.
        5. Format a compact merged multi-path context block.

        Falls back to full-tree narrative if no hits qualify.
        """
        if not query_vector or self.vdb is None:
            return self._get_active_narrative_context(self.tree.current_node)
        try:
            # Fetch all nodes above the base threshold (large top_k to capture everything)
            hits = self.vdb.search_topic_summaries(query_vector, top_k=50, min_similarity=0.30)
        except Exception:
            return self._get_active_narrative_context(self.tree.current_node)
        if not hits:
            return self._get_active_narrative_context(self.tree.current_node)

        node_map          = self._build_node_id_map()
        all_ancestor_sets = []  # [(similarity, [TopicNode, ...])]
        for hit in hits:
            node = node_map.get(hit["node_id"])
            if node is None:
                continue
            ancestors = self.tree.get_ancestors(node, include_self=True, exclude_root=True)
            if ancestors:
                all_ancestor_sets.append((hit["similarity"], ancestors))

        if not all_ancestor_sets:
            return self._get_active_narrative_context(self.tree.current_node)

        # Deduplicate shared ancestors across ALL qualifying paths
        seen_ids             = set()
        unique_nodes_ordered = []
        for similarity, ancestors in all_ancestor_sets:
            for node in ancestors:
                nid = getattr(node, "node_id", id(node))
                if nid not in seen_ids:
                    seen_ids.add(nid)
                    unique_nodes_ordered.append((node, similarity))

        # Now rank paths by similarity and take the top-3
        all_ancestor_sets_ranked = sorted(all_ancestor_sets, key=lambda x: x[0], reverse=True)[:3]

        blocks = ["── SURGICAL MEMORY CONTEXT (top matched topics + ancestry) ──"]
        for i, (similarity, ancestors) in enumerate(all_ancestor_sets_ranked, 1):
            path = " → ".join(n.topic_name for n in ancestors)
            blocks.append(f"Match {i} (confidence {similarity * 100:.0f}%): {path}")
        blocks.append("")
        blocks.append("── ANCESTRY DETAIL ──")
        for node, _ in unique_nodes_ordered:
            s = (node.summary or "Context expanding…").strip()
            blocks.append(f"  • {node.topic_name}: {s}")
        return "\n".join(blocks)

    # ── Public: synthesize_prompt ─────────────────────────────────────────────

    def synthesize_prompt(
        self,
        user_query:             str,
        query_vector:           list,
        active_node,
        recent_messages:        list,
        top_k_history:          int   = 2,
        min_history_similarity: float = 0.50,
    ) -> str:
        """
        Assemble the full enriched system prompt for the next LLM turn.

        Parameters
        ----------
        user_query             : The raw text of the user's current message.
        query_vector           : Embedding of *user_query*.
        active_node            : ``tree.current_node``.
        recent_messages        : Tail of ``tree.conversation`` (e.g. last 6 msgs).
        top_k_history          : Max past conversation messages to recall.
        min_history_similarity : Minimum cosine score for conversation recall.

        Returns
        -------
        str — A ready-to-use system prompt string.  Pass this as the
              ``system`` role message to your LLM.

        Example
        -------
            prompt = synth.synthesize_prompt(
                user_query      = "Is the cake safe for Sarah?",
                query_vector    = embed("Is the cake safe for Sarah?"),
                active_node     = tree.current_node,
                recent_messages = tree.conversation[-6:],
            )
            response = openai_client.chat.completions.create(
                model    = "gpt-4o",
                messages = [
                    {"role": "system", "content": prompt},
                    {"role": "user",   "content": "Is the cake safe for Sarah?"},
                ],
            )
        """
        # 1. Surgical multi-path memory context
        narrative_block  = self._get_surgical_context(query_vector)

        # 2. Cross-thread conversation recall
        history_matches = self.vdb.search_conversation(
            query_vector   = query_vector,
            top_k          = top_k_history,
            min_similarity = min_history_similarity,
        )
        recall_block = ""
        valid_recalls = [h for h in history_matches if h.text.strip() != user_query.strip()]
        if valid_recalls:
            recall_block = "\n── SUBCONSCIOUS CROSS-THREAD CONVERSATION RECALL ──\n"
            for msg in valid_recalls:
                time_str    = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(msg.timestamp))
                recall_block += (
                    f"Branch Path: {msg.thread_path} (Recall Strength: {msg.similarity * 100:.1f}%)\n"
                    f"Context [Role: {msg.role.upper()} | Time: {time_str}]:\n"
                    f"\"{msg.text.strip()}\"\n"
                    + "─" * 50 + "\n"
                )

        # 3. Verbatim recent log
        recent_block = "── RECENT CONVERSATION LOGS (VERBATIM) ──\n"
        if recent_messages:
            for msg in recent_messages:
                role    = msg.get("role", "unknown").upper()
                content = msg.get("content", "")
                if isinstance(content, str) and len(content) > 600:
                    content = content[:600] + "… [truncated]"
                elif isinstance(content, list):
                    content = "[multimodal message]"
                recent_block += f"[{role}]: {content}\n"
        else:
            recent_block += "  (No messages yet)\n"

        # Assemble final prompt
        now_str = datetime.now().strftime("%A, %d %B %Y, %I:%M %p")
        base = (
            f"Today's date and time: {now_str}\n"
            "Your training data may be outdated — always treat today's date above as ground truth.\n"
            "If web search results are provided, treat them as the most current and accurate source.\n\n"
            "You are a sharp, knowledgeable AI assistant. "
            "Answer the user's current message directly and helpfully.\n\n"
            "STRICT RULES (never break these):\n"
            "- NEVER mention topics, branches, threads, memory trees, or any internal system.\n"
            "- NEVER ask the user if they want to switch topics or continue a previous topic.\n"
            "- If the user asks about something new, just answer it.\n"
            "- Use the context below ONLY as silent background knowledge to stay coherent.\n"
            "- Stay on what the user actually asked. No preamble.\n"
            "- Provide detailed, comprehensive answers.\n\n"
        )
        return (
            base
            + narrative_block + "\n\n"
            + recall_block
            + recent_block + "\n"
            + "Now answer the user's latest message directly, as a knowledgeable friend would."
        )
