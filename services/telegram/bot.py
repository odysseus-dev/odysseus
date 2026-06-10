"""Telegram bridge for the overseer agent pair (stage 2).

Long-polling bot (raw Bot API over httpx — no extra dependencies) that gives
the supervisor a two-way channel to the user:

  notify(text)                 -> fire-and-forget status message
  ask_confirmation(text, t/o)  -> inline Да/Нет buttons, awaited bool
  ask_question(text, t/o)      -> awaited free-text reply
  pop_user_messages()          -> unsolicited user texts (guidance for the
                                  supervisor of the active task)

Configuration (settings.json): `overseer_telegram_token` is stored
Fernet-encrypted via src.secret_storage — write through set_token().
`overseer_telegram_chat_id` is bound automatically: the first account to
/start the bot claims it; later messages from other chats are ignored.

In-flight confirmations/questions are in-memory futures: a pending ask
cannot survive a process restart anyway (the awaiting coroutine dies with
it), and the command policy treats a vanished answer as "deny".
"""

import asyncio
import json
import logging
import re
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_API = "https://api.telegram.org"
_POLL_TIMEOUT = 50          # Telegram long-poll hold, seconds
_MAX_TEXT = 4000            # Telegram hard limit is 4096


def _md_to_plain(text: str) -> str:
    """Telegram messages are sent without a parse_mode, so raw markdown
    (**bold**, `code`, # headings, [t](u)) would show its literal markers.
    Flatten common markdown to clean plain text. Idempotent on plain text."""
    if not text:
        return text
    t = re.sub(r"```[a-zA-Z0-9_]*\n?", "", text)   # fenced code openers
    t = t.replace("```", "")
    t = re.sub(r"`([^`]+)`", r"\1", t)              # inline code
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)        # bold
    t = re.sub(r"__([^_]+)__", r"\1", t)            # bold (underscore)
    t = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", t)  # italic
    t = re.sub(r"^\s*#{1,6}\s*", "", t, flags=re.M)  # headings
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", t)  # links
    return t


class TelegramBridge:
    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._offset = 0
        self._pending_confirms: dict[str, asyncio.Future] = {}
        self._pending_questions: list[tuple[str, asyncio.Future]] = []
        self._user_messages: list[dict] = []   # unsolicited texts for the overseer
        self._seq = 0
        self._client: Optional[httpx.AsyncClient] = None
        # Telegram may be unreachable from this network for long stretches.
        # That is an expected condition, not an error: log one line on the
        # down transition, one on recovery, stay quiet in between.
        self._down_since: Optional[float] = None

    def _mark_down(self, err: Exception | str):
        if self._down_since is None:
            self._down_since = time.time()
            logger.info(f"Telegram unreachable — going quiet until it recovers ({err})")

    def _mark_up(self):
        if self._down_since is not None:
            mins = int((time.time() - self._down_since) / 60)
            logger.info(f"Telegram reachable again (was down ~{mins} min)")
            self._down_since = None

    @property
    def is_reachable(self) -> bool:
        return self._down_since is None

    # ── configuration ──

    @staticmethod
    def get_token() -> str:
        from src.settings import get_setting
        from src.secret_storage import decrypt
        raw = (get_setting("overseer_telegram_token", "") or "").strip()
        return decrypt(raw) if raw else ""

    @staticmethod
    def set_token(token: str):
        from src.settings import load_settings, save_settings
        from src.secret_storage import encrypt
        s = load_settings()
        s["overseer_telegram_token"] = encrypt(token.strip()) if token.strip() else ""
        save_settings(s)

    @staticmethod
    def get_chat_id() -> str:
        from src.settings import get_setting
        return str(get_setting("overseer_telegram_chat_id", "") or "").strip()

    @staticmethod
    def _set_chat_id(chat_id: str):
        from src.settings import load_settings, save_settings
        s = load_settings()
        s["overseer_telegram_chat_id"] = str(chat_id)
        save_settings(s)

    def is_configured(self) -> bool:
        return bool(self.get_token() and self.get_chat_id())

    # ── lifecycle ──

    async def start(self):
        if self._task and not self._task.done():
            return
        if not self.get_token():
            logger.info("Telegram bridge idle: no token configured")
            return
        # httpx logs full request URLs at INFO — for Bot API calls that
        # includes the secret token. Never let it reach the journal.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        self._client = httpx.AsyncClient(timeout=_POLL_TIMEOUT + 10)
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("Telegram bridge started")

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── outbound API ──

    async def notify(self, text: str) -> bool:
        """Send a status message. Returns False when unconfigured/failed."""
        chat_id = self.get_chat_id()
        if not chat_id:
            return False
        return bool(await self._api("sendMessage", {
            "chat_id": chat_id, "text": text[:_MAX_TEXT],
        }))

    async def ask_confirmation(self, text: str, timeout_s: int = 1800) -> Optional[bool]:
        """Да/Нет inline keyboard. True/False = user's answer; None = the
        question could not be delivered (unconfigured or Telegram
        unreachable) — the caller decides what a missing answer means."""
        chat_id = self.get_chat_id()
        if not chat_id:
            return None
        self._seq += 1
        cid = f"c{int(time.time())}_{self._seq}"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_confirms[cid] = fut
        sent = await self._api("sendMessage", {
            "chat_id": chat_id,
            "text": text[:_MAX_TEXT],
            "reply_markup": json.dumps({"inline_keyboard": [[
                {"text": "✅ Да", "callback_data": f"cfm:{cid}:yes"},
                {"text": "❌ Нет", "callback_data": f"cfm:{cid}:no"},
            ]]}),
        })
        if not sent:
            self._pending_confirms.pop(cid, None)
            return None
        try:
            return bool(await asyncio.wait_for(fut, timeout=timeout_s))
        except asyncio.TimeoutError:
            await self.notify("⏰ Время подтверждения истекло — действие отклонено.")
            return False
        finally:
            self._pending_confirms.pop(cid, None)

    async def ask_question(self, text: str, timeout_s: int = 3600) -> Optional[str]:
        """Ask and await the next free-text reply. None on timeout/unconfigured."""
        chat_id = self.get_chat_id()
        if not chat_id:
            return None
        self._seq += 1
        qid = f"q{int(time.time())}_{self._seq}"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_questions.append((qid, fut))
        sent = await self._api("sendMessage", {
            "chat_id": chat_id, "text": ("❓ " + text)[:_MAX_TEXT],
        })
        if not sent:
            self._pending_questions = [(q, f) for q, f in self._pending_questions if q != qid]
            return None
        try:
            return await asyncio.wait_for(fut, timeout=timeout_s)
        except asyncio.TimeoutError:
            return None
        finally:
            self._pending_questions = [(q, f) for q, f in self._pending_questions if q != qid]

    def pop_user_messages(self) -> list[dict]:
        """Drain unsolicited user texts ({text, ts}) queued for the overseer."""
        out, self._user_messages = self._user_messages, []
        return out

    # ── inbound ──

    async def _poll_loop(self):
        backoff = 2
        while True:
            try:
                token = self.get_token()
                if not token:
                    await asyncio.sleep(30)
                    continue
                resp = await self._client.get(
                    f"{_API}/bot{token}/getUpdates",
                    params={"offset": self._offset, "timeout": _POLL_TIMEOUT,
                            "allowed_updates": '["message","callback_query"]'},
                )
                data = resp.json()
                self._mark_up()
                if not data.get("ok"):
                    logger.warning(f"Telegram getUpdates not ok: {data.get('description')}")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 60)
                    continue
                backoff = 2
                for upd in data.get("result", []):
                    self._offset = max(self._offset, upd["update_id"] + 1)
                    try:
                        await self._handle_update(upd)
                    except Exception as e:
                        logger.warning(f"Telegram update handling failed: {e}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # Expected when Telegram is blocked/unreachable: stay quiet
                # (one transition log line) and keep retrying with backoff.
                self._mark_down(e)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 120)

    async def _handle_update(self, upd: dict):
        cb = upd.get("callback_query")
        if cb:
            await self._handle_callback(cb)
            return
        msg = upd.get("message") or {}
        text = (msg.get("text") or "").strip()
        chat_id = str((msg.get("chat") or {}).get("id") or "")
        if not text or not chat_id:
            return

        bound = self.get_chat_id()
        if not bound:
            # First /start binds the owner chat. Everything else is ignored
            # until binding so a stranger can't claim the bot mid-setup.
            if text.startswith("/start"):
                self._set_chat_id(chat_id)
                await self.notify("🤖 Odysseus overseer подключён. Этот чат привязан для уведомлений и подтверждений.")
            return
        if chat_id != bound:
            logger.warning(f"Telegram message from unbound chat {chat_id} ignored")
            return

        if text.startswith("/start"):
            await self.notify("✅ Уже привязано. Бот на связи.")
            return

        # Free text answers the oldest pending question, otherwise it is
        # guidance for the overseer of the active task.
        if self._pending_questions:
            _qid, fut = self._pending_questions[0]
            if not fut.done():
                fut.set_result(text)
            return
        self._user_messages.append({"text": text, "ts": time.time()})

    async def _handle_callback(self, cb: dict):
        data = cb.get("data") or ""
        chat_id = str(((cb.get("message") or {}).get("chat") or {}).get("id") or "")
        bound = self.get_chat_id()
        token_ok = bound and chat_id == bound
        await self._api("answerCallbackQuery", {"callback_query_id": cb.get("id")})
        if not token_ok or not data.startswith("cfm:"):
            return
        _, cid, answer = data.split(":", 2)
        fut = self._pending_confirms.get(cid)
        if fut and not fut.done():
            fut.set_result(answer == "yes")
        # Strip the buttons and show the outcome on the original message.
        msg = cb.get("message") or {}
        verdict = "✅ Подтверждено" if answer == "yes" else "❌ Отклонено"
        await self._api("editMessageText", {
            "chat_id": chat_id,
            "message_id": msg.get("message_id"),
            "text": ((msg.get("text") or "") + f"\n\n{verdict}")[:_MAX_TEXT],
        })

    async def _api(self, method: str, payload: dict):
        token = self.get_token()
        if not token or self._client is None:
            return None
        # Flatten markdown in any user-facing text (sent without parse_mode).
        if isinstance(payload.get("text"), str):
            payload = {**payload, "text": _md_to_plain(payload["text"])}
        try:
            resp = await self._client.post(f"{_API}/bot{token}/{method}", data=payload)
            data = resp.json()
            self._mark_up()
            if not data.get("ok"):
                # Real API rejection (bad chat id, message too long, ...) —
                # this one deserves a warning, unlike network unavailability.
                logger.warning(f"Telegram {method} failed: {data.get('description')}")
                return None
            return data.get("result")
        except Exception as e:
            self._mark_down(e)
            return None


_bridge: Optional[TelegramBridge] = None


def get_telegram_bridge() -> TelegramBridge:
    global _bridge
    if _bridge is None:
        _bridge = TelegramBridge()
    return _bridge
