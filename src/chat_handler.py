# src/chat_handler.py
"""Handler for chat endpoint operations."""
import os
import asyncio
import logging
from typing import Dict, List, Optional, Any

from fastapi import HTTPException

from src.constants import (
    MAX_CONTEXT_MESSAGES,
    DEFAULT_TEMPERATURE,
    DEFAULT_MAX_TOKENS,
    UPLOAD_DIR,
)
from core.models import ChatMessage
from src.chat_helpers import extract_urls, is_vision_model
from src.document_processor import build_user_content, analyze_image_with_vl_result
from src.youtube_handler import (
    is_youtube_url,
    extract_youtube_id,
    extract_transcript_async,
    format_transcript_for_context,
    fetch_youtube_comments,
    format_comments_for_context,
    YOUTUBE_INSTRUCTION_PROMPT,
)

logger = logging.getLogger(__name__)


class ChatHandler:
    """Handles chat operations for both streaming and non-streaming endpoints."""

    def __init__(
        self,
        session_manager,
        memory_manager,
        chat_processor,
        research_handler,
        preset_manager,
        upload_handler,
    ):
        self.session_manager = session_manager
        self.memory_manager = memory_manager
        self.chat_processor = chat_processor
        self.research_handler = research_handler
        self.preset_manager = preset_manager
        self.upload_handler = upload_handler

    # ------------------------------------------------------------------
    # Preset helpers
    # ------------------------------------------------------------------

    def validate_and_extract_preset(self, preset_id: Optional[str]) -> tuple:
        """Returns (temperature, max_tokens, preset_system_prompt, character_name)."""
        if preset_id and preset_id not in self.preset_manager.presets:
            raise HTTPException(400, f"Invalid preset_id: {preset_id}")

        temperature = DEFAULT_TEMPERATURE
        max_tokens = DEFAULT_MAX_TOKENS
        preset_system_prompt = None
        character_name = ""

        if preset_id and preset_id in self.preset_manager.presets:
            preset = self.preset_manager.presets[preset_id]
            if preset.get("enabled") is False:
                logger.info(f"Preset {preset_id} is disabled, using defaults")
                return temperature, max_tokens, preset_system_prompt, character_name
            if preset.get("system_prompt"):
                preset_system_prompt = preset["system_prompt"]
            character_name = preset.get("character_name", "")
            if character_name:
                name_line = f"Your name is {character_name}."
                if preset_system_prompt:
                    preset_system_prompt = f"{name_line} {preset_system_prompt}"
                else:
                    preset_system_prompt = name_line
            if "temperature" in preset:
                temperature = preset["temperature"]
            if "max_tokens" in preset:
                max_tokens = preset["max_tokens"]

        logger.info(f"Preset {preset_id}: temp={temperature}, max_tokens={max_tokens}")
        return temperature, max_tokens, preset_system_prompt, character_name

    def enhance_message_if_needed(self, message: str) -> str:
        """CoT enhancement disabled — modern models reason natively."""
        return message

    # ------------------------------------------------------------------
    # Preprocessing — shared between /api/chat and /api/chat_stream
    # ------------------------------------------------------------------

    async def preprocess_message(
        self,
        message: str,
        att_ids: List[str],
        sess,
        auto_opened_docs: Optional[List[Dict[str, Any]]] = None,
    ) -> tuple:
        """
        Common preprocessing for both chat endpoints.

        Returns (enhanced_message, user_content, text_for_context, youtube_transcripts, attachment_meta)

        If `auto_opened_docs` is provided, server-side document auto-creation
        (e.g. from an attached fillable PDF) appends entries describing the
        new doc so the caller can announce it to the frontend before streaming.
        """
        enhanced_message = message
        attachment_meta: List[Dict[str, Any]] = []

        # Extract URLs and process YouTube transcripts
        urls = extract_urls(enhanced_message)
        youtube_transcripts: List[str] = []

        has_youtube = False
        for url in urls:
            if is_youtube_url(url):
                video_id = extract_youtube_id(url)
                if not video_id:
                    continue
                has_youtube = True
                logger.info(f"Processing YouTube URL: {url}")
                # Fetch transcript and comments in parallel
                transcript_task = extract_transcript_async(url, video_id)
                comments_task = fetch_youtube_comments(video_id)
                transcript_data, comments_data = await asyncio.gather(
                    transcript_task, comments_task
                )
                # Extract title/channel from comments metadata
                title = comments_data.get("title", "")
                channel = comments_data.get("channel", "")
                youtube_transcripts.append(
                    format_transcript_for_context(transcript_data, url, title, channel)
                )
                comments_ctx = format_comments_for_context(comments_data, url)
                if comments_ctx:
                    youtube_transcripts.append(comments_ctx)

        # Inject instruction prompt so the LLM gives a structured breakdown
        if has_youtube:
            youtube_transcripts.insert(0, YOUTUBE_INSTRUCTION_PROMPT)

        # Analyze images — skip if vision disabled, or if main model is vision-capable
        from src.settings import get_setting
        vision_enabled = get_setting("vision_enabled", True)
        main_is_vision = is_vision_model(sess.model or "")

        # Resolve uploads once with the session owner. Attachment IDs are
        # bearer-like references; never trust them without an owner check.
        files_by_id: Dict[str, Dict] = {}
        owner = getattr(sess, "owner", None)
        if att_ids:
            for att_id in att_ids:
                fi = self.upload_handler.resolve_upload(att_id, owner=owner)
                if fi:
                    files_by_id[att_id] = fi

            for att_id in att_ids:
                fi = files_by_id.get(att_id)
                if fi:
                    attachment_meta.append({
                        "id": fi["id"],
                        "name": fi.get("name") or fi.get("original_name") or fi["id"],
                        "mime": fi.get("mime", ""),
                        "size": fi.get("size", 0),
                        "width": fi.get("width"),
                        "height": fi.get("height"),
                    })

        if att_ids and vision_enabled:
            meta_by_id = {m["id"]: m for m in attachment_meta}
            for att_id in att_ids:
                file_info = files_by_id.get(att_id)
                if file_info and self.upload_handler.is_image_file(
                    file_info["name"], file_info.get("mime", "")
                ):
                    if main_is_vision:
                        # Main model can see images — just note it, image is passed via build_user_content.
                        enhanced_message = f"{enhanced_message}\n\n[Image attached: {file_info['name']}]"
                        _m = meta_by_id.get(att_id)
                        if _m is not None:
                            _m["vision_model"] = sess.model or ""
                        # If the user has hand-edited the OCR/caption via the
                        # chat attachment dropdown, fold it in as an explicit
                        # hint so even vision-capable models respect the
                        # correction (otherwise the model would silently use
                        # whatever it reads from the pixels).
                        _vcache = os.path.join(UPLOAD_DIR, ".vision", att_id + ".txt")
                        if os.path.exists(_vcache):
                            try:
                                with open(_vcache, encoding="utf-8") as _vf:
                                    _vtext = _vf.read().strip()
                                if _vtext:
                                    enhanced_message += f"\n[User-corrected caption / OCR for this image — treat as authoritative]:\n{_vtext}"
                                    _m = meta_by_id.get(att_id)
                                    if _m is not None:
                                        _m["vision"] = _vtext
                            except Exception:
                                pass
                    else:
                        # Main model is text-only — use VL model for description.
                        # Prefer the cached/user-edited text in UPLOAD_DIR/.vision/{id}.txt
                        # so a manual correction (via the chat attachment dropdown's
                        # editable textarea) overrides what the vision model would say.
                        _vcache = os.path.join(UPLOAD_DIR, ".vision", att_id + ".txt")
                        vl_desc = None
                        vl_model = get_setting("vision_model", "") or ""
                        if os.path.exists(_vcache):
                            try:
                                with open(_vcache, encoding="utf-8") as _vf:
                                    cached_desc = _vf.read().strip()
                                if cached_desc and not cached_desc.startswith("["):
                                    vl_desc = cached_desc
                            except Exception:
                                vl_desc = None
                        if not vl_desc:
                            vl_result = analyze_image_with_vl_result(file_info["path"])
                            vl_desc = vl_result.get("text", "")
                            vl_model = vl_result.get("model", "")
                            if vl_desc and not vl_desc.startswith("["):
                                try:
                                    os.makedirs(os.path.join(UPLOAD_DIR, ".vision"), exist_ok=True)
                                    with open(_vcache, "w", encoding="utf-8") as _vf:
                                        _vf.write(vl_desc)
                                except Exception:
                                    pass
                        enhanced_message = f"{enhanced_message}\n\n[Image: {file_info['name']}]\n{vl_desc}"
                        # Surface the description to the client live so it renders as a
                        # collapsible "image description" on the user bubble (not just
                        # after a refresh that re-parses the stored message).
                        _m = meta_by_id.get(att_id)
                        if _m is not None:
                            _m["vision"] = vl_desc
                            _m["vision_model"] = vl_model

        user_content = build_user_content(
            enhanced_message, att_ids, UPLOAD_DIR, self.upload_handler,
            session_id=getattr(sess, "id", None),
            auto_opened_docs=auto_opened_docs,
            owner=owner,
            resolved_uploads=files_by_id,
        )

        # Strip image_url entries for text-only models (VL description is already in the text)
        if not vision_enabled and isinstance(user_content, list):
            text_parts = [
                item.get("text", "") for item in user_content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            user_content = "\n".join(text_parts).strip() if text_parts else enhanced_message
        elif not main_is_vision and isinstance(user_content, list):
            text_parts = [
                item.get("text", "") for item in user_content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            user_content = "\n".join(text_parts).strip() if text_parts else enhanced_message

        # Extract text portion for naming / context
        if isinstance(user_content, list):
            text_for_context = next(
                (item["text"] for item in user_content if item.get("type") == "text"),
                enhanced_message,
            )
        else:
            text_for_context = user_content

        return enhanced_message, user_content, text_for_context, youtube_transcripts, attachment_meta

    # ------------------------------------------------------------------
    # Session helpers
    # ------------------------------------------------------------------

    def update_session_name_if_needed(self, session, message: str):
        if not session.name:
            derived = " ".join(message.split()[:5])
            session.name = "Chat: " + derived if derived else "Chat"

    def trim_history_if_needed(self, session):
        if len(session.history) > MAX_CONTEXT_MESSAGES:
            session.history = session.history[-MAX_CONTEXT_MESSAGES:]

    async def handle_memory_command(self, session, message: str) -> Optional[str]:
        """Process inline memory commands. Returns response string or None."""
        is_memory_cmd, memory_text = self.memory_manager.process_inline_memory_command(
            message
        )
        if is_memory_cmd and memory_text:
            mem = self.memory_manager.load()
            if not self.memory_manager.find_duplicates(memory_text, mem):
                new_entry = self.memory_manager.add_entry(memory_text)
                mem.append(new_entry)
                self.memory_manager.save(mem)

            session.add_message(ChatMessage("user", message))
            session.add_message(
                ChatMessage("assistant", f"Saved to memory: {memory_text}")
            )

            from src.database import update_session_last_accessed

            update_session_last_accessed(session.id)
            self.session_manager.save_sessions()
            return f"Saved to memory: {memory_text}"
        return None
