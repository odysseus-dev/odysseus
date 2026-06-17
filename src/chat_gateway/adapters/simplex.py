"""SimpleX Chat platform adapter (transport only).

SimpleX has no tokens/accounts in the usual sense and no REST API. A bot drives
a locally-run SimpleX Chat CLI that exposes a WebSocket command server:

    simplex-chat -p 5225        # (optionally -d <db-prefix> for a dedicated profile)

The adapter connects to ws://<host>:<port> and speaks the CLI JSON protocol:
  - send:   {"corrId": "<id>", "cmd": "<terminal command string>"}
  - reply:  {"corrId": "<id>", "resp": {...}}            (matches corrId)
  - event:  {"corrId": null,   "resp": {"type": "...", ...}}   (async, e.g. messages)

`cmd` strings are the same as the terminal CLI — e.g. `@alice hello` (direct),
`#team hello` (group), `/show_active_user`, `/auto_accept on`.

Realtime via the `websockets` library (optional dep, already used by Mattermost).

Status: written to the verified adapter contract + the documented CLI WS protocol;
NOT yet tested end-to-end (needs a running `simplex-chat` CLI server). Field names
are read defensively because the schema varies slightly across CLI versions.

Refs: simplex-chat WebSocket/Bot API (bots/api, packages/simplex-chat-client).
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import uuid
from typing import Optional

from ..base import IncomingMessage, OutgoingMessage, PlatformAdapter

logger = logging.getLogger("chat_gateway")


class SimplexAdapter(PlatformAdapter):
    platform = "simplex"

    def __init__(self, config: dict):
        super().__init__(config)
        # The local simplex-chat CLI websocket, e.g. ws://127.0.0.1:5225
        self._ws_url = config.get("ws_url") or config.get("base_url") or ""
        self._auto_accept = config.get("auto_accept", True)
        self._display_name: Optional[str] = None     # bot's own profile name (group mention)
        self._seen = collections.deque(maxlen=1024)
        self._ws = None

    async def connect(self) -> bool:
        if not self._ws_url:
            logger.error("[simplex] ws_url is required (the simplex-chat CLI websocket)")
            return False
        return True

    async def _cmd(self, command: str) -> None:
        if self._ws is None:
            return
        await self._ws.send(json.dumps({"corrId": f"ody-{uuid.uuid4().hex[:8]}", "cmd": command}))

    async def listen(self) -> None:
        try:
            import websockets
        except ImportError:
            logger.error("[simplex] the 'websockets' package is required but not installed")
            return

        async with websockets.connect(self._ws_url, max_size=2 ** 22) as ws:
            self._ws = ws
            # Learn our own profile name, and (optionally) auto-accept new contacts
            # so users can connect to the bot without manual approval.
            await self._cmd("/show_active_user")
            if self._auto_accept:
                await self._cmd("/auto_accept on")
            await self._cmd("/show_address")
            logger.info("[simplex] connected to CLI at %s; listening", self._ws_url)
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
        # The type can be nested under "resp" or at the top level depending on
        # the CLI version; normalise both (matches Hermes' proven handling).
        resp = event.get("resp") if isinstance(event.get("resp"), dict) else event
        rtype = event.get("type") or resp.get("type") or ""

        # Capture our own display name from the active-user response.
        if rtype in ("activeUser", "activeUserSet") or "user" in resp:
            u = resp.get("user") or {}
            prof = (u.get("profile") or {})
            name = prof.get("displayName") or prof.get("localDisplayName")
            if name and not self._display_name:
                self._display_name = name
                self._bot_user_id = name

        # New inbound message(s). Newer CLI: newChatItems (list of AChatItem);
        # older: newChatItem with chatInfo/chatItem flat on the response.
        if rtype == "newChatItems":
            for achat in (resp.get("chatItems") or []):
                await self._handle_chat_item(achat)
        elif rtype in ("newChatItem", "chatItemReceived"):
            await self._handle_chat_item(resp)

    async def _handle_chat_item(self, achat: dict) -> None:
        chat_info = achat.get("chatInfo") or {}
        chat_item = achat.get("chatItem") or {}
        content = chat_item.get("content") or {}

        # Only react to *received* messages (skip our own sent items → loop guard).
        if content.get("type") != "rcvMsgContent":
            return
        msg_content = content.get("msgContent") or {}
        if msg_content.get("type") != "text":
            return
        text = msg_content.get("text") or ""

        meta = chat_item.get("meta") or {}
        item_id = str(meta.get("itemId") or "")
        if item_id and item_id in self._seen:
            return
        if item_id:
            self._seen.append(item_id)

        ci_type = chat_info.get("type")
        if ci_type == "direct":
            contact = chat_info.get("contact") or {}
            name = contact.get("localDisplayName") or contact.get("displayName") or ""
            sender = name
            channel_id = "@" + name            # CLI address token used by send()
            is_direct = True
            was_mentioned = False
        elif ci_type == "group":
            group = chat_info.get("groupInfo") or {}
            gname = group.get("localDisplayName") or group.get("displayName") or ""
            channel_id = "#" + gname
            is_direct = False
            sender = self._sender_from_group_item(chat_item)
            was_mentioned = bool(self._display_name and self._display_name.lower() in text.lower())
            if was_mentioned:
                text = text.replace(self._display_name, "").strip()
        else:
            return

        if self._bot_user_id and sender == self._bot_user_id:
            return

        msg = IncomingMessage(
            platform=self.platform,
            channel_id=channel_id,
            user_id=sender or "",
            text=text.strip(),
            message_id=item_id,
            thread_id=None,
            was_mentioned=was_mentioned,
            is_direct=is_direct,
            raw=achat,
        )
        await self._dispatch(msg)

    @staticmethod
    def _sender_from_group_item(chat_item: dict) -> str:
        # chatDir for a received group message names the member.
        d = chat_item.get("chatDir") or {}
        member = d.get("groupMember") or {}
        return member.get("localDisplayName") or member.get("memberProfile", {}).get("displayName") or ""

    async def send(self, message: OutgoingMessage) -> None:
        # channel_id already carries the CLI addressing prefix ("@name" or "#group").
        for line in message.text.split("\n"):
            line = line.strip()
            if line:
                await self._cmd(f"{message.channel_id} {line}")
