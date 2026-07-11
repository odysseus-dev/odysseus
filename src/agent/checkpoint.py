"""Context management: token estimation, compaction, checkpoint triggers."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class CompactionResult:
    messages: List[Dict[str, Any]]
    removed_count: int
    tokens_saved: int
    summary: str = ""


@dataclass
class ContextManager:
    max_tokens: int = 8192
    compaction_threshold: float = 0.8
    rebuild_threshold: float = 0.95
    keep_recent: int = 4
    _current_tokens: int = field(default=0, init=False)

    @property
    def current_tokens(self) -> int:
        return self._current_tokens

    def add_tokens(self, count: int) -> None:
        self._current_tokens += count

    def needs_compaction(self) -> bool:
        return self._current_tokens >= self.max_tokens * self.compaction_threshold

    def needs_checkpoint_rebuild(self) -> bool:
        return self._current_tokens >= self.max_tokens * self.rebuild_threshold

    def compact_messages(self, messages: List[Dict[str, Any]], keep_recent: Optional[int] = None) -> CompactionResult:
        keep = keep_recent or self.keep_recent
        if len(messages) <= keep + 2:
            return CompactionResult(messages=messages, removed_count=0, tokens_saved=0)
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]
        if len(non_system) > keep:
            removed = non_system[:-keep]
            preserved = non_system[-keep:]
            tokens_saved = sum(len(m.get("content", "")) // 4 for m in removed)
            compacted = system_msgs + [{"role": "user", "content": "[Context compacted — older messages summarized]"}] + preserved
            return CompactionResult(messages=compacted, removed_count=len(removed), tokens_saved=tokens_saved, summary=f"Removed {len(removed)} old messages, ~{tokens_saved} tokens saved.")
        return CompactionResult(messages=messages, removed_count=0, tokens_saved=0)

    def microcompact_tool_results(self, messages: List[Dict[str, Any]], max_result_tokens: int = 200) -> List[Dict[str, Any]]:
        result = []
        for msg in messages:
            if msg.get("role") != "tool":
                result.append(msg)
                continue
            content = msg.get("content", "")
            estimated = len(content) // 4
            if estimated > max_result_tokens:
                truncated = content[:max_result_tokens * 4]
                msg_copy = dict(msg)
                msg_copy["content"] = f"[Tool output truncated — {estimated} tokens, showing first {max_result_tokens} tokens]\n{truncated}\n..."
                result.append(msg_copy)
            else:
                result.append(msg)
        return result

    def reset(self) -> None:
        self._current_tokens = 0