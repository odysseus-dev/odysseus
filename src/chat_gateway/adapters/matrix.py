"""Matrix platform adapter (transport only).

Targets a self-hosted homeserver (Synapse/Dendrite/Conduit). Realtime via the
Client-Server API /sync long-poll — no extra dependency beyond httpx (core).

Auth: a bot user **access token** (Bearer). Create a dedicated Matrix user for
the bot and use its access token.

Status: written to the verified adapter contract; NOT yet tested end-to-end
(needs a live homeserver — e.g. the Proxmox "Element Synapse" community script).
"""

from __future__ import annotations

import asyncio
import collections
import logging
import uuid
from typing import Optional, Set
from urllib.parse import quote

import httpx

from ..base import IncomingMessage, OutgoingMessage, PlatformAdapter

logger = logging.getLogger("chat_gateway")

_CS = "/_matrix/client/v3"


class MatrixAdapter(PlatformAdapter):
    platform = "matrix"

    def __init__(self, config: dict):
        super().__init__(config)
        self._base_url = (config.get("base_url") or "").rstrip("/")
        self._token = config.get("token") or ""
        self._verify_tls = config.get("verify_tls", True)
        self._auto_join = config.get("auto_join", True)   # join rooms on invite
        self._display_name: Optional[str] = None
        self._since: Optional[str] = None
        self._direct_rooms: Set[str] = set()
        self._enc_warned: Set[str] = set()   # rooms we've warned are encrypted
        self._seen = collections.deque(maxlen=1024)
        self._typing_task: Optional[asyncio.Task] = None
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    def _u(self, path: str) -> str:
        return f"{self._base_url}{path}"

    async def connect(self) -> bool:
        if not self._base_url or not self._token:
            logger.error("[matrix] base_url and token are required")
            return False
        self._client = httpx.AsyncClient(verify=self._verify_tls, timeout=60)
        try:
            r = await self._client.get(self._u(f"{_CS}/account/whoami"), headers=self._headers)
            r.raise_for_status()
            self._bot_user_id = r.json().get("user_id")
            try:
                dn = await self._client.get(
                    self._u(f"{_CS}/profile/{quote(self._bot_user_id)}/displayname"),
                    headers=self._headers,
                )
                self._display_name = dn.json().get("displayname") if dn.status_code == 200 else None
            except Exception:
                self._display_name = None
        except Exception:
            logger.exception("[matrix] whoami failed — bad homeserver URL or token?")
            return False
        logger.info("[matrix] connected as %s (%s)", self._bot_user_id, self._display_name)
        return True

    async def listen(self) -> None:
        assert self._client is not None
        # Initial sync: get a `since` token but DON'T answer pre-existing history.
        params = {"timeout": "0"}
        r = await self._client.get(self._u(f"{_CS}/sync"), headers=self._headers, params=params)
        r.raise_for_status()
        data = r.json()
        self._since = data.get("next_batch")
        self._update_direct_rooms(data)
        logger.info("[matrix] initial sync complete; listening")

        while True:
            params = {"since": self._since, "timeout": "30000"}
            try:
                r = await self._client.get(self._u(f"{_CS}/sync"), headers=self._headers, params=params)
                r.raise_for_status()
                data = r.json()
            except Exception:
                logger.exception("[matrix] sync error; retrying shortly")
                await asyncio.sleep(3)
                continue
            self._since = data.get("next_batch", self._since)
            self._update_direct_rooms(data)
            if self._auto_join:
                await self._accept_invites(data)
            await self._process_joined(data)

    def _update_direct_rooms(self, data: dict) -> None:
        for ev in (data.get("account_data", {}) or {}).get("events", []) or []:
            if ev.get("type") == "m.direct":
                rooms: Set[str] = set()
                for _uid, rids in (ev.get("content") or {}).items():
                    rooms.update(rids or [])
                self._direct_rooms = rooms

    async def _accept_invites(self, data: dict) -> None:
        invites = (data.get("rooms", {}) or {}).get("invite", {}) or {}
        for room_id in invites:
            try:
                await self._client.post(self._u(f"{_CS}/join/{quote(room_id)}"), headers=self._headers)
                logger.info("[matrix] joined room on invite: %s", room_id)
            except Exception:
                logger.warning("[matrix] failed to join %s", room_id)

    async def _process_joined(self, data: dict) -> None:
        joined = (data.get("rooms", {}) or {}).get("join", {}) or {}
        for room_id, room in joined.items():
            for ev in (room.get("timeline", {}) or {}).get("events", []) or []:
                # Encrypted rooms: this lean httpx/sync adapter can't decrypt
                # (E2EE would need the heavy mautrix+olm stack). Warn once per
                # room so the silence is explained rather than mysterious.
                if ev.get("type") == "m.room.encrypted":
                    if room_id not in self._enc_warned:
                        self._enc_warned.add(room_id)
                        logger.warning("[matrix] room %s is end-to-end encrypted; "
                                       "messages there can't be read (E2EE unsupported)", room_id)
                    continue
                if ev.get("type") != "m.room.message":
                    continue
                content = ev.get("content") or {}
                # Only plain text; ignore m.notice (avoids bot-to-bot loops) and media.
                if content.get("msgtype") != "m.text":
                    continue
                event_id = ev.get("event_id")
                sender = ev.get("sender")
                if not event_id or event_id in self._seen:
                    continue
                self._seen.append(event_id)
                if self._bot_user_id and sender == self._bot_user_id:
                    continue
                body = content.get("body") or ""
                mentioned = self._is_mentioned(content, body)
                is_dm = room_id in self._direct_rooms
                msg = IncomingMessage(
                    platform=self.platform,
                    channel_id=room_id,
                    user_id=sender or "",
                    text=self._strip_mention(body),
                    message_id=event_id,
                    thread_id=None,                       # reply inline (room timeline)
                    was_mentioned=mentioned,
                    is_direct=is_dm,
                    raw=ev,
                )
                await self._dispatch(msg)

    def _is_mentioned(self, content: dict, body: str) -> bool:
        ids = ((content.get("m.mentions") or {}).get("user_ids")) or []
        if self._bot_user_id and self._bot_user_id in ids:
            return True
        if self._bot_user_id and self._bot_user_id in body:
            return True
        if self._display_name and self._display_name in body:
            return True
        return False

    def _strip_mention(self, body: str) -> str:
        if self._display_name:
            body = body.replace(self._display_name + ":", "").replace(self._display_name, "")
        if self._bot_user_id:
            body = body.replace(self._bot_user_id, "")
        return body.strip()

    async def send(self, message: OutgoingMessage) -> None:
        assert self._client is not None
        txn = f"odygw-{uuid.uuid4().hex}"
        url = self._u(f"{_CS}/rooms/{quote(message.channel_id)}/send/m.room.message/{txn}")
        payload = {"msgtype": "m.text", "body": self.format(message.text)}
        r = await self._client.put(url, headers=self._headers, json=payload)
        if r.status_code >= 300:
            logger.warning("[matrix] send failed %s: %s", r.status_code, r.text[:200])

    # ── typing indicator via REST ───────────────────────────────────────
    async def _typing_start(self, msg) -> None:
        self._typing_task = asyncio.create_task(self._typing_loop(msg.channel_id))

    async def _typing_stop(self) -> None:
        if self._typing_task:
            self._typing_task.cancel()
            self._typing_task = None

    async def _typing_loop(self, room_id: str) -> None:
        url = self._u(f"{_CS}/rooms/{quote(room_id)}/typing/{quote(self._bot_user_id or '')}")
        try:
            while True:
                try:
                    await self._client.put(url, headers=self._headers,
                                           json={"typing": True, "timeout": 20000})
                except Exception:
                    pass
                await asyncio.sleep(15)
        except asyncio.CancelledError:
            try:
                await self._client.put(url, headers=self._headers, json={"typing": False})
            except Exception:
                pass
            return

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
