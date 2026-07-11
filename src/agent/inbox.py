"""Actor communication — inbox for message passing between actors."""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class InboxMessage:
    sender_id: str
    receiver_id: str
    content: str
    type: str = "text"


class Inbox:
    def __init__(self) -> None:
        self._messages: Dict[str, List[InboxMessage]] = defaultdict(list)

    def send(self, message: InboxMessage) -> None:
        self._messages[message.receiver_id].append(message)

    def receive(self, actor_id: str, type_filter: Optional[str] = None) -> List[InboxMessage]:
        messages = self._messages.get(actor_id, [])
        if type_filter:
            messages = [m for m in messages if m.type == type_filter]
        self._messages[actor_id] = [m for m in self._messages.get(actor_id, []) if m.type != type_filter] if type_filter else []
        return messages

    def send_notification(self, sender_id: str, receiver_id: str, status: str, summary: str, body: str = "", error: Optional[str] = None) -> None:
        content = f"**Status**: {status}\n**Summary**: {summary}"
        if body:
            content += f"\n\n{body}"
        if error:
            content += f"\n\n**Error**: {error}"
        self.send(InboxMessage(sender_id=sender_id, receiver_id=receiver_id, content=content, type="actor_notification"))

    def is_empty(self, actor_id: str) -> bool:
        return len(self._messages.get(actor_id, [])) == 0

    def pending_count(self, actor_id: str) -> int:
        return len(self._messages.get(actor_id, []))
