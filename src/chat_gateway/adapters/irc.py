"""IRC platform adapter (transport only).

Targets a self-hosted ircd (ergo, InspIRCd, etc.). Uses raw asyncio streams —
no external dependency. Realtime is native (persistent TCP connection).

Auth: a nick, optionally a server password (PASS) and/or NickServ IDENTIFY.

Robustness adopted from the Hermes IRC adapter (NousResearch/hermes-agent, MIT):
  - proper IRC line parser (prefix / command / params + trailing),
  - nick-collision (433) handling with an incrementing suffix,
  - byte-aware message splitting that accounts for the PRIVMSG overhead and
    breaks on word boundaries (IRC's hard line limit is 512 bytes incl. CRLF),
  - flood rate-limiting between lines,
  - CRLF/NUL sanitisation of outgoing text (prevents command injection).

The connection is opened inside listen() so the service-layer retry loop
reconnects cleanly after a drop. IRC has no standard typing indicator, so the
typing hooks stay no-ops.
"""

from __future__ import annotations

import asyncio
import logging
import re
import ssl
from typing import List, Optional

from ..base import IncomingMessage, OutgoingMessage, PlatformAdapter

logger = logging.getLogger("chat_gateway")

_IRC_HARD_LIMIT = 512   # bytes, including the trailing CRLF


def _parse_irc_message(raw: str) -> dict:
    """Parse a raw IRC line into {prefix, command, params} (params includes trailing)."""
    prefix = ""
    trailing = None
    if raw.startswith(":"):
        try:
            prefix, raw = raw[1:].split(" ", 1)
        except ValueError:
            return {"prefix": raw[1:], "command": "", "params": []}
    if " :" in raw:
        raw, trailing = raw.split(" :", 1)
    parts = raw.split()
    command = parts[0] if parts else ""
    params = parts[1:]
    if trailing is not None:
        params.append(trailing)
    return {"prefix": prefix, "command": command, "params": params}


def _extract_nick(prefix: str) -> str:
    return prefix.split("!", 1)[0] if "!" in prefix else prefix


def _strip_control(text: str) -> str:
    """Strip CRLF (command-injection vector) and NUL from outgoing content."""
    return text.replace("\r", " ").replace("\n", " ").replace("\x00", "")


class IrcAdapter(PlatformAdapter):
    platform = "irc"

    def __init__(self, config: dict):
        super().__init__(config)
        self._host = config.get("host") or ""
        self._port = int(config.get("port") or (6697 if config.get("tls", True) else 6667))
        self._tls = bool(config.get("tls", True))
        self._verify_tls = config.get("verify_tls", True)
        self._nick = config.get("nick") or "odysseus"
        self._current_nick = self._nick
        self._realname = config.get("realname") or "Odysseus agent"
        self._server_password = config.get("password")
        self._nickserv_password = config.get("nickserv_password")
        self._channels: List[str] = list(config.get("channels") or [])
        self._writer: Optional[asyncio.StreamWriter] = None

    async def connect(self) -> bool:
        if not self._host or not self._nick:
            logger.error("[irc] host and nick are required")
            return False
        self._bot_user_id = self._nick
        return True

    async def _send_raw(self, line: str) -> None:
        if self._writer is None:
            return
        self._writer.write((line + "\r\n").encode("utf-8", "replace"))
        await self._writer.drain()

    async def listen(self) -> None:
        ssl_ctx = None
        if self._tls:
            ssl_ctx = ssl.create_default_context()
            if not self._verify_tls:
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE

        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port, ssl=ssl_ctx), timeout=30
        )
        self._writer = writer
        self._current_nick = self._nick
        try:
            if self._server_password:
                await self._send_raw(f"PASS {self._server_password}")
            await self._send_raw(f"NICK {self._nick}")
            await self._send_raw(f"USER {self._nick} 0 * :{self._realname}")
            logger.info("[irc] registering as %s on %s:%s", self._nick, self._host, self._port)

            while True:
                raw = await reader.readline()
                if not raw:
                    logger.warning("[irc] connection closed by server")
                    return
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line:
                    await self._handle_line(line)
        finally:
            self._writer = None
            try:
                writer.close()
            except Exception:
                pass

    async def _handle_line(self, line: str) -> None:
        if line.startswith("PING"):
            await self._send_raw("PONG " + line.split(" ", 1)[1])
            return

        msg = _parse_irc_message(line)
        command = msg["command"]
        params = msg["params"]

        # RPL_WELCOME — registration done; identify + join.
        if command == "001":
            if params:
                self._current_nick = params[0]
            if self._nickserv_password:
                await self._send_raw(f"PRIVMSG NickServ :IDENTIFY {self._nickserv_password}")
                await asyncio.sleep(2)
            for ch in self._channels:
                await self._send_raw(f"JOIN {ch}")
            logger.info("[irc] connected as %s; joined %s",
                        self._current_nick, ", ".join(self._channels) or "(no channels)")
            return

        # ERR_NICKNAMEINUSE — pick the next free variant and retry.
        if command == "433":
            base = self._nick.rstrip("_0123456789")
            m = re.search(r"_(\d+)$", self._current_nick)
            if m:
                self._current_nick = f"{base}_{int(m.group(1)) + 1}"
            elif self._current_nick == self._nick:
                self._current_nick = self._nick + "_"
            else:
                self._current_nick = self._nick + "_1"
            self._bot_user_id = self._current_nick
            await self._send_raw(f"NICK {self._current_nick}")
            return

        if command == "PRIVMSG" and len(params) >= 2:
            target, text = params[0], params[1]
            sender_nick = _extract_nick(msg["prefix"])
            if not sender_nick or sender_nick == self._current_nick:
                return

            is_direct = (target == self._current_nick)     # addressed straight to the bot
            reply_target = sender_nick if is_direct else target

            mentioned = False
            body = text
            if not is_direct:
                low = text.lower()
                nick_low = self._current_nick.lower()
                if low.startswith(nick_low):
                    mentioned = True
                    body = text[len(self._current_nick):].lstrip(" :,")
                elif nick_low in low.split():
                    mentioned = True

            await self._dispatch(IncomingMessage(
                platform=self.platform,
                channel_id=reply_target,
                user_id=sender_nick,
                text=body.strip(),
                message_id="",
                thread_id=None,
                was_mentioned=mentioned,
                is_direct=is_direct,
                raw={"line": line, "target": target},
            ))

    # ── byte-aware splitting (PRIVMSG overhead + word boundaries) ───────
    def _split_message(self, content: str, target: str) -> List[str]:
        overhead = len(f"PRIVMSG {target} :".encode("utf-8")) + 2   # +CRLF
        limit = max(64, _IRC_HARD_LIMIT - overhead)
        out: List[str] = []
        for para in content.split("\n"):
            para = _strip_control(para).rstrip()
            if not para:
                continue
            while len(para.encode("utf-8")) > limit:
                # largest prefix that fits in `limit` bytes
                lo, hi, best = 1, len(para), 1
                while lo <= hi:
                    mid = (lo + hi) // 2
                    if len(para[:mid].encode("utf-8")) <= limit:
                        best = mid; lo = mid + 1
                    else:
                        hi = mid - 1
                split_at = best
                space = para.rfind(" ", 0, split_at)
                if space > split_at // 3:        # prefer a word boundary
                    split_at = space
                out.append(para[:split_at].rstrip())
                para = para[split_at:].lstrip()
            if para:
                out.append(para)
        return out

    async def send(self, message: OutgoingMessage) -> None:
        for line in self._split_message(message.text, message.channel_id):
            await self._send_raw(f"PRIVMSG {message.channel_id} :{line}")
            await asyncio.sleep(0.3)             # flood protection
