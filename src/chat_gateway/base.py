"""Chat Gateway — platform adapter contract.

A platform adapter is *transport only*. It knows how to connect to one
messaging platform, turn inbound events into `IncomingMessage`, and send an
`OutgoingMessage` back. It contains no agent logic — that lives in
`runner.GatewayRunner`, shared by every platform (the Hermes pattern).

Reference: NousResearch/hermes-agent (MIT) gateway/platforms/* — same shape
(thin adapter + one shared agent runner + per-platform toolsets).
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

logger = logging.getLogger("chat_gateway")


@dataclass
class IncomingMessage:
    """A user message received from a platform, normalised."""

    platform: str                      # "mattermost", "telegram", ...
    channel_id: str                    # where to reply
    user_id: str                       # platform id of the sender
    text: str                          # message body, @mention already stripped
    message_id: str = ""               # platform post id (for dedup / threading)
    thread_id: Optional[str] = None    # root post id if this is in a thread
    was_mentioned: bool = False        # did the message @mention the bot?
    is_direct: bool = False            # DM / 1:1 channel?
    raw: dict = field(default_factory=dict)   # original event, for adapter use


@dataclass
class OutgoingMessage:
    """A reply to deliver back to a platform."""

    channel_id: str
    text: str
    thread_id: Optional[str] = None


# The runner hands the adapter this callback; the adapter calls it for every
# inbound message and awaits the resulting reply (or None to stay silent).
MessageHandler = Callable[[IncomingMessage], Awaitable[Optional[OutgoingMessage]]]


class PlatformAdapter(abc.ABC):
    """Base class for a single-platform transport adapter."""

    #: lowercase platform key, must match the config block name
    platform: str = "base"

    def __init__(self, config: dict):
        self.config = config or {}
        self._handler: Optional[MessageHandler] = None
        self._bot_user_id: Optional[str] = None

    def set_message_handler(self, handler: MessageHandler) -> None:
        self._handler = handler

    async def _dispatch(self, msg: IncomingMessage) -> None:
        """Adapters call this for each inbound message. Applies the shared
        gateway rules (handler present, not our own post) then sends the reply."""
        if self._handler is None:
            logger.warning("[%s] no message handler set; dropping message", self.platform)
            return
        if self._bot_user_id and msg.user_id == self._bot_user_id:
            return  # never respond to our own posts (loop guard)
        logger.info("[%s] received post %s in %s (mention=%s dm=%s): %.80r",
                    self.platform, msg.message_id, msg.channel_id, msg.was_mentioned, msg.is_direct, msg.text)
        await self._typing_start(msg)
        try:
            reply = await self._handler(msg)
        except Exception:
            logger.exception("[%s] handler error", self.platform)
            return
        finally:
            await self._typing_stop()
        if reply and reply.text.strip():
            try:
                await self.send(reply)
            except Exception:
                logger.exception("[%s] send failed", self.platform)

    # ── optional "typing" indicator hooks (overridden per platform) ─────
    async def _typing_start(self, msg: "IncomingMessage") -> None:
        return None

    async def _typing_stop(self) -> None:
        return None

    # ── transport contract ─────────────────────────────────────────────
    @abc.abstractmethod
    async def connect(self) -> bool:
        """Authenticate / resolve the bot identity. Return True on success."""

    @abc.abstractmethod
    async def listen(self) -> None:
        """Long-running: receive events and call `self._dispatch(...)`.
        Should reconnect on transient failure and return only on shutdown."""

    @abc.abstractmethod
    async def send(self, message: OutgoingMessage) -> None:
        """Deliver a reply to the platform."""

    async def disconnect(self) -> None:
        """Optional clean shutdown hook."""
        return None

    def format(self, text: str) -> str:
        """Platform-specific outbound formatting. Default: passthrough."""
        return text
