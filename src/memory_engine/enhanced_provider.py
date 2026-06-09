"""EnhancedMemoryProvider — unified provider for episodic, profile, and fact tiers.

Replaces NativeMemoryProvider.  Delegates to:
  - EpisodicTree + PromptSynthesizer for agent-mode episodic retrieval
  - ProfileManager for structured user facts
  - MemoryManager (kept as-is) for legacy flat facts
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.memory_provider import MemoryProvider, MemoryRecord, MemorySearchHit
from src.memory_engine.episodic_tree import EpisodicTree
from src.memory_engine.profile_manager import ProfileManager, ProfileEntry
from src.memory_engine.prompt_synthesizer import PromptSynthesizer
from src.memory_engine.topic_classifier import TopicClassifier

logger = logging.getLogger(__name__)


class EnhancedMemoryProvider(MemoryProvider):
    """Native hierarchical memory provider."""

    provider_id = "native"
    display_name = "Odysseus enhanced memory"

    def __init__(
        self,
        memory_manager,
        memory_vector=None,
        *,
        data_dir: str = "data",
        owner: Optional[str] = None,
        topic_classifier: Optional[TopicClassifier] = None,
    ):
        self.memory_manager = memory_manager
        self.memory_vector = memory_vector
        self.data_dir = data_dir
        self.owner = owner
        self.topic_classifier = topic_classifier

        self.episodic_tree = EpisodicTree(data_dir, owner=owner)
        self.profile_manager = ProfileManager(data_dir, owner=owner)
        self.prompt_synthesizer = PromptSynthesizer(self.episodic_tree)

    # --------------------------------------------------------------------- #
    # Helpers
    # --------------------------------------------------------------------- #

    def _vector_available(self) -> bool:
        return bool(self.memory_vector and getattr(self.memory_vector, "healthy", True))

    def _to_record(self, entry: Dict[str, Any]) -> MemoryRecord:
        metadata = {k: v for k, v in entry.items() if k not in {
            "id", "text", "timestamp", "source", "category", "uses", "owner", "session_id", "metadata"
        }}
        stored = entry.get("metadata")
        if isinstance(stored, dict):
            metadata.update(stored)
        return MemoryRecord(
            id=entry.get("id", ""),
            text=entry.get("text", ""),
            timestamp=entry.get("timestamp", 0),
            category=entry.get("category", "fact"),
            source=entry.get("source", "unknown"),
            owner=entry.get("owner"),
            session_id=entry.get("session_id"),
            metadata=metadata,
        )

    def _profile_to_record(self, entry: ProfileEntry) -> MemoryRecord:
        return MemoryRecord(
            id=entry.id,
            text=f"{entry.key}: {entry.value}",
            timestamp=entry.timestamp,
            category="profile",
            source=entry.source,
            owner=entry.owner,
            metadata={"key": entry.key, "confidence": entry.confidence, **entry.metadata},
        )

    def _topic_to_record(self, topic_id: str) -> MemoryRecord:
        topic = self.episodic_tree.topics.get(topic_id)
        text = self.episodic_tree.get_topic_path_text(topic_id)
        return MemoryRecord(
            id=topic_id,
            text=text,
            timestamp=topic.created_at if topic else 0,
            category="episodic",
            source="conversation",
            owner=self.owner,
            metadata={"topic_name": topic.topic_name if topic else ""},
        )

    async def ingest_episodic(
        self,
        messages: List[Dict[str, Any]],
        *,
        session_id: Optional[str] = None,
    ) -> str:
        """Feed a conversation exchange into the episodic tree.

        Returns the active topic id.
        """
        return self.episodic_tree.add(messages, session_id=session_id)

    # --------------------------------------------------------------------- #
    # Tiered storage
    # --------------------------------------------------------------------- #

    async def remember(
        self,
        text: str,
        *,
        owner: Optional[str] = None,
        session_id: Optional[str] = None,
        category: str = "fact",
        source: str = "user",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MemoryRecord:
        if category == "profile":
            key = metadata.get("key", "fact") if metadata else "fact"
            entry = self.profile_manager.upsert(
                key=key,
                value=text,
                source=source,
                metadata=metadata,
            )
            return self._profile_to_record(entry)

        if category == "episodic":
            # Ingest as a message exchange into the episodic tree
            topic_id = self.episodic_tree.add(
                [{"role": "user" if source == "user" else "assistant", "text": text}],
                session_id=session_id,
            )
            return self._topic_to_record(topic_id)

        # Default: flat fact store
        entry = self.memory_manager.add_entry(
            text,
            source=source,
            category=category,
            owner=owner or self.owner,
        )
        if session_id:
            entry["session_id"] = session_id
        if metadata:
            entry["metadata"] = dict(metadata)

        memories = self.memory_manager.load_all()
        memories.append(entry)
        self.memory_manager.save(memories)

        if self._vector_available():
            self.memory_vector.add(entry["id"], entry["text"])

        return self._to_record(entry)

    async def recall(
        self,
        query: str,
        *,
        owner: Optional[str] = None,
        top_k: int = 5,
    ) -> List[MemorySearchHit]:
        hits: List[MemorySearchHit] = []

        # 1. Profile tier (highest priority for identity/preferences)
        profile_results = self.profile_manager.search(query)
        for entry in profile_results[:top_k]:
            hits.append(
                MemorySearchHit(
                    memory=self._profile_to_record(entry),
                    provider_id=self.provider_id,
                    score=0.95,
                )
            )

        # 2. Fact tier
        fact_hits = await self._recall_facts(query, owner=owner, top_k=top_k)
        hits.extend(fact_hits)

        # 3. Episodic tier (multi-path tree retrieval)
        if len(hits) < top_k:
            try:
                episodic_context = await self.prompt_synthesizer.retrieve(query, top_k=top_k)
                if episodic_context:
                    # Return as a single synthetic episodic memory record
                    hits.append(
                        MemorySearchHit(
                            memory=MemoryRecord(
                                id="episodic_context",
                                text=episodic_context,
                                category="episodic",
                                source="conversation",
                                owner=self.owner,
                            ),
                            provider_id=self.provider_id,
                            score=0.85,
                        )
                    )
            except Exception as e:
                logger.warning("Episodic recall failed: %s", e)

        # Deduplicate by text content
        seen: set = set()
        unique = []
        for h in hits:
            key = h.memory.id or h.memory.text[:100]
            if key not in seen:
                seen.add(key)
                unique.append(h)

        return unique[:top_k]

    async def _recall_facts(
        self,
        query: str,
        *,
        owner: Optional[str] = None,
        top_k: int = 5,
    ) -> List[MemorySearchHit]:
        memories = self.memory_manager.load(owner=owner or self.owner)
        by_id = {m.get("id"): m for m in memories}

        if self._vector_available():
            try:
                results = []
                for result in self.memory_vector.search(query, k=top_k):
                    memory_id = result.get("memory_id")
                    entry = by_id.get(memory_id) if memory_id else None
                    if not entry:
                        continue
                    if owner is not None and entry.get("owner") != owner:
                        continue
                    results.append(
                        MemorySearchHit(
                            memory=self._to_record(entry),
                            provider_id=self.provider_id,
                            score=result.get("score"),
                        )
                    )
                if results:
                    return results
            except Exception as e:
                logger.warning("Fact vector search failed: %s", e)

        # Keyword fallback
        fallback = self.memory_manager.get_relevant_memories(
            query,
            memories,
            max_items=top_k,
        )
        return [
            MemorySearchHit(
                memory=self._to_record(entry),
                provider_id=self.provider_id,
                score=None,
            )
            for entry in fallback
        ]

    async def list_memories(
        self,
        *,
        owner: Optional[str] = None,
        limit: int = 100,
    ) -> List[MemoryRecord]:
        records: List[MemoryRecord] = []

        # Facts
        for entry in self.memory_manager.load(owner=owner or self.owner)[:limit]:
            records.append(self._to_record(entry))

        # Profiles
        for entry in self.profile_manager.list_all()[:limit]:
            records.append(self._profile_to_record(entry))

        # Recent episode topics (last N)
        sorted_topics = sorted(
            self.episodic_tree.topics.values(),
            key=lambda t: t.created_at,
            reverse=True,
        )
        for topic in sorted_topics[:limit]:
            records.append(self._topic_to_record(topic.id))

        return records[:limit]

    async def delete(self, memory_id: str, *, owner: Optional[str] = None) -> bool:
        # Try fact store first
        memories = self.memory_manager.load_all()
        remaining = []
        deleted = False

        for entry in memories:
            if entry.get("id") == memory_id:
                if owner is not None and entry.get("owner") != owner:
                    remaining.append(entry)
                    continue
                deleted = True
                continue
            remaining.append(entry)

        if deleted:
            self.memory_manager.save(remaining)
            if self._vector_available():
                self.memory_vector.remove(memory_id)
            return True

        # Try profile store (key-based deletion not typical, but handle by id)
        for key, entry in list(self.profile_manager._entries.items()):
            if entry.id == memory_id:
                if owner is not None and entry.owner != owner:
                    return False
                self.profile_manager.delete(key)
                return True

        # Try episodic tree (topics don't have user-facing delete yet)
        if memory_id in self.episodic_tree.topics:
            # Only allow deleting non-root topics
            if memory_id == self.episodic_tree._root_id:
                return False
            del self.episodic_tree.topics[memory_id]
            self.episodic_tree.save()
            return True

        return False

    def increment_uses(self, ids: List[str]) -> None:
        if hasattr(self.memory_manager, "increment_uses"):
            self.memory_manager.increment_uses(ids)

    # --------------------------------------------------------------------- #
    # Tool schemas
    # --------------------------------------------------------------------- #

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "user_profile_update",
                    "description": "Update a structured user profile entry (e.g. name, preference, allergy).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "key": {
                                "type": "string",
                                "description": "Profile key, e.g. 'name', 'allergy', 'favorite_color'.",
                            },
                            "value": {
                                "type": "string",
                                "description": "Value for this profile key.",
                            },
                            "confidence": {
                                "type": "number",
                                "description": "Confidence level from 0.0 to 1.0.",
                                "default": 1.0,
                            },
                        },
                        "required": ["key", "value"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "user_profile_get",
                    "description": "Retrieve a user profile entry by key.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "key": {
                                "type": "string",
                                "description": "Profile key to retrieve.",
                            },
                        },
                        "required": ["key"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "user_profile_delete",
                    "description": "Delete a user profile entry by key.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "key": {
                                "type": "string",
                                "description": "Profile key to delete.",
                            },
                        },
                        "required": ["key"],
                    },
                },
            },
        ]

    async def handle_tool_call(self, name: str, arguments: Dict[str, Any]) -> Any:
        if name == "user_profile_update":
            key = arguments.get("key")
            value = arguments.get("value")
            confidence = arguments.get("confidence", 1.0)
            if not key or not value:
                return {"error": "Missing key or value"}
            entry = self.profile_manager.upsert(key, value, confidence=confidence, source="agent_tool")
            return {"success": True, "entry": entry.to_dict()}

        if name == "user_profile_get":
            key = arguments.get("key")
            if not key:
                return {"error": "Missing key"}
            entry = self.profile_manager.get(key)
            return {"success": True, "entry": entry.to_dict() if entry else None}

        if name == "user_profile_delete":
            key = arguments.get("key")
            if not key:
                return {"error": "Missing key"}
            ok = self.profile_manager.delete(key)
            return {"success": ok}

        raise KeyError(f"EnhancedMemoryProvider does not expose tool {name}")
