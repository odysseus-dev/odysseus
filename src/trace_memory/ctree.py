"""
CTree — Hierarchical B+Tree Conversation Memory
================================================
The core data structure of TRACE.  Organises every LLM conversation
exchange into a tree of named topic branches, with:

  • Automatic LLM-driven topic detection and branch assignment.
  • Lazy summary generation for frozen (inactive) branches.
  • Vector-DB integration for surgical multi-path retrieval.
  • A four-rule-guarded self-healing reorganization pass.
  • Soft archival of trivial leaf messages.
  • Full JSON serialisation / deserialisation.

Typical usage
-------------
    from trace import CTree, VectorDatabase, PromptSynthesizer

    # 1. Boot the engine
    tree = CTree(api_key="sk-...", model="gpt-4o-mini")
    vdb  = VectorDatabase("my_session.db")
    tree.vdb = vdb

    # 2. Wire up the embed function (any callable: text -> List[float])
    tree.embed_fn = my_embed_function

    # 3. Add exchanges after getting a response from your LLM
    tree.add([
        {"role": "user",      "content": "Tell me about black holes."},
        {"role": "assistant", "content": "Black holes are regions ..."},
    ])

    # 4. Build the enriched system prompt for the next turn
    synth  = PromptSynthesizer(tree, vdb)
    prompt = synth.synthesize_prompt(
        user_query     = "How do Hawking radiation work?",
        query_vector   = my_embed_function("How do Hawking radiation work?"),
        active_node    = tree.current_node,
        recent_messages= tree.conversation[-6:],
    )

    # 5. Persist
    tree.save("my_session.json", save_conversation=True)

    # 6. Reload later
    tree = CTree.load("my_session.json", api_key="sk-...")
"""

import json
import uuid
import time
import os

from typing import List, Dict, Optional, Tuple, Any, Callable
from dataclasses import dataclass, field

from ._llm_utils import ChatGPT_API, extract_json

# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_int(value, fallback):
    if value is None:
        return fallback
    if isinstance(value, str) and (value.strip().upper() == "N/A" or not value.strip()):
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


# ── Node base ─────────────────────────────────────────────────────────────────

class Node:
    """
    Abstract base for all tree nodes.

    Attributes
    ----------
    children       : Direct child nodes.
    parent         : Parent node (None for root).
    sub_node_count : Cached count of direct children.
    created_at     : Unix timestamp of node birth (set once, never changed).
    """

    def __init__(self, children=None, parent=None, sub_node_count=0):
        self.children: List["Node"] = children or []
        self.parent: Optional["Node"] = parent
        self.sub_node_count: int = sub_node_count
        self.created_at: float = time.time()

    def update_sub_node_count(self):
        """Propagate child-count changes up the ancestor chain."""
        self.sub_node_count = len(self.children)
        if self.parent is not None:
            self.parent.update_sub_node_count()

    def is_leaf(self) -> bool:
        return len(self.children) == 0

    def to_dict(self) -> dict:
        raise NotImplementedError("Subclasses must implement to_dict()")


# ── MessageNode ───────────────────────────────────────────────────────────────

class MessageNode(Node):
    """
    A leaf node holding one user/assistant exchange (optionally a system
    message that preceded it).

    Attributes
    ----------
    user_message      : {"role": "user",      "content": "..."}
    assistant_message : {"role": "assistant", "content": "..."}
    system_message    : {"role": "system",    "content": "..."}  or None
    message_index     : Index of the user message inside ``CTree.conversation``.
    """

    def __init__(
        self,
        user_message=None,
        assistant_message=None,
        system_message=None,
        message_index: int = 0,
        parent=None,
    ):
        super().__init__(parent=parent)
        self.user_message:      dict = user_message      or {}
        self.assistant_message: dict = assistant_message or {}
        self.system_message:    Optional[dict] = system_message
        self.message_index:     int  = message_index

    def to_dict(self) -> dict:
        result = {
            "type":          "message",
            "message_index": self.message_index,
            "user":          self.user_message.get("content", "")[:200],
            "assistant":     self.assistant_message.get("content", "")[:200],
            "sub_node_count": self.sub_node_count,
        }
        if self.system_message is not None:
            result["system"] = self.system_message.get("content", "")[:200]
        return result

    def __repr__(self):
        u = self.user_message.get("content", "")[:40]
        a = self.assistant_message.get("content", "")[:40]
        return f"MessageNode([{self.message_index}]: U:{u!r}… A:{a!r}…)"


# ── TopicNode ─────────────────────────────────────────────────────────────────

class TopicNode(Node):
    """
    An internal branch node representing a coherent conversation topic.

    Attributes
    ----------
    topic_name  : Short human-readable label (e.g. "Hawking Radiation").
    summary     : LLM-generated 1–2 sentence summary (filled lazily on freeze).
    start_index : First index in ``CTree.conversation`` that belongs to this topic.
    end_index   : Exclusive last index.
    node_id     : Stable UUID — generated once, persisted across save/load cycles.
    """

    def __init__(
        self,
        topic_name: str = "",
        summary:    str = "",
        start_index: int = 0,
        end_index:   int = 0,
        parent=None,
    ):
        super().__init__(parent=parent)
        self.topic_name:  str = topic_name
        self.summary:     str = summary
        self.start_index: int = start_index
        self.end_index:   int = end_index
        self.node_id:     str = str(uuid.uuid4())

    def get_message_count(self) -> int:
        if self.is_leaf():
            return self.end_index - self.start_index
        count = 0
        for child in self.children:
            if isinstance(child, MessageNode):
                count += 1
            elif isinstance(child, TopicNode):
                count += child.get_message_count()
        return count

    def to_dict(self) -> dict:
        return {
            "type":          "topic",
            "node_id":       self.node_id,
            "topic_name":    self.topic_name,
            "summary":       self.summary,
            "start_index":   self.start_index,
            "end_index":     self.end_index,
            "sub_node_count": self.sub_node_count,
            "created_at":    self.created_at,
            "children":      [c.to_dict() for c in self.children],
        }

    def __repr__(self):
        return (
            f"TopicNode({self.topic_name!r}, "
            f"[{self.start_index}:{self.end_index}], "
            f"{self.sub_node_count} children)"
        )


# ── CTree ─────────────────────────────────────────────────────────────────────

class CTree:
    """
    The TRACE hierarchical conversation memory tree.

    Parameters
    ----------
    max_children   : Max child nodes before a node is split (default 5).
    api_key        : LLM API key.  Falls back to OPENAI_API_KEY env var.
    model          : LLM model ID used for topic detection, summarisation, etc.
    auto_save_path : If set, the tree auto-saves to this path after every
                     ``add()`` call.

    Injectable dependencies
    -----------------------
    tree.vdb       : A ``VectorDatabase`` instance for embedding-backed recall.
    tree.embed_fn : Callable[str, List[float]] — your embedding function.

    Example
    -------
        import openai

        client = openai.OpenAI(api_key="sk-...", base_url="http://...")

        def embed(text):
            r = client.embeddings.create(input=[text], model="nomic-embed-text")
            return r.data[0].embedding

        tree = CTree(api_key="sk-...", model="gpt-4o-mini")
        tree.embed_fn = embed
    """

    def __init__(
        self,
        max_children: int = 5,
        api_key:      str = None,
        model:        str = "gpt-4o-mini",
        auto_save_path: str = None,
        embed_fn:     Optional[Callable[[str], List[float]]] = None,
    ):
        self.max_children   = max_children
        self.model          = model
        self.conversation:  List[dict]      = []
        self.auto_save_path: Optional[str]  = auto_save_path
        self._archived_nodes: List[MessageNode] = []
        self.vdb            = None   # inject: VectorDatabase instance
        self.embed_fn       = embed_fn   # inject: callable(text) -> List[float]

        # API key resolution
        if api_key:
            self.api_key = api_key
        elif os.getenv("OPENAI_API_KEY"):
            self.api_key = os.getenv("OPENAI_API_KEY")
        else:
            raise ValueError(
                "API key must be provided via api_key= or the "
                "OPENAI_API_KEY environment variable."
            )

        self.root: TopicNode = TopicNode(
            topic_name="ROOT",
            summary="Virtual root node of the conversation tree",
            start_index=0,
            end_index=0,
        )
        self.current_node: TopicNode = self.root

    # ── Ancestry helpers ──────────────────────────────────────────────────────

    def get_ancestors(
        self,
        node,
        include_self: bool = True,
        exclude_root: bool = False,
    ) -> List[TopicNode]:
        """
        Return the ordered list of ancestor ``TopicNode`` objects from root
        down to *node*.

        Parameters
        ----------
        node         : Starting node.
        include_self : If True, include *node* itself in the result.
        exclude_root : If True, omit the virtual ROOT node.

        Returns
        -------
        List[TopicNode] in root-to-leaf order.
        """
        ancestors = []
        current = node if include_self else node.parent
        while current is not None:
            ancestors.insert(0, current)
            current = current.parent
        if exclude_root:
            ancestors = [n for n in ancestors if n.topic_name != "ROOT"]
        return ancestors

    def _is_frozen_node(self, node) -> bool:
        """
        A node is *frozen* when neither it nor any of its ancestors is the
        currently active node.  Frozen nodes are safe to summarise and merge.
        """
        if node is self.current_node:
            return False
        ancestors = self.get_ancestors(self.current_node, include_self=False)
        return node not in ancestors

    # ── Summarisation & VDB upsert ────────────────────────────────────────────

    def _generate_summaries_for_frozen_nodes(self, node=None):
        """
        Walk the tree and LLM-summarise every frozen TopicNode that has no
        summary yet.  Also embeds each new summary into the VDB.
        """
        if node is None:
            node = self.root
        if isinstance(node, TopicNode):
            if node is not self.root and self._is_frozen_node(node):
                if not node.summary or not node.summary.strip():
                    messages = self.conversation[node.start_index : node.end_index]
                    if messages:
                        node.summary = self._llm_summarize(messages, node.topic_name)
                        self._upsert_node_to_vdb(node)
        if hasattr(node, "children"):
            for child in node.children:
                self._generate_summaries_for_frozen_nodes(child)

    def _upsert_node_to_vdb(self, node, depth=None):
        """Embed a TopicNode summary and upsert it into the VDB topic table."""
        if self.vdb is None or self.embed_fn is None:
            return
        if not node.summary or not node.node_id:
            return
        try:
            embedding = self.embed_fn(node.summary)
            self.vdb.upsert_topic_summary(
                node_id     = node.node_id,
                topic_name  = node.topic_name,
                summary     = node.summary,
                embedding   = embedding,
                start_index = node.start_index,
                end_index   = node.end_index,
                depth       = depth,
            )
        except Exception:
            pass  # embedding failures must never crash the tree

    # ── Public: add exchange ──────────────────────────────────────────────────

    def add(self, messages: List[dict]):
        """
        Ingest one completed user/assistant exchange (and optional system
        message) into the tree.

        Parameters
        ----------
        messages : A list of dicts, each with ``"role"`` and ``"content"``.
                   Must include at least one ``"user"`` and one
                   ``"assistant"`` message.

        Raises
        ------
        ValueError : If the exchange is missing a user or assistant message.

        Example
        -------
            tree.add([
                {"role": "user",      "content": "What is a neutron star?"},
                {"role": "assistant", "content": "A neutron star is ..."},
            ])

            # With a system message (e.g. tool-call context)
            tree.add([
                {"role": "system",    "content": "Search result: ..."},
                {"role": "user",      "content": "Summarise that."},
                {"role": "assistant", "content": "Here is a summary: ..."},
            ])
        """
        system_msg = assistant_msg = user_msg = None
        for m in messages:
            role = m.get("role")
            if role == "system":
                system_msg = m
            elif role == "user":
                user_msg = m
            elif role == "assistant":
                assistant_msg = m

        if not user_msg or not assistant_msg:
            raise ValueError("Each exchange must contain a 'user' and 'assistant' message.")

        msg_dict = {
            "system":    system_msg,
            "user":      user_msg,
            "assistant": assistant_msg,
            "index":     len(self.conversation),
        }

        for msg in messages:
            self.conversation.append(msg)

        if len(self.root.children) == 0:
            self._initialize_first_topic_with_message(msg_dict)
        else:
            self._add_message(msg_dict)

        if self.auto_save_path:
            self.save(self.auto_save_path, save_conversation=True)

    # ── Internal: topic routing ───────────────────────────────────────────────

    def _initialize_first_topic_with_message(self, msg_dict):
        topic_name = self._llm_generate_topic_from_message(
            msg_dict["user"], msg_dict["assistant"], system_msg=msg_dict["system"]
        )
        msg_count = 3 if msg_dict["system"] else 2
        first_topic = TopicNode(
            topic_name=topic_name,
            start_index=0,
            end_index=msg_count,
            parent=self.root,
        )
        msg_node = MessageNode(
            user_message      = msg_dict["user"],
            assistant_message = msg_dict["assistant"],
            system_message    = msg_dict["system"],
            message_index     = msg_dict["index"],
            parent            = first_topic,
        )
        first_topic.children.append(msg_node)
        self.root.children.append(first_topic)
        self.root.update_sub_node_count()
        self.root.end_index = msg_count
        self.current_node   = first_topic

    def _add_message(self, msg_dict):
        target_topic = self._assign_topic_for_message(
            msg_dict["user"], msg_dict["assistant"], msg_dict["system"]
        )
        msg_node = MessageNode(
            user_message      = msg_dict["user"],
            assistant_message = msg_dict["assistant"],
            system_message    = msg_dict["system"],
            message_index     = msg_dict["index"],
            parent            = target_topic,
        )
        target_topic.children.append(msg_node)
        target_topic.update_sub_node_count()
        target_topic.end_index = len(self.conversation)
        self.current_node      = target_topic

    def _assign_topic_for_message(self, user_msg, assistant_msg, system_msg=None):
        candidate_nodes = self.get_ancestors(
            self.current_node, include_self=True, exclude_root=False
        )
        if not candidate_nodes:
            return self._create_new_topic_for_message(
                user_msg, assistant_msg, self.root, "", system_msg
            )
        classification = self._llm_classify_message_exchange(
            user_msg, assistant_msg, candidate_nodes, system_msg
        )
        if classification["belongs_to_current"]:
            return candidate_nodes[-1]
        parent_index = classification.get("new_topic_parent_index", len(candidate_nodes) - 1)
        parent_node  = candidate_nodes[parent_index]
        topic_name   = classification.get("new_topic_name", "")
        return self._create_new_topic_for_message(
            user_msg, assistant_msg, parent_node, topic_name, system_msg
        )

    def _has_topic_children(self, node) -> bool:
        return any(isinstance(child, TopicNode) for child in node.children)

    def _create_new_topic_for_message(
        self, user_msg, assistant_msg, parent, topic_name="", system_msg=None
    ):
        if not topic_name or not topic_name.strip():
            topic_name = self._llm_generate_topic_from_message(
                user_msg, assistant_msg, parent=parent, system_msg=system_msg
            )
        msg_count = 3 if system_msg else 2
        new_topic = TopicNode(
            topic_name  = topic_name,
            start_index = len(self.conversation) - msg_count,
            end_index   = len(self.conversation),
            parent      = parent,
        )
        parent.children.append(new_topic)
        parent.update_sub_node_count()
        return new_topic

    def _create_new_node(self, message, start_index, parent, topic_name=""):
        if not topic_name or not topic_name.strip():
            topic_name = self._llm_generate_topic(message, parent)
        new_topic = TopicNode(
            topic_name=topic_name,
            start_index=start_index,
            end_index=start_index,
            parent=parent,
        )
        parent.children.append(new_topic)
        parent.update_sub_node_count()
        return new_topic

    # ── LLM helpers ───────────────────────────────────────────────────────────

    def _llm_generate_topic(self, message, parent=None) -> str:
        content = message.get("content", "")
        context = f"\nParent topic: {parent.topic_name}" if (parent and parent.topic_name != "ROOT") else ""
        prompt  = (
            f"Given the following message, generate a concise topic name "
            f"(2-5 words) that captures its main subject.{context}\n\n"
            f"Message: {content}\n\nRespond with ONLY the topic name, nothing else."
        )
        try:
            return ChatGPT_API(self.model, prompt, api_key=self.api_key, temperature=0.3, max_tokens=50).strip()
        except Exception as e:
            print(f"LLM error in topic generation: {e}")
            return f"Topic at index {len(self.conversation)}"

    def _llm_generate_topic_from_message(
        self, user_msg, assistant_msg, parent=None, system_msg=None
    ) -> str:
        user_content      = user_msg.get("content", "")
        assistant_content = assistant_msg.get("content", "")
        context           = f"\nParent topic: {parent.topic_name}" if (parent and parent.topic_name != "ROOT") else ""
        system_context    = f"\nSystem: {system_msg.get('content', '')}\n" if system_msg else ""
        prompt = (
            f"Given the following conversation exchange, generate a concise topic name "
            f"(2-5 words) that captures its main subject.{context}\n{system_context}\n"
            f"User: {user_content}\nAssistant: {assistant_content}\n\n"
            f"Respond with ONLY the topic name, nothing else."
        )
        try:
            return ChatGPT_API(self.model, prompt, api_key=self.api_key, temperature=0.3, max_tokens=50).strip()
        except Exception as e:
            print(f"LLM error in topic generation from message: {e}")
            return f"Topic at message {len(self.conversation) // 2}"

    def _llm_summarize(self, messages: List[dict], topic_name: str) -> str:
        content = "\n\n".join(
            [f"{m.get('role','unknown')}: {m.get('content','')}" for m in messages[:5]]
        )
        prompt  = (
            f"Summarize the following conversation segment about \"{topic_name}\" "
            f"in 1-2 sentences.\n\n{content}\n\nSummary:"
        )
        try:
            return ChatGPT_API(self.model, prompt, api_key=self.api_key, temperature=0.3, max_tokens=100).strip()
        except Exception as e:
            print(f"LLM error in summarization: {e}")
            return f"Discussion about {topic_name}"

    def _llm_classify_message_exchange(
        self, user_msg, assistant_msg, candidate_nodes, system_msg=None
    ) -> dict:
        user_content      = user_msg.get("content", "")
        assistant_content = assistant_msg.get("content", "")
        current_node      = candidate_nodes[-1]
        recent_messages   = self.conversation[max(0, current_node.end_index - 6) : current_node.end_index]
        recent_content    = "\n".join(
            [f"- {m.get('role','unknown')}: {m.get('content','')[:150]}" for m in recent_messages[-6:]]
        )
        ancestor_list = "\n".join(
            [f"{i}. {node.topic_name} - {node.summary[:100]}" for i, node in enumerate(candidate_nodes[:-1])]
        )
        system_context = f"System: {system_msg.get('content', '')[:200]}\n" if system_msg else ""
        prompt = (
            f"Decide if this new conversation exchange belongs to the current topic "
            f"or starts a completely new one.\n\n"
            f"**Current Topic:** {current_node.topic_name}\n"
            f"**Topic Summary:** {current_node.summary}\n\n"
            f"**Recent exchanges in current topic:**\n{recent_content}\n\n"
            f"**New exchange:**\n{system_context}"
            f"User: {user_content[:400]}\nAssistant: {assistant_content[:400]}\n\n"
            f"**Available parent nodes (choose one as parent if creating a new topic):**\n{ancestor_list}\n\n"
            f"RULES:\n"
            f"1. If the new exchange is a natural continuation of the current topic, set belongs_to_current=true.\n"
            f"2. If the new exchange introduces a completely different subject, set belongs_to_current=false.\n"
            f"3. CRITICAL: When choosing new_topic_parent_index — if the new topic is completely unrelated "
            f"to all listed ancestors, you MUST choose index 0 (the root/top-level). Only choose a deeper "
            f"ancestor if the new topic is a genuine sub-topic of that ancestor.\n"
            f"4. Do NOT nest an unrelated topic under the current topic just because it is the most recent.\n\n"
            f"Respond ONLY with valid JSON:\n"
            f"{{\"reasoning\": \"brief explanation\", \"belongs_to_current\": true|false, "
            f"\"new_topic_name\": \"name (or N/A)\", \"new_topic_parent_index\": <index or N/A>}}\n\n"
            f"Output ONLY the JSON, no other text."
        )
        try:
            response = ChatGPT_API(self.model, prompt, api_key=self.api_key, temperature=0.1, max_tokens=200)
            result   = extract_json(response)
            if "new_topic_parent_index" in result:
                raw    = result["new_topic_parent_index"]
                is_na  = raw is None or (isinstance(raw, str) and raw.strip().upper() == "N/A")
                if not is_na:
                    result["new_topic_parent_index"] = min(
                        max(0, _safe_int(raw, len(candidate_nodes) - 1)),
                        len(candidate_nodes) - 1,
                    )
            if "belongs_to_current" not in result:
                result["belongs_to_current"] = True
            return result
        except Exception as e:
            print(f"LLM error in message classification: {e}")
            return {
                "reasoning": "Error in classification, defaulting to current topic",
                "belongs_to_current": True,
                "new_topic_name": "",
                "new_topic_parent_index": len(candidate_nodes) - 1,
            }

    def _llm_split_subtopics(self, messages: List[dict], node) -> list:
        message_summary = [
            f"[{i}] {m.get('role','unknown')}: {m.get('content','')[:200]}"
            for i, m in enumerate(messages)
        ]
        messages_text = "\n".join(message_summary)
        prompt = (
            f'Analyze the following conversation segment about "{node.topic_name}" '
            f"and identify distinct subtopics.\nGroup consecutive messages into 2 coherent subtopics.\n\n"
            f"Messages (indices 0–{len(messages) - 1}):\n{messages_text}\n\n"
            f"Respond in JSON format with an array of subtopics:\n"
            f'{{"subtopics": [{{"topic_name": "...", "summary": "...", '
            f'"start_offset": <int>, "end_offset": <int>}}, ...]}}\n\n'
            f"Ensure: subtopics cover all messages; no gaps/overlaps; each subtopic ≥ 2 messages.\n"
            f"Output ONLY the JSON, no other text."
        )
        try:
            response = ChatGPT_API(self.model, prompt, api_key=self.api_key, temperature=0.3, max_tokens=500)
            result   = extract_json(response)
            if isinstance(result, list):
                return result
            if "subtopics" in result:
                return result["subtopics"]
        except Exception as e:
            print(f"LLM error in splitting: {e}")
        mid = len(messages) // 2
        return [
            {"topic_name": f"{node.topic_name} – Part 1", "summary": "First part", "start_offset": 0,   "end_offset": mid},
            {"topic_name": f"{node.topic_name} – Part 2", "summary": "Second part", "start_offset": mid, "end_offset": len(messages)},
        ]

    def _llm_find_split_point(self, topic_children, parent_node) -> int:
        children_text = "\n".join(
            [f"[{i}] {c.topic_name} – {c.summary[:100]}" for i, c in enumerate(topic_children)]
        )
        prompt = (
            f'You are analyzing topic "{parent_node.topic_name}" with {len(topic_children)} subtopics.\n'
            f"Find the BEST SPLIT POINT (natural topic shift).\n\nSubtopics:\n{children_text}\n\n"
            f'Respond with ONLY: {{"reasoning": "...", "split_index": <int 1–{len(topic_children)-1}>}}'
        )
        try:
            response    = ChatGPT_API(self.model, prompt, api_key=self.api_key, temperature=0.2, max_tokens=200)
            result      = extract_json(response)
            split_index = _safe_int(result.get("split_index"), len(topic_children) // 2)
            return max(1, min(split_index, len(topic_children) - 1))
        except Exception:
            return len(topic_children) // 2

    def _llm_generate_topic_from_children(self, topic_children, parent_node) -> str:
        names  = [c.topic_name for c in topic_children[:5]]
        text   = ", ".join(names) + (f", and {len(topic_children) - 5} more" if len(topic_children) > 5 else "")
        prompt = (
            f"Generate a concise topic name (2-6 words) that captures the common "
            f"theme of these subtopics:\n\nParent topic: {parent_node.topic_name}\n"
            f"Subtopics: {text}\n\nRespond with ONLY the topic name."
        )
        try:
            name = ChatGPT_API(self.model, prompt, api_key=self.api_key, temperature=0.3, max_tokens=50).strip()
            return name.strip("\"'")
        except Exception:
            return f"{parent_node.topic_name} – Part"

    def _llm_group_topics_into_subtopics(self, topic_children, parent_node) -> list:
        num_topics = len(topic_children)
        num_groups = max(2, (num_topics + self.max_children - 1) // self.max_children)
        topics_text = "\n".join(
            [f"[{i}] {c.topic_name} – {c.summary[:100]}" for i, c in enumerate(topic_children)]
        )
        prompt = (
            f"Analyze these {num_topics} topics and group them into {num_groups} logical categories "
            f"by theme, respecting chronological flow.\n\nTopics:\n{topics_text}\n\n"
            f'Respond with ONLY: {{"groups": [{{"topic_name": "...", "summary": "...", '
            f'"topic_indices": [...]}}]}}'
        )
        try:
            response = ChatGPT_API(self.model, prompt, api_key=self.api_key, temperature=0.3, max_tokens=800)
            result   = extract_json(response)
            if isinstance(result, dict) and "groups" in result:
                groups = result["groups"]
            elif isinstance(result, list):
                groups = result
            else:
                return self._create_fallback_groups(topic_children, num_groups)
            all_indices = set()
            for g in groups:
                all_indices.update(g.get("topic_indices", []))
            if len(all_indices) != num_topics:
                return self._create_fallback_groups(topic_children, num_groups)
            return groups
        except Exception:
            return self._create_fallback_groups(topic_children, num_groups)

    def _create_fallback_groups(self, topic_children, num_groups) -> list:
        groups, tpg = [], len(topic_children) // num_groups
        for i in range(num_groups):
            s = i * tpg
            e = s + tpg if i < num_groups - 1 else len(topic_children)
            idx = list(range(s, e))
            gt  = [topic_children[j] for j in idx]
            groups.append({
                "topic_name": f"Topic Group {i + 1}",
                "summary":    f"Group from {gt[0].topic_name} to {gt[-1].topic_name}",
                "topic_indices": idx,
            })
        return groups

    # ── Node expansion / splitting ────────────────────────────────────────────

    def _expand_node(self, node):
        msg_nodes = [c for c in node.children if isinstance(c, MessageNode)]
        if not msg_nodes:
            return
        messages  = []
        for mn in msg_nodes:
            messages.append(mn.user_message)
            messages.append(mn.assistant_message)
        subtopics = self._llm_split_subtopics(messages, node)
        node.children.clear()
        last_subtopic_node = None
        for subtopic in subtopics:
            s_off = subtopic["start_offset"]
            e_off = subtopic["end_offset"]
            s_idx = s_off // 2
            e_idx = (e_off + 1) // 2
            sn    = TopicNode(
                topic_name  = subtopic["topic_name"],
                start_index = node.start_index + s_off,
                end_index   = node.start_index + e_off,
                parent      = node,
            )
            for i in range(s_idx, min(e_idx, len(msg_nodes))):
                mn        = msg_nodes[i]
                mn.parent = sn
                sn.children.append(mn)
            node.children.append(sn)
            last_subtopic_node = sn
        node.update_sub_node_count()
        if self.current_node is node and last_subtopic_node:
            self.current_node = last_subtopic_node

    def _split_node(self, node):
        if node is self.root:
            self._expand_root_into_subtopics(node)
            return
        parent        = node.parent
        if parent is None:
            return
        topic_children = [c for c in node.children if isinstance(c, TopicNode)]
        if len(topic_children) < 2:
            return
        split_point   = self._llm_find_split_point(topic_children, node)
        first_half    = topic_children[:split_point]
        second_half   = topic_children[split_point:]
        first_start   = first_half[0].start_index
        first_end     = first_half[-1].end_index
        second_start  = second_half[0].start_index
        second_end    = second_half[-1].end_index
        first_name    = self._llm_generate_topic_from_children(first_half,  node)
        second_name   = self._llm_generate_topic_from_children(second_half, node)
        first_node    = TopicNode(topic_name=first_name,  start_index=first_start,  end_index=first_end,  parent=parent)
        second_node   = TopicNode(topic_name=second_name, start_index=second_start, end_index=second_end, parent=parent)
        for t in first_half:
            t.parent = first_node;  first_node.children.append(t)
        for t in second_half:
            t.parent = second_node; second_node.children.append(t)
        parent.children.remove(node)
        parent.children.extend([first_node, second_node])
        first_node.update_sub_node_count()
        second_node.update_sub_node_count()
        parent.update_sub_node_count()
        if self.current_node is node:
            self.current_node = second_node

    def _expand_root_into_subtopics(self, root_node):
        topic_children = [c for c in root_node.children if isinstance(c, TopicNode)]
        if len(topic_children) < 2:
            return
        groups = self._llm_group_topics_into_subtopics(topic_children, root_node)
        root_node.children.clear()
        last_subtopic_node = None
        for group in groups:
            indices = group["topic_indices"]
            group_topics = [topic_children[i] for i in indices if i < len(topic_children)]
            if not group_topics:
                continue
            g_start = min(t.start_index for t in group_topics)
            g_end   = max(t.end_index   for t in group_topics)
            sn = TopicNode(
                topic_name  = group["topic_name"],
                summary     = group.get("summary", ""),
                start_index = g_start,
                end_index   = g_end,
                parent      = root_node,
            )
            for t in group_topics:
                t.parent = sn
                sn.children.append(t)
            sn.update_sub_node_count()
            root_node.children.append(sn)
            last_subtopic_node = sn
        root_node.update_sub_node_count()
        if last_subtopic_node:
            def _in_subtree(n, target):
                if n is target: return True
                return any(_in_subtree(c, target) for c in n.children)
            for sn in root_node.children:
                if isinstance(sn, TopicNode) and _in_subtree(sn, self.current_node):
                    break
            else:
                self.current_node = last_subtopic_node

    # ── Public utilities ──────────────────────────────────────────────────────

    def generate_summaries(self):
        """
        Manually trigger lazy summarisation of all frozen branches.
        Called automatically during ``save()``.
        """
        self._generate_summaries_for_frozen_nodes()

    def print_tree(self, node=None, indent: int = 0, show_messages: bool = False):
        """
        Pretty-print the conversation tree to stdout.

        Parameters
        ----------
        node          : Start node (defaults to root).
        indent        : Current indentation level (used internally).
        show_messages : If True, also render MessageNode contents.
        """
        if node is None:
            node = self.root
        prefix = "  " * indent
        if isinstance(node, TopicNode):
            msg_count = node.get_message_count()
            if node is self.root:
                print(f"{prefix}ROOT (sub-nodes: {node.sub_node_count})")
            else:
                print(f"{prefix}├─ {node.topic_name} [{node.start_index}:{node.end_index}] ({msg_count} msgs)")
                if indent < 3:
                    print(f"{prefix}   {node.summary[:100]}")
        elif isinstance(node, MessageNode) and show_messages:
            if node.system_message:
                print(f"{prefix}  └─ Message[{node.message_index}] (3 msgs):")
                print(f"{prefix}     System: {node.system_message.get('content','')[:50]}…")
            else:
                print(f"{prefix}  └─ Message[{node.message_index}] (2 msgs):")
            print(f"{prefix}     User:      {node.user_message.get('content','')[:50]}…")
            print(f"{prefix}     Assistant: {node.assistant_message.get('content','')[:50]}…")
        for child in node.children:
            if isinstance(child, TopicNode) or (isinstance(child, MessageNode) and show_messages):
                self.print_tree(child, indent + 1, show_messages)

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "max_children":   self.max_children,
            "total_messages": len(self.conversation),
            "tree":           self.root.to_dict(),
        }

    def save(self, filepath: str, save_conversation: bool = False):
        """
        Persist the tree to a JSON file.

        Parameters
        ----------
        filepath          : Output path (e.g. "sessions/chat_001.json").
        save_conversation : If True, embed the raw ``conversation`` list in
                            the file so it can be fully restored.

        Example
        -------
            tree.save("my_session.json", save_conversation=True)
        """
        self._generate_summaries_for_frozen_nodes()
        data = self.to_dict()
        if save_conversation:
            data["conversation"] = self.conversation
        if self._archived_nodes:
            data["archived"] = [n.to_dict() for n in self._archived_nodes]
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, filepath: str, api_key: str = None, model: str = None, embed_fn: Optional[Callable[[str], List[float]]] = None) -> "CTree":
        """
        Reconstruct a ``CTree`` from a previously saved JSON file.

        Parameters
        ----------
        filepath : Path to the JSON file produced by ``save()``.
        api_key  : LLM API key.
        model    : Override model ID (defaults to "gpt-4o-mini").

        Returns
        -------
        CTree  — fully hydrated tree with ``conversation``, all nodes, and
                 any archived messages restored.

        Example
        -------
            tree = CTree.load("my_session.json", api_key="sk-...")
        """
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        tree = cls(
            max_children = data.get("max_children", 5),
            api_key      = api_key,
            model        = model or "gpt-4o-mini",
            embed_fn     = embed_fn,
        )
        tree.conversation = data.get("conversation", [])
        tree.root         = tree._reconstruct_node(data["tree"], parent=None)
        tree.current_node = tree._find_current_node(tree.root)
        for arch_data in data.get("archived", []):
            try:
                tree._archived_nodes.append(tree._reconstruct_node(arch_data, parent=None))
            except Exception:
                pass
        return tree

    def _reconstruct_node(self, node_data: dict, parent):
        node_type = node_data.get("type", "topic")
        if node_type == "message":
            msg_start_idx = node_data.get("message_index", node_data.get("pair_index", 0) * 2)
            user_msg = assistant_msg = {}
            system_msg = None
            for i in range(max(0, msg_start_idx - 1), min(len(self.conversation), msg_start_idx + 4)):
                if i < len(self.conversation) and self.conversation[i].get("role") == "user":
                    if i > 0 and self.conversation[i - 1].get("role") == "system":
                        system_msg = self.conversation[i - 1]
                    user_msg = self.conversation[i]
                    if i + 1 < len(self.conversation) and self.conversation[i + 1].get("role") == "assistant":
                        assistant_msg = self.conversation[i + 1]
                        break
            node = MessageNode(
                user_message      = user_msg,
                assistant_message = assistant_msg,
                system_message    = system_msg,
                message_index     = msg_start_idx,
                parent            = parent,
            )
        else:
            node = TopicNode(
                topic_name  = node_data.get("topic_name", ""),
                summary     = node_data.get("summary", ""),
                start_index = node_data.get("start_index", 0),
                end_index   = node_data.get("end_index", 0),
                parent      = parent,
            )
            if "node_id" in node_data:
                node.node_id = node_data["node_id"]
            node.created_at = node_data.get("created_at", node.created_at)
            for child_data in node_data.get("children", []):
                node.children.append(self._reconstruct_node(child_data, parent=node))
        node.sub_node_count = node_data.get("sub_node_count", 0)
        return node

    def _find_current_node(self, node):
        if isinstance(node, MessageNode):
            return node.parent if isinstance(node.parent, TopicNode) else self.root
        if not isinstance(node, TopicNode):
            return self.root
        if node.children:
            last = node.children[-1]
            if isinstance(last, MessageNode):
                return node
            if isinstance(last, TopicNode):
                return self._find_current_node(last)
        return node

    # ── Self-healing reorganizer ──────────────────────────────────────────────

    def _build_flat_topic_list(self) -> list:
        """Post-order traversal: returns every non-root TopicNode."""
        result = []
        def _post(node):
            for c in node.children:
                _post(c)
            if isinstance(node, TopicNode) and node is not self.root:
                result.append(node)
        _post(self.root)
        return result

    def _llm_check_relatedness(self, node_a, node_b) -> dict:
        """Ask the LLM whether two frozen branches are related enough to merge."""
        hint = "a_into_b means B is older" if node_a.created_at <= node_b.created_at else "b_into_a means A is older"
        prompt = (
            f'Topic A: "{node_a.topic_name}" — {(node_a.summary or "(no summary)")[:200]}\n'
            f'Topic B: "{node_b.topic_name}" — {(node_b.summary or "(no summary)")[:200]}\n\n'
            f"Are these two topics related enough to be grouped under the same parent?\n"
            f"The cosine similarity score already pre-filtered these — lean toward yes unless they are "
            f"clearly different domains (e.g. cooking vs programming).\n"
            f"If yes, merge_direction: \"a_into_b\" = A becomes child of B, \"b_into_a\" = B becomes child of A.\n"
            f"({hint})\n\n"
            f'Reply ONLY with JSON. Keep "reason" under 10 words:\n'
            f'{{"related": true|false, "reason": "short reason", "merge_direction": "a_into_b"|"b_into_a"|"N/A"}}'
        )
        try:
            response = ChatGPT_API(self.model, prompt, api_key=self.api_key, temperature=0.1, max_tokens=300)
            result   = extract_json(response)
            result.setdefault("related", False)
            result.setdefault("merge_direction", "N/A")
            return result
        except Exception:
            return {"related": False, "reason": "LLM error", "merge_direction": "N/A"}

    def _merge_node_into(self, source: "TopicNode", target: "TopicNode"):
        """
        Move *source* to become a child of *target*.
        No MessageNodes are deleted.  VDB entry is cleared for re-indexing.
        """
        old_parent = source.parent
        if old_parent is None or old_parent is target:
            return
        try:
            old_parent.children.remove(source)
        except ValueError:
            return
        old_parent.update_sub_node_count()
        source.parent = target
        target.children.append(source)
        target.update_sub_node_count()
        if self.vdb is not None:
            try:
                self.vdb.delete_topic_summary(source.node_id)
            except Exception:
                pass

    def _is_ancestor_of(self, potential_ancestor, node) -> bool:
        current = node.parent
        while current is not None:
            if current is potential_ancestor:
                return True
            current = current.parent
        return False

    def reorganize(
        self,
        embed_fn=None,
        similarity_threshold: float = 0.55,
        prune_trivial_leaves: bool  = False,
    ) -> dict:
        """
        Run one conservative self-healing reorganization pass over the tree.

        This is the core of TRACE's "Axiomatic Reorganization" feature.
        It evaluates all *frozen* (inactive) branches for semantic similarity
        and, when four strict axioms are satisfied, merges related branches
        under a common parent — mimicking how the human brain consolidates
        memories during sleep.

        The four axioms
        ---------------
        1. **Chronological Guard**: The older node absorbs the newer one —
           never the reverse.
        2. **Frozen State**: Only nodes outside the currently active ancestry
           path may be merged.  The live conversation thread is never touched.
        3. **Sim Threshold**: Cosine similarity between branch embeddings must
           exceed *similarity_threshold* (default 0.55).
        4. **LLM Veto**: The LLM independently confirms the merge makes
           semantic sense.  If it disagrees, the merge is aborted.

        Parameters
        ----------
        embed_fn             : Optional embedding function to override
                               ``tree.embed_fn``.  Must be callable:
                               ``(text: str) -> List[float]``.
        similarity_threshold : Minimum cosine similarity for a pair to be
                               considered as merge candidates. (default 0.55)
        prune_trivial_leaves : If True, MessageNodes with < 20 words in both
                               sides are soft-archived (moved to
                               ``tree._archived_nodes``).  Default False.

        Returns
        -------
        dict with keys:
            merged        (int) — number of branches merged.
            pruned        (int) — number of trivial messages archived.
            skipped       (int) — pairs skipped due to axiom violations.
            duration_secs (float) — wall-clock time for the pass.

        Example
        -------
            stats = tree.reorganize(
                embed_fn=my_embed,
                similarity_threshold=0.60,
                prune_trivial_leaves=True,
            )
            print(f"Merged {stats['merged']} branches in {stats['duration_secs']:.1f}s")
        """
        import time as _time
        from .vector_db import cosine_similarity as _cos

        t0      = _time.time()
        merged  = pruned = skipped = 0

        # Phase 1 — collect frozen candidates
        all_topics   = self._build_flat_topic_list()
        live_ancestors = set(self.get_ancestors(self.current_node, include_self=True))
        candidates   = [n for n in all_topics if n not in live_ancestors and self._is_frozen_node(n)]

        if len(candidates) < 2:
            return {"merged": 0, "pruned": 0, "skipped": 0, "duration_secs": _time.time() - t0}

        # Phase 2 — embed candidates
        self._generate_summaries_for_frozen_nodes()
        _embed     = embed_fn or self.embed_fn
        embeddings = {}
        if _embed:
            for node in candidates:
                text = (node.summary or node.topic_name).strip()
                if not text:
                    for child in node.children:
                        if isinstance(child, MessageNode):
                            u = (child.user_message.get("content") or "").strip()
                            a = (child.assistant_message.get("content") or "").strip()
                            raw = f"{u} {a}".strip()
                            if raw:
                                text = raw[:300]
                                if not node.topic_name:
                                    node.topic_name = self._llm_generate_topic_from_message(
                                        child.user_message, child.assistant_message
                                    )
                                break
                if not text:
                    continue
                try:
                    embeddings[node.node_id] = _embed(text)
                except Exception as e:
                    print(f"  ⚠ Embed error for [{node.topic_name}]: {e}")

        # Phase 3 — find high-similarity pairs and apply axioms
        candidate_pairs = []
        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                na, nb = candidates[i], candidates[j]
                if na.node_id not in embeddings or nb.node_id not in embeddings:
                    skipped += 1
                    continue
                sim = _cos(embeddings[na.node_id], embeddings[nb.node_id])
                if sim < similarity_threshold:
                    continue
                older, newer = (na, nb) if na.created_at <= nb.created_at else (nb, na)
                if older.created_at > newer.created_at:
                    skipped += 1
                    continue
                if self._is_ancestor_of(older, newer) or self._is_ancestor_of(newer, older):
                    skipped += 1
                    continue
                candidate_pairs.append((sim, older, newer))

        candidate_pairs.sort(key=lambda x: x[0], reverse=True)

        already_moved = set()
        for sim, older, newer in candidate_pairs:
            if older.node_id in already_moved or newer.node_id in already_moved:
                skipped += 1
                continue
            if not self._is_frozen_node(older) or not self._is_frozen_node(newer):
                skipped += 1
                continue
            decision = self._llm_check_relatedness(older, newer)
            if not decision.get("related", False):
                skipped += 1
                continue
            self._merge_node_into(source=newer, target=older)
            already_moved.add(newer.node_id)
            merged += 1

        # Phase 4 — optional leaf pruning
        if prune_trivial_leaves:
            for node in self._build_flat_topic_list():
                if node not in live_ancestors and self._is_frozen_node(node):
                    msg_children   = [c for c in node.children if isinstance(c, MessageNode)]
                    topic_children = [c for c in node.children if isinstance(c, TopicNode)]
                    if topic_children:
                        continue
                    parent = node.parent
                    if parent is None or not (parent.summary and parent.summary.strip()):
                        continue
                    for mc in msg_children:
                        u_words = len((mc.user_message.get("content", "") or "").split())
                        a_words = len((mc.assistant_message.get("content", "") or "").split())
                        if u_words < 20 and a_words < 20:
                            self._archived_nodes.append(mc)
                            node.children.remove(mc)
                            pruned += 1

        return {
            "merged":        merged,
            "pruned":        pruned,
            "skipped":       skipped,
            "duration_secs": _time.time() - t0,
        }
