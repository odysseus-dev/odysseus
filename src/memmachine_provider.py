"""MemMachine memory provider for Odysseus.

Implements the MemoryProvider ABC using the MemMachine Python client.
MemMachine is an optional dependency; if it is not installed or the server
is unreachable, the provider degrades gracefully and the native memory
provider continues to serve all operations.

Environment variables:
  MEMMACHINE_URL      — MemMachine server base URL (default: http://localhost:8080)
  MEMMACHINE_ORG_ID   — Organisation ID (default: odysseus)
  MEMMACHINE_PROJECT_ID — Project ID (default: default)
  MEMMACHINE_GROUP_ID — Group ID (default: main)
  MEMMACHINE_AGENT_ID — Agent ID (default: assistant)
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from src.memory_provider import MemoryProvider, MemoryRecord, MemorySearchHit

logger = logging.getLogger(__name__)

# Mapping file persists the odysseus_id → memmachine_uid relationship across
# restarts so delete() can target the correct remote record.
_DEFAULT_MAPPING_FILE = os.path.join("data", "memmachine_id_map.json")

# MemMachine result path helpers (defensive because the exact schema may
# evolve). We try the most common nesting first and fall back to treating the
# result as a plain list of dicts.


def _extract_episodes(result: Any) -> List[Dict[str, Any]]:
    """Defensively pull episode-like objects out of a MemMachine search result."""
    if result is None:
        return []

    # README example: result.content.episodic_memory.long_term_memory.episodes
    try:
        episodes = (
            result.content.episodic_memory.long_term_memory.episodes
        )
        if isinstance(episodes, list):
            return episodes
    except Exception:
        pass

    # Flat list of dicts
    if isinstance(result, list):
        return result

    # Dict with an 'episodes' key
    if isinstance(result, dict):
        for key in ("episodes", "results", "memories", "items", "data"):
            val = result.get(key)
            if isinstance(val, list):
                return val

    return []


def _episode_to_dict(ep: Any) -> Dict[str, Any]:
    """Normalise a MemMachine episode object to a plain dict."""
    if isinstance(ep, dict):
        return ep
    # Pydantic-style or dataclass-style objects
    if hasattr(ep, "model_dump"):
        try:
            return ep.model_dump()
        except Exception:
            pass
    if hasattr(ep, "dict"):
        try:
            return ep.dict()
        except Exception:
            pass
    if hasattr(ep, "__dataclass_fields__"):
        try:
            return ep.__dict__
        except Exception:
            pass
    # Fallback: expose a minimal dict with 'content' as str()
    return {"content": str(ep)}


class MemMachineMemoryProvider(MemoryProvider):
    """MemMachine-backed memory provider."""

    provider_id = "memmachine"
    display_name = "MemMachine"

    def __init__(
        self,
        base_url: Optional[str] = None,
        org_id: Optional[str] = None,
        project_id: Optional[str] = None,
        group_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        mapping_file: Optional[str] = None,
    ):
        self._base_url = base_url or os.getenv(
            "MEMMACHINE_URL", "http://localhost:8080"
        )
        self._org_id = org_id or os.getenv("MEMMACHINE_ORG_ID", "odysseus")
        self._project_id = project_id or os.getenv(
            "MEMMACHINE_PROJECT_ID", "default"
        )
        self._group_id = group_id or os.getenv("MEMMACHINE_GROUP_ID", "main")
        self._agent_id = agent_id or os.getenv("MEMMACHINE_AGENT_ID", "assistant")
        self._mapping_file = mapping_file or _DEFAULT_MAPPING_FILE
        self._id_map: Dict[str, str] = {}
        self._client = None
        self._project = None
        self._healthy = False
        self._load_mapping()
        self._initialize()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_mapping(self) -> None:
        try:
            with open(self._mapping_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    self._id_map = data
        except (FileNotFoundError, json.JSONDecodeError):
            self._id_map = {}

    def _save_mapping(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._mapping_file) or ".", exist_ok=True)
            tmp = f"{self._mapping_file}.tmp.{os.getpid()}"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._id_map, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._mapping_file)
        except Exception as e:
            logger.warning("Failed to save MemMachine id mapping: %s", e)

    def _initialize(self) -> None:
        try:
            from memmachine_client import MemMachineClient
        except ImportError as e:
            logger.warning(
                "memmachine-client not installed. "
                "Install it from requirements-optional.txt to use MemMachine."
            )
            self._healthy = False
            return

        try:
            self._client = MemMachineClient(base_url=self._base_url)
            self._project = self._client.get_or_create_project(
                org_id=self._org_id, project_id=self._project_id
            )
            self._healthy = True
            logger.info(
                "MemMachine provider ready: org=%s project=%s url=%s",
                self._org_id,
                self._project_id,
                self._base_url,
            )
        except Exception as e:
            logger.warning("MemMachine provider init failed: %s", e)
            self._healthy = False

    def _memory(self, owner: Optional[str], session_id: Optional[str]):
        """Build a scoped MemMachine memory handle."""
        return self._project.memory(
            group_id=self._group_id,
            agent_id=self._agent_id,
            user_id=owner or "anonymous",
            session_id=session_id or "default",
        )

    @property
    def healthy(self) -> bool:
        return self._healthy

    # ------------------------------------------------------------------
    # MemoryProvider ABC
    # ------------------------------------------------------------------

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
        if not self._healthy:
            raise RuntimeError("MemMachine provider is not healthy")

        odysseus_id = str(uuid.uuid4())
        meta: Dict[str, Any] = dict(metadata or {})
        meta.update(
            {
                "category": category,
                "source": source,
                "odysseus_id": odysseus_id,
                "timestamp": int(time.time()),
            }
        )

        mm = self._memory(owner, session_id)
        try:
            result = mm.add(text, metadata=meta)
        except Exception as e:
            logger.error("MemMachine remember failed: %s", e)
            raise RuntimeError(f"MemMachine add failed: {e}") from e

        # Extract UID from the returned AddMemoryResult list
        uid = None
        if isinstance(result, list) and result:
            first = result[0]
            uid = getattr(first, "uid", None)
        elif hasattr(result, "uid"):
            uid = result.uid

        if uid:
            self._id_map[odysseus_id] = uid
            self._save_mapping()
        else:
            logger.warning(
                "MemMachine add succeeded but no UID was returned; "
                "delete() will not be able to target this record."
            )

        return MemoryRecord(
            id=odysseus_id,
            text=text,
            timestamp=meta["timestamp"],
            category=category,
            source=source,
            owner=owner,
            session_id=session_id,
            metadata=meta,
        )

    async def recall(
        self,
        query: str,
        *,
        owner: Optional[str] = None,
        top_k: int = 5,
    ) -> List[MemorySearchHit]:
        if not self._healthy:
            return []

        mm = self._memory(owner, None)
        try:
            raw = mm.search(query)
        except Exception as e:
            logger.warning("MemMachine search failed: %s", e)
            return []

        episodes = _extract_episodes(raw)
        hits: List[MemorySearchHit] = []
        seen_ids: set = set()

        for ep in episodes[:top_k]:
            d = _episode_to_dict(ep)
            content = d.get("content", d.get("text", ""))
            if not content:
                continue

            odysseus_id = d.get("metadata", {}).get("odysseus_id") if isinstance(
                d.get("metadata"), dict
            ) else None
            if not odysseus_id:
                odysseus_id = str(uuid.uuid4())

            # Deduplicate within this provider's own results
            if odysseus_id in seen_ids:
                continue
            seen_ids.add(odysseus_id)

            meta = d.get("metadata", {}) if isinstance(d.get("metadata"), dict) else {}
            score = d.get("score")
            if score is None and hasattr(ep, "score"):
                score = getattr(ep, "score", None)

            hits.append(
                MemorySearchHit(
                    memory=MemoryRecord(
                        id=odysseus_id,
                        text=content,
                        timestamp=meta.get("timestamp", int(time.time())),
                        category=meta.get("category", "fact"),
                        source=meta.get("source", "unknown"),
                        owner=owner,
                        metadata=meta,
                    ),
                    provider_id=self.provider_id,
                    score=score,
                )
            )

        return hits

    async def list_memories(
        self,
        *,
        owner: Optional[str] = None,
        limit: int = 100,
    ) -> List[MemoryRecord]:
        """Best-effort list via a broad search query.

        MemMachine does not expose a direct "list all" API, so we issue a
        wildcard-style search. The results may be incomplete.
        """
        if not self._healthy:
            return []

        # Try a broad recall and return the memories
        hits = await self.recall("*", owner=owner, top_k=limit)
        return [h.memory for h in hits]

    def increment_uses(self, ids: List[str]) -> None:
        """MemMachine tracks usage internally; no-op for Odysseus counters."""

    async def delete(self, memory_id: str, *, owner: Optional[str] = None) -> bool:
        if not self._healthy:
            return False

        uid = self._id_map.get(memory_id)
        if not uid:
            logger.warning(
                "MemMachine delete: no UID mapping for odysseus_id=%s", memory_id
            )
            return False

        mm = self._memory(owner, None)
        try:
            # MemMachine API for deleting a single memory is not documented in
            # the public README. We try common method names and fail gracefully.
            deleted = False
            for method_name in ("delete", "remove", "forget"):
                if hasattr(mm, method_name):
                    method = getattr(mm, method_name)
                    try:
                        method(uid)
                        deleted = True
                        break
                    except Exception:
                        pass

            if not deleted:
                logger.warning(
                    "MemMachine delete: no suitable delete method found on memory object"
                )
                return False

            self._id_map.pop(memory_id, None)
            self._save_mapping()
            return True
        except Exception as e:
            logger.warning("MemMachine delete failed: %s", e)
            return False
