"""Context rebuild — restore agent understanding from checkpoint files."""
from __future__ import annotations

import logging
from typing import Any, Dict, List
from src.agent.checkpoint_writer import CheckpointWriter

logger = logging.getLogger(__name__)


class ContextRebuilder:
    def __init__(self, base_dir: str) -> None:
        self.base_dir = base_dir
        self.writer = CheckpointWriter(base_dir)

    def needs_rebuild(self) -> bool:
        checkpoint = self.writer.checkpoint_store.read()
        return bool(checkpoint.strip() and len(checkpoint.strip()) > 50)

    def build_rebuild_message(self) -> str:
        return self.writer.rebuild_context()

    def build_system_message(self) -> Dict[str, str]:
        content = self.build_rebuild_message()
        if not content:
            return {"role": "system", "content": ""}
        return {"role": "system", "content": "## Context Rebuild\nYour previous context was compacted. Here is the restored state from checkpoint:\n\n" + content}

    def compact_messages(self, messages: List[Dict[str, Any]], keep_recent: int = 4) -> List[Dict[str, Any]]:
        if len(messages) <= keep_recent + 2:
            return messages
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]
        if len(non_system) > keep_recent:
            preserved = non_system[-keep_recent:]
            rebuild_msg = self.build_system_message()
            if rebuild_msg.get("content"):
                return system_msgs + [rebuild_msg] + preserved
            return system_msgs + preserved
        return messages

    def inject_checkpoint_into_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        rebuild_msg = self.build_system_message()
        if not rebuild_msg.get("content"):
            return messages
        last_user_idx = len(messages) - 1
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                last_user_idx = i
                break
        return messages[:last_user_idx] + [rebuild_msg] + messages[last_user_idx:]
