"""Episodic memory tree — hierarchical topic clustering for conversations.

Inspired by TRACE's CTree but implemented natively with JSON persistence
and ChromaDB for topic-summary embeddings.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from src.topic_analyzer import TOPIC_KEYWORDS

logger = logging.getLogger(__name__)


def _tokenize(text: str) -> Set[str]:
    """Simple tokenizer for Jaccard similarity."""
    return set(word.strip(".,!?\"';:()[]") for word in text.lower().split())


def _jaccard(a: str, b: str) -> float:
    """Jaccard similarity between two texts."""
    if not a or not b:
        return 0.0
    ta = _tokenize(a)
    tb = _tokenize(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _extract_keywords(text: str) -> Set[str]:
    """Return matched TRACE-style topic keywords from text."""
    words = set()
    lower = text.lower()
    for topic, kws in TOPIC_KEYWORDS.items():
        for kw in kws:
            if kw in lower:
                words.add(kw)
    return words


@dataclass
class MessageNode:
    """A single turn in the conversation."""

    id: str
    role: str
    text: str
    timestamp: int
    session_id: Optional[str] = None
    topic_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "text": self.text,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "topic_id": self.topic_id,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> MessageNode:
        return cls(
            id=d["id"],
            role=d["role"],
            text=d["text"],
            timestamp=d.get("timestamp", 0),
            session_id=d.get("session_id"),
            topic_id=d.get("topic_id"),
        )


@dataclass
class TopicNode:
    """A cluster of messages under a topic branch."""

    id: str
    topic_name: str
    summary: str = ""
    parent_id: Optional[str] = None
    children_ids: List[str] = field(default_factory=list)
    message_start: int = 0
    message_end: int = 0
    embedding_id: Optional[str] = None
    created_at: int = 0
    keywords: Set[str] = field(default_factory=set)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "topic_name": self.topic_name,
            "summary": self.summary,
            "parent_id": self.parent_id,
            "children_ids": self.children_ids,
            "message_start": self.message_start,
            "message_end": self.message_end,
            "embedding_id": self.embedding_id,
            "created_at": self.created_at,
            "keywords": list(self.keywords),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> TopicNode:
        return cls(
            id=d["id"],
            topic_name=d.get("topic_name", "Untitled"),
            summary=d.get("summary", ""),
            parent_id=d.get("parent_id"),
            children_ids=list(d.get("children_ids", [])),
            message_start=d.get("message_start", 0),
            message_end=d.get("message_end", 0),
            embedding_id=d.get("embedding_id"),
            created_at=d.get("created_at", 0),
            keywords=set(d.get("keywords", [])),
        )


class EpisodicTree:
    """JSON-backed hierarchical episodic memory tree.

    Uses heuristic topic branching by default (Jaccard + keyword overlap).
    Topic summaries are generated lazily or by an external summarizer.
    """

    def __init__(
        self,
        data_dir: str,
        *,
        owner: Optional[str] = None,
        branch_threshold: float = 0.4,
    ):
        self.data_dir = data_dir
        self.owner = owner
        self.branch_threshold = branch_threshold
        self._file_path = self._path_for_owner(owner)

        self.messages: List[MessageNode] = []
        self.topics: Dict[str, TopicNode] = {}
        self._root_id: Optional[str] = None
        self._current_topic_id: Optional[str] = None

        self.load()

    # --------------------------------------------------------------------- #
    # Persistence
    # --------------------------------------------------------------------- #

    def _path_for_owner(self, owner: Optional[str]) -> str:
        fname = f"episodes_{owner or 'default'}.json"
        return os.path.join(self.data_dir, fname)

    def load(self) -> None:
        if not os.path.exists(self._file_path):
            self._create_root()
            return
        try:
            with open(self._file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.messages = [MessageNode.from_dict(m) for m in data.get("messages", [])]
            self.topics = {
                t["id"]: TopicNode.from_dict(t) for t in data.get("topics", [])
            }
            self._root_id = data.get("root_id")
            self._current_topic_id = data.get("current_topic_id")
            if not self._root_id or self._root_id not in self.topics:
                self._create_root()
        except Exception as e:
            logger.error("Failed to load episodic tree: %s", e)
            self._create_root()

    def save(self) -> None:
        data = {
            "messages": [m.to_dict() for m in self.messages],
            "topics": [t.to_dict() for t in self.topics.values()],
            "root_id": self._root_id,
            "current_topic_id": self._current_topic_id,
        }
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            with open(self._file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error("Failed to save episodic tree: %s", e)

    # --------------------------------------------------------------------- #
    # Tree helpers
    # --------------------------------------------------------------------- #

    def _create_root(self) -> None:
        root = TopicNode(
            id=str(uuid.uuid4()),
            topic_name="General",
            summary="Root topic for general conversation.",
            created_at=int(time.time()),
        )
        self.topics = {root.id: root}
        self._root_id = root.id
        self._current_topic_id = root.id
        self.messages = []

    def _new_topic(self, name: str, parent_id: Optional[str] = None) -> TopicNode:
        topic = TopicNode(
            id=str(uuid.uuid4()),
            topic_name=name,
            parent_id=parent_id or self._root_id,
            created_at=int(time.time()),
            keywords=_extract_keywords(name),
        )
        self.topics[topic.id] = topic
        parent = self.topics.get(topic.parent_id)
        if parent:
            parent.children_ids.append(topic.id)
        return topic

    def get_ancestors(self, node_id: str) -> List[TopicNode]:
        """Walk from the given node up to the root."""
        path: List[TopicNode] = []
        seen: Set[str] = set()
        current_id = node_id
        while current_id and current_id in self.topics:
            if current_id in seen:
                logger.warning("Cycle detected in topic tree at %s", current_id)
                break
            seen.add(current_id)
            node = self.topics[current_id]
            path.append(node)
            current_id = node.parent_id
        path.reverse()
        return path

    def get_topic_messages(self, topic_id: str) -> List[MessageNode]:
        """Return messages assigned to a given topic."""
        return [m for m in self.messages if m.topic_id == topic_id]

    # --------------------------------------------------------------------- #
    # Ingestion
    # --------------------------------------------------------------------- #

    def add(
        self,
        messages: List[Dict[str, Any]],
        *,
        session_id: Optional[str] = None,
        force_topic_id: Optional[str] = None,
    ) -> str:
        """Ingest a list of message dicts and return the active topic id.

        Each dict should have keys: role, text (and optionally content).
        """
        if not messages:
            return self._current_topic_id or self._root_id

        # Normalize dicts to MessageNodes
        nodes: List[MessageNode] = []
        for msg in messages:
            text = msg.get("text") or msg.get("content", "")
            if not text:
                continue
            node = MessageNode(
                id=str(uuid.uuid4()),
                role=msg.get("role", "unknown"),
                text=str(text),
                timestamp=int(time.time()),
                session_id=session_id,
            )
            nodes.append(node)
            self.messages.append(node)

        if not nodes:
            return self._current_topic_id or self._root_id

        # Determine topic
        if force_topic_id and force_topic_id in self.topics:
            topic_id = force_topic_id
        else:
            topic_id = self._classify_topic(nodes)

        # Assign nodes to topic
        start_idx = len(self.messages) - len(nodes)
        for i, node in enumerate(nodes):
            node.topic_id = topic_id

        # Update topic message range
        topic = self.topics[topic_id]
        if topic.message_start == 0 and topic.message_end == 0:
            topic.message_start = start_idx
        topic.message_end = len(self.messages) - 1

        # Update current topic
        self._current_topic_id = topic_id

        # Lazily update keywords from message text
        all_text = " ".join(n.text for n in nodes)
        topic.keywords.update(_extract_keywords(all_text))

        self.save()
        return topic_id

    def _classify_topic(self, nodes: List[MessageNode]) -> str:
        """Heuristic topic classification — fast, no LLM."""
        text = " ".join(n.text for n in nodes)
        current_id = self._current_topic_id or self._root_id
        current_topic = self.topics.get(current_id)

        if not current_topic:
            return self._root_id

        # Jaccard similarity against current topic name + summary
        topic_text = f"{current_topic.topic_name} {current_topic.summary}"
        sim = _jaccard(text, topic_text)

        # Boost by keyword overlap
        msg_kw = _extract_keywords(text)
        topic_kw = current_topic.keywords
        if msg_kw and topic_kw:
            overlap = len(msg_kw & topic_kw) / max(len(msg_kw), len(topic_kw))
            sim = max(sim, overlap)

        if sim >= self.branch_threshold:
            return current_topic.id

        # Try matching against sibling topics
        parent_id = current_topic.parent_id or self._root_id
        siblings = [
            t for tid, t in self.topics.items()
            if tid != current_topic.id and t.parent_id == parent_id
        ]
        best_match = None
        best_score = self.branch_threshold
        for sib in siblings:
            sib_text = f"{sib.topic_name} {sib.summary}"
            sib_sim = _jaccard(text, sib_text)
            sib_kw = sib.keywords
            if msg_kw and sib_kw:
                overlap = len(msg_kw & sib_kw) / max(len(msg_kw), len(sib_kw))
                sib_sim = max(sib_sim, overlap)
            if sib_sim > best_score:
                best_score = sib_sim
                best_match = sib.id

        if best_match:
            return best_match

        # Create new topic branch
        new_topic = self._new_topic(self._generate_topic_name(text), parent_id=parent_id)
        return new_topic.id

    def _generate_topic_name(self, text: str) -> str:
        """Generate a short topic name from text using keyword matches."""
        matched = _extract_keywords(text)
        if matched:
            # Return the first matched keyword as a simple topic name
            return sorted(matched)[0].capitalize()
        # Fallback: first few words
        words = text.split()[:3]
        return " ".join(words).capitalize() or "New Topic"

    # --------------------------------------------------------------------- #
    # Summary helpers
    # --------------------------------------------------------------------- #

    def get_topic_summary(self, topic_id: str) -> str:
        """Return existing summary or a joined snippet of messages."""
        topic = self.topics.get(topic_id)
        if not topic:
            return ""
        if topic.summary:
            return topic.summary
        msgs = self.get_topic_messages(topic_id)
        snippet = " | ".join(m.text[:120] for m in msgs[-5:])
        return snippet

    def set_topic_summary(self, topic_id: str, summary: str) -> None:
        topic = self.topics.get(topic_id)
        if topic:
            topic.summary = summary
            self.save()

    # --------------------------------------------------------------------- #
    # Query helpers
    # --------------------------------------------------------------------- #

    def get_all_topic_texts(self) -> List[Tuple[str, str]]:
        """Return list of (topic_id, searchable_text) for embedding."""
        results = []
        for tid, topic in self.topics.items():
            text = f"{topic.topic_name}\n{topic.summary}".strip()
            if not text:
                msgs = self.get_topic_messages(tid)
                text = " ".join(m.text[:200] for m in msgs[-3:])
            results.append((tid, text))
        return results

    def get_topic_path_text(self, topic_id: str) -> str:
        """Return a concatenated text of the topic and all ancestors."""
        ancestors = self.get_ancestors(topic_id)
        parts = []
        for node in ancestors:
            parts.append(f"[{node.topic_name}] {node.summary}".strip())
        return "\n".join(parts)
