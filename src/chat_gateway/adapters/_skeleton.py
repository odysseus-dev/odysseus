"""SKELETON / EXAMPLE platform adapter — copy this to build a new one.

This file is NOT registered (see adapters/__init__.py). It exists as the
canonical starting point for adding any platform — self-hosted or third-party.

A platform adapter is TRANSPORT ONLY. It must:
  1. authenticate and learn its own bot identity (so it can ignore its own posts),
  2. receive inbound messages and turn each into an `IncomingMessage`,
  3. call `await self._dispatch(msg)` for each (the base class applies the shared
     rules — own-post guard, logging, typing hooks — then runs the agent via the
     GatewayRunner handler and calls `self.send(reply)`),
  4. implement `send()` to deliver a reply,
  5. (optional) implement the typing-indicator hooks.

It must NOT contain any agent / LLM / tool logic — that is the runner's job and
is shared by every platform.

To add a platform:
  1. Copy this file to adapters/<platform>.py and rename the class.
  2. Set `platform = "<key>"` (must match the config block name).
  3. Fill in connect(), listen(), send() using the platform's API/SDK.
  4. Register it in adapters/__init__.py (one line in ADAPTERS).
  5. Add a config block under `platforms:` in data/chat_gateway.yaml.
  6. See ADDING_A_PLATFORM.md for the full checklist (auth, realtime model,
     mention detection, dedup, rate limits, threading).

Transport choices already proven in this codebase:
  - httpx (core dep): REST + long-poll (see adapters/matrix.py).
  - websockets (optional dep): WebSocket event streams (see adapters/mattermost.py).
  - asyncio streams (stdlib): raw TCP protocols (see adapters/irc.py).
Avoid adding heavy SDKs unless necessary — keep the core lean (anti-bloat).
"""

from __future__ import annotations

import collections
import logging
from typing import Optional

from ..base import IncomingMessage, OutgoingMessage, PlatformAdapter

logger = logging.getLogger("chat_gateway")


class SkeletonAdapter(PlatformAdapter):
    # Must match the key used in the config `platforms:` block.
    platform = "skeleton"

    def __init__(self, config: dict):
        super().__init__(config)
        # Pull whatever this platform needs out of config.options:
        self._base_url = (config.get("base_url") or "").rstrip("/")
        self._token = config.get("token") or ""
        self._verify_tls = config.get("verify_tls", True)
        # Dedup cache so a redelivered event isn't answered twice.
        self._seen = collections.deque(maxlen=512)

    async def connect(self) -> bool:
        """Authenticate and resolve the bot's own id. Return False to abort.

        Set `self._bot_user_id` here — the base class uses it to ignore the
        bot's own messages (loop guard).
        """
        if not self._base_url or not self._token:
            logger.error("[%s] base_url and token are required", self.platform)
            return False
        # TODO: call the platform's "who am I" endpoint and set:
        # self._bot_user_id = ...
        logger.info("[%s] connected (skeleton — implement me)", self.platform)
        return False  # skeleton never actually connects

    async def listen(self) -> None:
        """Long-running receive loop. Build an IncomingMessage per inbound event
        and `await self._dispatch(msg)`. Reconnect on transient errors; return
        only on shutdown (the service wrapper also retries with backoff).

        Pseudo-shape:

            async for event in self._stream():          # WS / long-poll / TCP
                if not is_user_message(event):
                    continue
                post_id = event["id"]
                if post_id in self._seen:                # dedup
                    continue
                self._seen.append(post_id)
                msg = IncomingMessage(
                    platform=self.platform,
                    channel_id=event["channel"],
                    user_id=event["sender"],
                    text=strip_mention(event["text"]),
                    message_id=post_id,
                    thread_id=event.get("thread") or None,
                    was_mentioned=bot_is_mentioned(event),
                    is_direct=is_dm(event),
                    raw=event,
                )
                await self._dispatch(msg)
        """
        raise NotImplementedError

    async def send(self, message: OutgoingMessage) -> None:
        """Deliver `message.text` to `message.channel_id`. If the platform
        supports threads and `message.thread_id` is set, reply in that thread;
        otherwise reply inline."""
        raise NotImplementedError

    # Optional: show a "typing…" indicator while the agent works. Default no-op.
    # async def _typing_start(self, msg: IncomingMessage) -> None: ...
    # async def _typing_stop(self) -> None: ...

    def format(self, text: str) -> str:
        """Optional outbound formatting (e.g. markdown ↔ platform syntax)."""
        return text
