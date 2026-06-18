"""
telegram_chat_handler.py

Telegram-specific chat integration that bridges Telegram messages to the main
chat and agent flows.
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict

from core.database import Session as DBSession, SessionLocal
from core.models import ChatMessage
from routes.chat_helpers import resolve_session_auth, save_assistant_response
from routes.prefs_routes import _load_for_user as load_prefs_for_user
from routes.research_routes import _resolve_research_endpoint
from routes.telegram_helpers import (
    build_telegram_topic_name,
    find_telegram_session_by_topic_name,
    _get_telegram_user_config,
    _save_telegram_user_config,
    format_for_telegram,
    save_telegram_topic_mapping,
    send_telegram_message,
)
from src.action_intents import classify_tool_intent
from src.chat_handler import ChatHandler
from src.chat_helpers import coerce_message_and_session
from src.context_compactor import maybe_compact, trim_for_context
from src.endpoint_resolver import resolve_chat_fallback_candidates, resolve_endpoint
from src.llm_core import stream_llm_with_fallback
from src.model_context import estimate_tokens
from src.user_time import current_datetime_context_message

logger = logging.getLogger(__name__)

TELEGRAM_MAX_MESSAGE_LENGTH = 4096
MAX_RESPONSE_SPLITS = 10
_WEB_HINTS = (
    "weather",
    "forecast",
    "temperature",
    "current",
    "currently",
    "latest",
    "news",
    "today",
    "tonight",
    "tomorrow",
    "this week",
    "stock",
    "price",
    "traffic",
    "score",
    "scores",
    "live",
)


class TelegramChatHandler:
    """Telegram-specific chat handler that bridges to the main chat system."""

    def __init__(self, chat_handler: ChatHandler, session_manager, research_handler=None):
        self.chat_handler = chat_handler
        self.session_manager = session_manager
        self.research_handler = research_handler

    @staticmethod
    def _persist_session_headers(session_id: str, headers: Dict[str, str]) -> None:
        db = SessionLocal()
        try:
            db_session = db.query(DBSession).filter(DBSession.id == session_id).first()
            if not db_session:
                return
            db_session.headers = headers or {}
            db.commit()
        finally:
            db.close()

    @staticmethod
    def _extract_stream_text(chunk: str) -> str:
        if not chunk.startswith("data: ") or chunk.startswith("data: [DONE]"):
            return ""
        try:
            data = json.loads(chunk[6:])
        except json.JSONDecodeError:
            return ""
        if data.get("thinking"):
            return ""
        return str(data.get("delta") or "")

    @staticmethod
    def _extract_command_payload(text: str, command: str) -> str:
        stripped = str(text or "").strip()
        prefix = f"/{command}"
        if not stripped.lower().startswith(prefix):
            return stripped
        remainder = stripped[len(prefix):].strip()
        return remainder

    @staticmethod
    def _get_chat_mode(owner: str) -> str:
        config = _get_telegram_user_config(owner) or {}
        mode = str(config.get("mode") or "chat").strip().lower()
        return mode if mode in {"chat", "agent"} else "chat"

    @staticmethod
    def _set_chat_mode(owner: str, mode: str) -> bool:
        config = _get_telegram_user_config(owner) or {}
        config["mode"] = mode
        return _save_telegram_user_config(owner, config)

    @staticmethod
    def _should_use_web_search(message_text: str) -> bool:
        text = str(message_text or "").strip()
        if not text:
            return False
        lower = text.lower()
        if lower.startswith("/web"):
            return True
        if "http://" in lower or "https://" in lower:
            return True
        intent = classify_tool_intent(text)
        if intent.category == "web":
            return True
        return any(hint in lower for hint in _WEB_HINTS)

    def _get_or_create_telegram_session(self, owner: str, telegram_user_id: int):
        """Get or create a Telegram conversation session for this user."""
        db = SessionLocal()
        try:
            return self._get_or_create_named_session(owner, f"telegram_{telegram_user_id}", db=db)
        except Exception as e:
            logger.error("Error getting/creating Telegram session for %s: %s", owner, e, exc_info=True)
            raise
        finally:
            db.close()

    def _get_or_create_named_session(self, owner: str, session_name: str, *, db=None):
        """Return an owned session by name, creating it with the default endpoint if needed."""
        owns_db = db is None
        db = db or SessionLocal()
        try:
            existing = db.query(DBSession).filter(
                DBSession.owner == owner,
                DBSession.name == session_name,
            ).first()

            if existing:
                logger.debug("Found existing Telegram-backed session %s for user %s", session_name, owner)
                return self.session_manager.get_session(existing.id)

            logger.info("Creating new Telegram-backed session %s for user %s", session_name, owner)
            endpoint_url, model, headers = resolve_endpoint("default", owner=owner)
            if not endpoint_url or not model:
                raise RuntimeError(
                    "No default chat model is configured for this account. "
                    "Set a default model in Settings before using Telegram chat."
                )

            new_session = self.session_manager.create_session(
                session_id=str(uuid.uuid4()),
                name=session_name,
                endpoint_url=endpoint_url,
                model=model,
                owner=owner,
            )
            new_session.headers = headers or {}
            self._persist_session_headers(new_session.id, new_session.headers)
            return new_session
        finally:
            if owns_db:
                db.close()

    def get_or_create_telegram_topic_session(
        self,
        owner: str,
        *,
        forum_chat_id: int,
        topic_id: int,
        topic_name: str | None = None,
        forum_chat_title: str = "",
    ):
        """Return the Odysseus session bound to a Telegram forum topic, creating one if needed."""
        config = _get_telegram_user_config(owner) or {}
        raw_mappings = config.get("topic_mappings", {})
        topic_mappings = raw_mappings if isinstance(raw_mappings, dict) else {}
        for session_id, mapping in topic_mappings.items():
            if isinstance(mapping, dict) and int(mapping.get("topic_id", -1)) == int(topic_id):
                return self.session_manager.get_session(session_id)

        if topic_name:
            match = find_telegram_session_by_topic_name(owner, topic_name)
            if match:
                session_id, session_name = match
                saved = save_telegram_topic_mapping(
                    owner,
                    forum_chat_id=forum_chat_id,
                    topic_id=topic_id,
                    session_id=session_id,
                    session_name=session_name,
                    topic_name=topic_name,
                    forum_chat_title=forum_chat_title,
                )
                if saved:
                    return self.session_manager.get_session(session_id)

        fallback_name = build_telegram_topic_name(topic_name or f"Topic {topic_id}", str(topic_id))
        session = self._get_or_create_named_session(owner, fallback_name)
        save_telegram_topic_mapping(
            owner,
            forum_chat_id=forum_chat_id,
            topic_id=topic_id,
            session_id=session.id,
            session_name=session.name,
            topic_name=topic_name or session.name,
            forum_chat_title=forum_chat_title,
        )
        return session

    def _get_target_session(self, owner: str, telegram_user_id: int, session_id: str | None = None):
        """Return the session bound to this Telegram context."""
        if session_id:
            session = self.session_manager.get_session(session_id)
            if getattr(session, "owner", None) != owner:
                raise RuntimeError(f"Session {session_id} does not belong to {owner}")
            return session
        return self._get_or_create_telegram_session(owner, telegram_user_id)

    async def _build_context(self, session, owner: str, message: str, enhanced_message: str, *, use_web: bool) -> dict:
        resolve_session_auth(session, session.id, owner=owner)
        uprefs = load_prefs_for_user(owner) or {}
        mem_enabled = uprefs.get("memory_enabled", True)
        preface, rag_sources, web_sources = self.chat_handler.chat_processor.build_context_preface(
            message=enhanced_message,
            session=session,
            use_web=use_web,
            use_rag=True,
            use_memory=mem_enabled,
            owner=owner,
            agent_mode=False,
            incognito=False,
            use_skills=False,
        )

        messages = list(preface) + list(session.get_context_messages())
        try:
            messages.append(current_datetime_context_message())
        except Exception:
            logger.debug("Failed to add current date/time context", exc_info=True)
        messages.append({"role": "user", "content": enhanced_message})

        messages, context_length, was_compacted = await maybe_compact(
            session,
            session.endpoint_url,
            session.model,
            messages,
            session.headers,
            owner=owner,
        )
        messages = trim_for_context(messages, context_length)

        return {
            "messages": messages,
            "rag_sources": rag_sources,
            "web_sources": web_sources,
            "used_memories": getattr(self.chat_handler.chat_processor, "_last_used_memories", []),
            "uprefs": uprefs,
            "context_length": context_length,
            "was_compacted": was_compacted,
        }

    async def _handle_mode_command(
        self,
        owner: str,
        chat_id: int,
        text: str,
        message_thread_id: int | None = None,
    ) -> bool:
        current = self._get_chat_mode(owner)
        await send_telegram_message(
            chat_id,
            format_for_telegram(
                "🤖 Telegram mode\n\n"
                f"You are currently in **{current}** mode.\n\n"
                "Use `/setmodechat` to switch to chat mode or `/setmodeagent` to switch to agent mode."
            ),
            message_thread_id=message_thread_id,
        )
        return True

    async def _handle_set_mode_command(
        self,
        owner: str,
        chat_id: int,
        mode: str,
        message_thread_id: int | None = None,
    ) -> bool:
        if mode not in {"chat", "agent"}:
            await send_telegram_message(
                chat_id,
                format_for_telegram("❌ Invalid mode command."),
                message_thread_id=message_thread_id,
            )
            return False

        if not self._set_chat_mode(owner, mode):
            await send_telegram_message(
                chat_id,
                format_for_telegram("❌ Failed to save Telegram mode. Please try again."),
                message_thread_id=message_thread_id,
            )
            return False

        await send_telegram_message(
            chat_id,
            format_for_telegram(
                f"✅ You are now in **{mode}** mode.\n\n"
                + (
                    "Agent mode can use Odysseus tools like web search and deep research when needed."
                    if mode == "agent"
                    else "Chat mode gives direct answers and can use web search context for current-info questions."
                )
            ),
            message_thread_id=message_thread_id,
        )
        return True

    async def _handle_research_command(
        self,
        owner: str,
        session,
        chat_id: int,
        text: str,
        message_thread_id: int | None = None,
    ) -> bool:
        query = self._extract_command_payload(text, "research")
        if not query:
            await send_telegram_message(
                chat_id,
                format_for_telegram("Usage: `/research your topic here`"),
                message_thread_id=message_thread_id,
            )
            return False

        if not self.research_handler:
            await send_telegram_message(
                chat_id,
                format_for_telegram("❌ Deep research is not available right now."),
                message_thread_id=message_thread_id,
            )
            return False

        resolve_session_auth(session, session.id, owner=owner)
        ep_url, ep_model, ep_headers = _resolve_research_endpoint(session, owner=owner)
        if not ep_url or not ep_model:
            await send_telegram_message(
                chat_id,
                format_for_telegram(
                    "❌ No research model is configured for this account. "
                    "Set one in Odysseus Settings first."
                ),
                message_thread_id=message_thread_id,
            )
            return False

        research_query = await self.research_handler.synthesize_query(
            session,
            query,
            ep_url,
            ep_model,
            ep_headers,
        )
        self.session_manager.add_message(session.id, ChatMessage(role="user", content=query))

        async def _send_completion(result_text: str, sources: list | None) -> None:
            suffix = ""
            if sources:
                lines = []
                for idx, src in enumerate(sources[:5], 1):
                    url = str(src.get("url") or "").strip()
                    title = str(src.get("title") or src.get("domain") or url or f"Source {idx}").strip()
                    if url:
                        lines.append(f"{idx}. {title} - {url}")
                    else:
                        lines.append(f"{idx}. {title}")
                if lines:
                    suffix = "\n\nSources:\n" + "\n".join(lines)
            await self._send_response_to_telegram(result_text + suffix, chat_id, message_thread_id=message_thread_id)

        def _on_complete(_sid, result_text, sources, findings):
            metadata = {"research": True, "model": ep_model}
            if sources:
                metadata["research_sources"] = sources
            if findings:
                metadata["research_findings"] = findings
            try:
                self.session_manager.add_message(
                    _sid,
                    ChatMessage(role="assistant", content=result_text, metadata=metadata),
                )
                self.session_manager.save_sessions()
            except Exception:
                logger.exception("Failed to persist Telegram research result for session %s", _sid)
            asyncio.create_task(_send_completion(result_text, sources))

        self.research_handler.start_research(
            session.id,
            research_query,
            ep_url,
            ep_model,
            llm_headers=ep_headers,
            owner=owner,
            on_complete=_on_complete,
        )

        await send_telegram_message(
            chat_id,
            format_for_telegram(
                "🔎 Starting deep research.\n\n"
                "I'll send the finished report here when it's ready."
            ),
            message_thread_id=message_thread_id,
        )
        return True

    async def _run_chat_mode(
        self,
        session,
        owner: str,
        chat_id: int,
        message: str,
        enhanced_message: str,
        *,
        message_thread_id: int | None = None,
    ) -> bool:
        use_web = self._should_use_web_search(message)
        context = await self._build_context(
            session,
            owner,
            message,
            enhanced_message,
            use_web=use_web,
        )

        response_text = ""
        last_metrics = None
        started_at = time.time()
        _requested_model = session.model
        _actual_model = None
        _answered_by = None

        try:
            estimated = estimate_tokens(context["messages"])
            logger.debug("Estimated tokens: %s", estimated)

            candidates = [(session.endpoint_url, session.model, session.headers)] + resolve_chat_fallback_candidates(owner=owner)
            async for chunk in stream_llm_with_fallback(
                candidates,
                context["messages"],
                headers=session.headers,
                temperature=getattr(session, "temperature", 0.7),
                max_tokens=getattr(session, "max_tokens", 0) or 0,
                session_id=session.id,
            ):
                if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
                    try:
                        data = json.loads(chunk[6:])
                    except json.JSONDecodeError:
                        data = {}
                    if "delta" in data and not data.get("thinking"):
                        response_text += data["delta"]
                    elif data.get("type") == "fallback":
                        _answered_by = data.get("answered_by") or _answered_by
                        _actual_model = _actual_model or _answered_by
                    elif data.get("type") == "model_actual":
                        _actual_model = data.get("model") or _actual_model
                    elif data.get("type") in {"usage", "metrics"}:
                        last_metrics = data.get("data", {})
                elif chunk == "data: [DONE]\n\n":
                    break
        except Exception as e:
            logger.error("Error streaming Telegram chat response: %s", e, exc_info=True)
            await send_telegram_message(
                chat_id,
                format_for_telegram("❌ Error communicating with AI model. Please try again."),
                message_thread_id=message_thread_id,
            )
            return False

        if not response_text:
            await send_telegram_message(
                chat_id,
                format_for_telegram("❌ The AI model didn't return a response. Please try again."),
                message_thread_id=message_thread_id,
            )
            return False

        if not last_metrics:
            elapsed = time.time() - started_at
            out_tokens = len(response_text) // 4
            last_metrics = {
                "response_time": round(elapsed, 2),
                "input_tokens": estimate_tokens(context["messages"]),
                "output_tokens": out_tokens,
                "tokens_per_second": round(out_tokens / elapsed, 2) if elapsed > 0 else 0,
                "context_length": context["context_length"],
                "model": _actual_model or _answered_by or _requested_model,
                "requested_model": _requested_model,
                "usage_source": "estimated",
            }

        self.session_manager.add_message(session.id, ChatMessage(role="user", content=message))
        save_assistant_response(
            session,
            self.session_manager,
            session.id,
            response_text,
            last_metrics,
            web_sources=context["web_sources"],
            rag_sources=context["rag_sources"],
            used_memories=context["used_memories"],
        )
        await self._send_response_to_telegram(response_text, chat_id, message_thread_id=message_thread_id)
        return True

    async def _run_agent_mode(
        self,
        session,
        owner: str,
        chat_id: int,
        message: str,
        enhanced_message: str,
        *,
        message_thread_id: int | None = None,
    ) -> bool:
        from src.agent_loop import stream_agent_loop
        from src.settings import get_setting
        from src.agent_tools import MAX_AGENT_ROUNDS as DEFAULT_MAX_AGENT_ROUNDS
        from src.tool_policy import build_effective_tool_policy

        context = await self._build_context(
            session,
            owner,
            message,
            enhanced_message,
            use_web=False,
        )

        response_text = ""
        last_metrics = None
        web_sources = context["web_sources"]
        started_at = time.time()
        requested_model = session.model
        actual_model = None
        answered_by = None

        try:
            max_tool_calls = int(get_setting("agent_max_tool_calls", 0))
            try:
                max_rounds = int(get_setting("agent_max_rounds", DEFAULT_MAX_AGENT_ROUNDS) or DEFAULT_MAX_AGENT_ROUNDS)
            except (TypeError, ValueError):
                max_rounds = DEFAULT_MAX_AGENT_ROUNDS
            max_rounds = max(1, min(max_rounds, 200))
            tool_policy = build_effective_tool_policy(
                disabled_tools={"ask_user", "ui_control"},
                last_user_message=message,
            )

            async for chunk in stream_agent_loop(
                session.endpoint_url,
                session.model,
                context["messages"],
                headers=session.headers,
                temperature=getattr(session, "temperature", 0.7),
                max_tokens=getattr(session, "max_tokens", 0) or 0,
                max_tool_calls=max_tool_calls,
                max_rounds=max_rounds,
                context_length=context["context_length"],
                session_id=session.id,
                disabled_tools=sorted(tool_policy.all_disabled_names()),
                tool_policy=tool_policy,
                owner=owner,
                fallbacks=resolve_chat_fallback_candidates(owner=owner),
            ):
                if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
                    try:
                        data = json.loads(chunk[6:])
                    except json.JSONDecodeError:
                        data = {}
                    if "delta" in data and not data.get("thinking"):
                        response_text += data["delta"]
                    elif data.get("type") == "web_sources":
                        web_sources = data.get("data", [])
                    elif data.get("type") == "fallback":
                        answered_by = data.get("answered_by") or answered_by
                        actual_model = actual_model or answered_by
                    elif data.get("type") == "model_actual":
                        actual_model = data.get("model") or actual_model
                    elif data.get("type") == "metrics":
                        last_metrics = data.get("data", {})
                elif chunk == "data: [DONE]\n\n":
                    break
        except Exception as e:
            logger.error("Error streaming Telegram agent response: %s", e, exc_info=True)
            await send_telegram_message(
                chat_id,
                format_for_telegram("❌ Agent mode failed. Please try again."),
                message_thread_id=message_thread_id,
            )
            return False

        if not response_text:
            await send_telegram_message(
                chat_id,
                format_for_telegram("❌ Agent mode did not return a response. Please try again."),
                message_thread_id=message_thread_id,
            )
            return False

        if not last_metrics:
            elapsed = time.time() - started_at
            out_tokens = len(response_text) // 4
            last_metrics = {
                "response_time": round(elapsed, 2),
                "input_tokens": estimate_tokens(context["messages"]),
                "output_tokens": out_tokens,
                "tokens_per_second": round(out_tokens / elapsed, 2) if elapsed > 0 else 0,
                "context_length": context["context_length"],
                "model": actual_model or answered_by or requested_model,
                "requested_model": requested_model,
                "usage_source": "estimated",
            }

        self.session_manager.add_message(session.id, ChatMessage(role="user", content=message))
        save_assistant_response(
            session,
            self.session_manager,
            session.id,
            response_text,
            last_metrics,
            web_sources=web_sources,
            rag_sources=context["rag_sources"],
            used_memories=context["used_memories"],
        )
        await self._send_response_to_telegram(response_text, chat_id, message_thread_id=message_thread_id)
        return True

    async def handle_telegram_message(
        self,
        message_text: str,
        owner: str,
        telegram_user_id: int,
        chat_id: int,
        session_id: str | None = None,
        message_thread_id: int | None = None,
    ) -> bool:
        """Process a Telegram message and send response(s) back."""
        try:
            session = self._get_target_session(owner, telegram_user_id, session_id=session_id)

            if message_text.strip().lower().startswith("/setmodeagent"):
                return await self._handle_set_mode_command(owner, chat_id, "agent", message_thread_id)

            if message_text.strip().lower().startswith("/setmodechat"):
                return await self._handle_set_mode_command(owner, chat_id, "chat", message_thread_id)

            if message_text.strip().lower().startswith("/mode"):
                return await self._handle_mode_command(owner, chat_id, message_text, message_thread_id)

            if message_text.strip().lower().startswith("/research"):
                return await self._handle_research_command(
                    owner,
                    session,
                    chat_id,
                    message_text,
                    message_thread_id,
                )

            message = self._extract_command_payload(message_text, "web") if message_text.strip().lower().startswith("/web") else message_text

            try:
                message, session_id = coerce_message_and_session(
                    {"message": message, "session": session.id},
                    None,
                    None,
                    self.session_manager,
                )
                session = self.session_manager.get_session(session_id)
            except Exception as e:
                logger.error("Error coercing message/session: %s", e, exc_info=True)
                await send_telegram_message(
                    chat_id,
                    format_for_telegram("❌ Error: Could not process your message. Please try again."),
                    message_thread_id=message_thread_id,
                )
                return False

            try:
                enhanced_msg, _, _, _, _ = await self.chat_handler.preprocess_message(
                    message,
                    att_ids=[],
                    sess=session,
                    allow_tool_preprocessing=True,
                )
            except Exception as e:
                logger.error("Error preprocessing message: %s", e, exc_info=True)
                await send_telegram_message(
                    chat_id,
                    format_for_telegram("❌ Error: Could not process your message. Please try again."),
                    message_thread_id=message_thread_id,
                )
                return False

            mode = self._get_chat_mode(owner)
            if mode == "agent":
                return await self._run_agent_mode(
                    session,
                    owner,
                    chat_id,
                    message,
                    enhanced_msg,
                    message_thread_id=message_thread_id,
                )
            return await self._run_chat_mode(
                session,
                owner,
                chat_id,
                message,
                enhanced_msg,
                message_thread_id=message_thread_id,
            )

        except Exception as e:
            logger.error(
                "Error handling Telegram message for user %s: %s",
                owner, e, exc_info=True
            )
            try:
                await send_telegram_message(
                    chat_id,
                    format_for_telegram("❌ An unexpected error occurred. Please try again later."),
                    message_thread_id=message_thread_id,
                )
            except Exception as send_err:
                logger.error("Error sending error message: %s", send_err)
            return False

    async def _send_response_to_telegram(
        self,
        response_text: str,
        chat_id: int,
        *,
        message_thread_id: int | None = None,
    ) -> bool:
        """Split response into Telegram messages and send them."""
        try:
            if not response_text:
                logger.warning("Empty response to send")
                return False

            messages = self._split_response(response_text, TELEGRAM_MAX_MESSAGE_LENGTH)

            if len(messages) > MAX_RESPONSE_SPLITS:
                logger.warning("Response too long (%d messages), truncating", len(messages))
                messages = messages[:MAX_RESPONSE_SPLITS]
                if messages:
                    messages[-1] += "\n\n...(response truncated)"

            success = True
            for i, msg in enumerate(messages):
                formatted_msg = format_for_telegram(msg)
                if not await send_telegram_message(chat_id, formatted_msg, message_thread_id=message_thread_id):
                    logger.error("Failed to send Telegram message chunk %d/%d", i + 1, len(messages))
                    success = False
                if i < len(messages) - 1:
                    await asyncio.sleep(0.5)

            return success
        except Exception as e:
            logger.error("Error sending response to Telegram: %s", e, exc_info=True)
            return False

    @staticmethod
    def _split_response(text: str, max_length: int = TELEGRAM_MAX_MESSAGE_LENGTH) -> list:
        """Split text into Telegram-friendly chunks."""
        if len(text) <= max_length:
            return [text]

        messages = []
        current = ""

        paragraphs = text.split("\n\n")
        for para in paragraphs:
            if len(current) + len(para) + 2 <= max_length:
                current += para + "\n\n"
            else:
                if current:
                    messages.append(current.rstrip())
                if len(para) > max_length:
                    lines = para.split("\n")
                    for line in lines:
                        if len(current) + len(line) + 1 <= max_length:
                            current += line + "\n"
                        else:
                            if current:
                                messages.append(current.rstrip())
                            current = line + "\n"
                else:
                    current = para + "\n\n"

        if current:
            messages.append(current.rstrip())

        result = []
        for msg in messages:
            if len(msg) > max_length:
                for i in range(0, len(msg), max_length):
                    result.append(msg[i:i + max_length])
            else:
                result.append(msg)

        return result or [""]
