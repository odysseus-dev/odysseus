"""Profile memory — structured user facts and preferences.

MemMachine-inspired key-value store with upsert-by-key semantics.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ProfileEntry:
    """A structured user fact."""

    id: str
    key: str
    value: str
    owner: Optional[str] = None
    confidence: float = 1.0
    source: str = "agent"
    timestamp: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "key": self.key,
            "value": self.value,
            "owner": self.owner,
            "confidence": self.confidence,
            "source": self.source,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> ProfileEntry:
        return cls(
            id=d["id"],
            key=d["key"],
            value=d["value"],
            owner=d.get("owner"),
            confidence=d.get("confidence", 1.0),
            source=d.get("source", "agent"),
            timestamp=d.get("timestamp", 0),
            metadata=d.get("metadata", {}),
        )


class ProfileManager:
    """JSON-backed profile store with upsert-by-key semantics."""

    def __init__(self, data_dir: str, *, owner: Optional[str] = None):
        self.data_dir = data_dir
        self.owner = owner
        self._file_path = self._path_for_owner(owner)
        self._entries: Dict[str, ProfileEntry] = {}  # key -> entry
        self.load()

    def _path_for_owner(self, owner: Optional[str]) -> str:
        fname = f"profile_{owner or 'default'}.json"
        return os.path.join(self.data_dir, fname)

    # --------------------------------------------------------------------- #
    # Persistence
    # --------------------------------------------------------------------- #

    def load(self) -> None:
        if not os.path.exists(self._file_path):
            return
        try:
            with open(self._file_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for d in raw.get("entries", []):
                entry = ProfileEntry.from_dict(d)
                self._entries[entry.key] = entry
        except Exception as e:
            logger.error("Failed to load profile: %s", e)

    def save(self) -> None:
        data = {"entries": [e.to_dict() for e in self._entries.values()]}
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            with open(self._file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error("Failed to save profile: %s", e)

    # --------------------------------------------------------------------- #
    # CRUD
    # --------------------------------------------------------------------- #

    def upsert(
        self,
        key: str,
        value: str,
        *,
        confidence: float = 1.0,
        source: str = "agent",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ProfileEntry:
        """Add or update a profile entry by key."""
        existing = self._entries.get(key)
        entry = ProfileEntry(
            id=existing.id if existing else str(uuid.uuid4()),
            key=key,
            value=value,
            owner=self.owner,
            confidence=confidence,
            source=source,
            timestamp=int(time.time()),
            metadata=metadata or {},
        )
        self._entries[key] = entry
        self.save()
        return entry

    def get(self, key: str) -> Optional[ProfileEntry]:
        return self._entries.get(key)

    def get_value(self, key: str) -> Optional[str]:
        entry = self._entries.get(key)
        return entry.value if entry else None

    def delete(self, key: str) -> bool:
        if key not in self._entries:
            return False
        del self._entries[key]
        self.save()
        return True

    def list_all(self) -> List[ProfileEntry]:
        return list(self._entries.values())

    def search(self, query: str) -> List[ProfileEntry]:
        """Simple keyword search over keys and values."""
        q = query.lower()
        results = []
        for entry in self._entries.values():
            if q in entry.key.lower() or q in entry.value.lower():
                results.append(entry)
        return results

    # --------------------------------------------------------------------- #
    # Auto-promotion heuristic
    # --------------------------------------------------------------------- #

    _PROFILE_PATTERNS = {
        "name": r"my name is\s+(.+)",
        "location": r"i (?:live in|am from|am located in)\s+(.+)",
        "occupation": r"i (?:work as|am a)\s+(.+)",
        "preference": r"i (?:like|love|prefer|hate|dislike)\s+(.+)",
    }

    def try_extract_from_text(self, text: str) -> Optional[ProfileEntry]:
        """Attempt to extract a profile entry from free-form text."""
        import re
        lower = text.lower()
        for key, pattern in self._PROFILE_PATTERNS.items():
            m = re.search(pattern, lower)
            if m:
                value = m.group(1).strip(". ")
                return self.upsert(key, value, source="auto_extract")
        return None
