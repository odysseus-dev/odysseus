"""Mattermost platform adapter (transport only).

Connects to a Mattermost server, listens on the WebSocket event stream, and
sends replies via the REST API. No agent logic — that's in GatewayRunner.

Transport:
  - REST  : httpx (already a core Odysseus dependency)
  - WebSocket : the `websockets` library (OPTIONAL dep — only needed when the
                gateway is enabled; add `websockets` to requirements-optional.txt).

Auth: a Mattermost **bot account access token** (Authorization: Bearer ...).

Clean-room implementation; architecture referenced from NousResearch/hermes-agent
(MIT) gateway/platforms/mattermost/adapter.py.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
from typing import Optional

import httpx

from ..base import IncomingMessage, OutgoingMessage, PlatformAdapter

logger = logging.getLogger("chat_gateway")


class MattermostAdapter(PlatformAdapter):
    platform = "mattermost"

    def __init__(self, config: dict):
        super().__init__(config)
        self._base_url = (config.get("base_url") or "").rstrip("/")
        self._token = config.get("token") or ""
        self._verify_tls = config.get("verify_tls", True)
        self._bot_username: Optional[str] = None
        self._seen = collections.deque(maxlen=512)   # post-id dedup
        self._seq = 0
        self._ws = None                              # live socket (for typing sends)
        self._typing_task: Optional[asyncio.Task] = None

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    def _api(self, path: str) -> str:
        return f"{self._base_url}/api/v4{path}"

    # ── connect: resolve the bot identity ───────────────────────────────
    async def connect(self) -> bool:
        if not self._base_url or not self._token:
            logger.error("[mattermost] base_url and token are required")
            return False
        try:
            async with httpx.AsyncClient(verify=self._verify_tls, timeout=15) as client:
                r = await client.get(self._api("/users/me"), headers=self._headers)
                r.raise_for_status()
                me = r.json()
        except Exception:
            logger.exception("[mattermost] /users/me failed — bad URL or token?")
            return False
        self._bot_user_id = me.get("id")
        self._bot_username = me.get("username")
        logger.info("[mattermost] connected as @%s (%s)", self._bot_username, self._bot_user_id)
        return True

    # ── listen: WebSocket event stream ──────────────────────────────────
    async def listen(self) -> None:
        try:
            import websockets  # optional dependency
        except ImportError:
            logger.error("[mattermost] the 'websockets' package is required for the gateway "
                         "but is not installed (add it to requirements-optional.txt)")
            return

        ws_url = self._base_url.replace("https://", "wss://").replace("http://", "ws://") + "/api/v4/websocket"
        async with websockets.connect(ws_url, max_size=2 ** 22) as ws:
            # Mattermost auth handshake over the socket (avoids header-arg
            # differences between websockets versions).
            self._seq += 1
            await ws.send(json.dumps({
                "seq": self._seq,
                "action": "authentication_challenge",
                "data": {"token": self._token},
            }))
            self._ws = ws
            logger.info("[mattermost] websocket connected; listening")
            try:
                async for raw in ws:
                    try:
                        event = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    await self._on_event(event)
            finally:
                self._ws = None

    async def _on_event(self, event: dict) -> None:
        if event.get("event") != "posted":
            return
        data = event.get("data") or {}
        try:
            post = json.loads(data.get("post") or "{}")
        except (json.JSONDecodeError, TypeError):
            return

        # Skip system messages (join/leave/add-to-channel/header changes, etc.).
        # These have a non-empty post `type`; real user messages have type "".
        if post.get("type"):
            return

        post_id = post.get("id")
        if not post_id or post_id in self._seen:
            return
        self._seen.append(post_id)

        user_id = post.get("user_id")
        if self._bot_user_id and user_id == self._bot_user_id:
            return  # our own post

        channel_type = data.get("channel_type")        # "D" direct, "O" open, "P" private
        is_direct = channel_type == "D"

        # Mention detection: MM ships a JSON list of mentioned user ids on the event.
        was_mentioned = False
        try:
            mentions = json.loads(data.get("mentions") or "[]")
            was_mentioned = self._bot_user_id in mentions
        except (json.JSONDecodeError, TypeError):
            pass

        text = self._strip_mention(post.get("message") or "")
        root_id = post.get("root_id") or None

        msg = IncomingMessage(
            platform=self.platform,
            channel_id=post.get("channel_id", ""),
            user_id=user_id or "",
            text=text,
            message_id=post_id,
            thread_id=root_id,
            was_mentioned=was_mentioned,
            is_direct=is_direct,
            raw=post,
        )
        await self._dispatch(msg)

    def _strip_mention(self, text: str) -> str:
        if self._bot_username:
            text = text.replace(f"@{self._bot_username}", "")
        return text.strip()

    # ── send: REST ──────────────────────────────────────────────────────
    async def send(self, message: OutgoingMessage) -> None:
        payload = {"channel_id": message.channel_id, "message": self.format(message.text)}
        if message.thread_id:
            payload["root_id"] = message.thread_id
        async with httpx.AsyncClient(verify=self._verify_tls, timeout=30) as client:
            r = await client.post(self._api("/posts"), headers=self._headers, json=payload)
            if r.status_code >= 300:
                logger.warning("[mattermost] post failed %s: %s", r.status_code, r.text[:200])

    # ── "typing…" indicator while the agent works ───────────────────────
    async def _typing_start(self, msg) -> None:
        self._typing_task = asyncio.create_task(self._typing_loop(msg.channel_id, msg.thread_id))

    async def _typing_stop(self) -> None:
        if self._typing_task:
            self._typing_task.cancel()
            self._typing_task = None

    async def _typing_loop(self, channel_id: str, parent_id: Optional[str]) -> None:
        """Publish user_typing over the WS every ~2s until cancelled. Mattermost
        expires a typing indicator after a few seconds, so it must be refreshed."""
        data = {"channel_id": channel_id}
        if parent_id:
            data["parent_id"] = parent_id
        try:
            while True:
                if self._ws is not None:
                    try:
                        self._seq += 1
                        await self._ws.send(json.dumps({
                            "seq": self._seq, "action": "user_typing", "data": data,
                        }))
                    except Exception:
                        pass  # transient; keep trying until cancelled
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            return
