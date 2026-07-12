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
from src.chat_helpers import extract_urls, model_supports_vision
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


def _sync_upload_vision_to_gallery(file_info: Dict[str, Any], owner: Optional[str], text: str) -> None:
    file_hash = (file_info or {}).get("hash")
    if not file_hash or not text:
        return
    try:
        from core.database import GalleryImage, SessionLocal
        db = SessionLocal()
        try:
            q = db.query(GalleryImage).filter(
                GalleryImage.file_hash == file_hash,
                GalleryImage.is_active == True,  # noqa: E712
            )
            if owner:
                q = q.filter(GalleryImage.owner == owner)
            img = q.first()
            if not img:
                return
            img.caption = text.strip()
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    except Exception as e:
        logger.warning("Failed to sync upload vision text to gallery: %s", e)


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
        allow_tool_preprocessing: bool = True,
    ) -> tuple:
        """
        Common preprocessing for both chat endpoints.

        Returns (enhanced_message, user_content, text_for_context, youtube_transcripts, attachment_meta)

        If `auto_opened_docs` is provided, server-side document auto-creation
        (e.g. from an attached fillable PDF) appends entries describing the
        new doc so the caller can announce it to the frontend before streaming.
        """
        # ── Slash commands ────────────────────────────────────────────────
        # Handle slash commands before normal preprocessing
        if message.strip().startswith("/compact"):
            return await self._handle_compact_command(sess), "", "", [], []
        if message.strip().startswith("/goal"):
            goal_text = message.strip()[5:].strip()
            return await self._handle_goal_command(sess, goal_text), "", "", [], []
        if message.strip().startswith("/dream"):
            return await self._handle_dream_command(sess), "", "", [], []
        if message.strip().startswith("/status"):
            return await self._handle_status_command(sess), "", "", [], []
        if message.strip().startswith("/task"):
            task_args = message.strip()[5:].strip()
            return await self._handle_task_command(sess, task_args), "", "", [], []

        enhanced_message = message
        attachment_meta: List[Dict[str, Any]] = []

        # Extract URLs and process YouTube transcripts
        urls = extract_urls(enhanced_message) if allow_tool_preprocessing else []
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

        # Resolve uploads once with the session owner. Attachment IDs are
        # bearer-like references; never trust them without an owner check.
        files_by_id: Dict[str, Dict] = {}
        owner = getattr(sess, "owner", None)
        effective_att_ids = att_ids if allow_tool_preprocessing else []
        if effective_att_ids:
            for att_id in effective_att_ids:
                fi = self.upload_handler.resolve_upload(att_id, owner=owner)
                if fi:
                    files_by_id[att_id] = fi

            for att_id in effective_att_ids:
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

        # Analyze images only when attachment preprocessing is actually
        # allowed. The vision capability check can probe local model endpoints,
        # so guide-only/no-tools turns must not reach it.
        vision_enabled = False
        main_is_vision = False
        if effective_att_ids:
            from src.settings import get_setting
            vision_enabled = get_setting("vision_enabled", True)
            if vision_enabled:
                main_is_vision = await asyncio.to_thread(
                    model_supports_vision,
                    sess.model or "",
                    getattr(sess, "endpoint_url", "") or "",
                )

        if effective_att_ids and vision_enabled:
            meta_by_id = {m["id"]: m for m in attachment_meta}
            for att_id in effective_att_ids:
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
                                    _sync_upload_vision_to_gallery(file_info, owner, _vtext)
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
                                    _sync_upload_vision_to_gallery(file_info, owner, vl_desc)
                            except Exception:
                                vl_desc = None
                        if not vl_desc:
                            vl_result = analyze_image_with_vl_result(file_info["path"], owner=owner)
                            vl_desc = vl_result.get("text", "")
                            vl_model = vl_result.get("model", "")
                            if vl_desc and not vl_desc.startswith("["):
                                try:
                                    os.makedirs(os.path.join(UPLOAD_DIR, ".vision"), exist_ok=True)
                                    with open(_vcache, "w", encoding="utf-8") as _vf:
                                        _vf.write(vl_desc)
                                    _sync_upload_vision_to_gallery(file_info, owner, vl_desc)
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
            enhanced_message, effective_att_ids, UPLOAD_DIR, self.upload_handler,
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

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------

    async def _handle_compact_command(self, sess) -> str:
        """Handle /compact — trigger context compaction and checkpoint."""
        try:
            import os
            from src.agent.checkpoint_writer import CheckpointWriter
            from src.agent_loop import estimate_tokens

            session_id = getattr(sess, "id", "")
            data_dir = os.environ.get("APP_DATA_DIR", "/app/data")
            base_dir = os.path.join(data_dir, "memory", session_id)
            writer = CheckpointWriter(base_dir)

            # Extract recent context for checkpoint
            messages = getattr(sess, "history", [])
            recent_user = ""
            for msg in reversed(messages):
                if isinstance(msg, dict) and msg.get("role") == "user":
                    recent_user = (msg.get("content") or "")[:500]
                    break
                elif hasattr(msg, "role") and msg.role == "user":
                    recent_user = (getattr(msg, "content", "") or "")[:500]
                    break

            tokens = estimate_tokens(messages) if messages else 0

            writer.write_checkpoint(
                active_intent=recent_user or "Manual compact requested",
                next_action="Continue from compacted context",
                current_work=f"Manual compact at {tokens} tokens",
            )

            # Hard trim: keep last 4 messages
            if hasattr(sess, "history") and len(sess.history) > 4:
                sess.history = sess.history[-4:]

            return (
                f"✅ Context compacted. Checkpoint saved to `{base_dir}/checkpoint.md`.\n"
                f"Previous context: ~{tokens} tokens. Recent 4 messages retained."
            )
        except Exception as e:
            return f"❌ Compact failed: {e}"

    async def _handle_goal_command(self, sess, goal_text: str) -> str:
        """Handle /goal <condition> — set a stopping condition for the session."""
        from src.user_time import get_user_language
        lang = get_user_language() or "en"

        # Load translations
        try:
            import importlib
            locale_mod = importlib.import_module(f"static.locales.{lang}")
            t = getattr(locale_mod, "default", {})
        except Exception:
            t = {}

        if not goal_text:
            # Show current goal if set
            current_goal = getattr(sess, "extra_data", {}).get("goal") if hasattr(sess, "extra_data") else None
            if current_goal:
                return (
                    f"## {t.get('slash.goal.help', 'Current Goal')}\n\n"
                    f"**Condition:** {current_goal}\n\n"
                    f"{t.get('slash.goal.noGoal', 'Use /goal <new condition> to change it.')}"
                )
            return (
                f"## {t.get('slash.goal.help', 'Goal Mode')}\n\n"
                f"{t.get('slash.goal.usage', 'Usage: /goal <stopping condition>')}\n\n"
                f"{t.get('slash.goal.examples', 'Examples:\\n• /goal All tests pass\\n• /goal Bug is fixed')}"
            )

        # Store goal in session extra_data
        if not hasattr(sess, "extra_data") or sess.extra_data is None:
            sess.extra_data = {}
        sess.extra_data["goal"] = goal_text
        sess.extra_data["goal_set_at"] = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()

        # Persist to database
        if self.session_manager and hasattr(sess, "id"):
            try:
                self.session_manager.update_session_metadata(sess.id, sess.extra_data)
            except Exception as e:
                logger.warning(f"Failed to persist goal to database: {e}")

        return (
            f"## {t.get('slash.goal.set', 'Goal Set').replace('{goal}', goal_text)}\n\n"
            f"**Condition:** {goal_text}\n\n"
            f"{t.get('slash.goal.noGoal', 'The agent will not stop until this goal is satisfied.')}"
        )

    async def _handle_dream_command(self, sess) -> str:
        """Handle /dream — scan session traces, extract knowledge to MEMORY.md."""
        try:
            import os
            from src.agent.memory_persist import MemoryStore, NotesStore

            session_id = getattr(sess, "id", "")
            data_dir = os.environ.get("APP_DATA_DIR", "/app/data")
            base_dir = os.path.join(data_dir, "memory", session_id)
            memory = MemoryStore(base_dir)
            notes = NotesStore(base_dir)

            # Read current state
            current_memory = memory.read()
            current_notes = notes.read()

            # Extract knowledge from session history
            messages = getattr(sess, "history", [])
            if not messages:
                return "📭 No session history to analyze."

            # Collect key patterns from conversation
            user_msgs = []
            assistant_msgs = []
            tool_results = []
            for msg in messages:
                if isinstance(msg, dict):
                    role = msg.get("role", "")
                    content = msg.get("content", "")
                elif hasattr(msg, "role"):
                    role = msg.role
                    content = getattr(msg, "content", "")
                else:
                    continue

                if role == "user" and content:
                    user_msgs.append(content[:200])
                elif role == "assistant" and content:
                    assistant_msgs.append(content[:200])
                elif role == "tool" and content:
                    tool_results.append(content[:100])

            # Extract durable knowledge patterns
            knowledge_items = []

            # Pattern 1: User preferences (from repeated requests)
            if len(user_msgs) > 3:
                # Simple heuristic: look for repeated action verbs
                actions = {}
                for msg in user_msgs:
                    words = msg.lower().split()
                    if words:
                        actions[words[0]] = actions.get(words[0], 0) + 1
                common = [w for w, c in actions.items() if c >= 2 and len(w) > 3]
                if common:
                    knowledge_items.append(f"User frequently requests: {', '.join(common[:5])}")

            # Pattern 2: Tools used successfully
            tools_used = set()
            for result in tool_results:
                if "exit_code\": 0" in result or "success" in result.lower():
                    # Extract tool name from result
                    pass
            if tools_used:
                knowledge_items.append(f"Tools successfully used: {', '.join(tools_used)}")

            # Pattern 3: Topics discussed
            topics = set()
            for msg in user_msgs:
                # Simple keyword extraction
                words = msg.lower().split()
                for w in words:
                    if len(w) > 5 and w not in ("about", "these", "those", "their", "there", "what", "when", "where", "which", "while", "would", "could", "should"):
                        topics.add(w)
            if topics:
                knowledge_items.append(f"Topics discussed: {', '.join(list(topics)[:10])}")

            # Update MEMORY.md with extracted knowledge
            if knowledge_items:
                new_section = "\n\n## Discovered from session\n" + "\n".join(f"- {item}" for item in knowledge_items)

                # Check if section already exists
                if "## Discovered from session" in current_memory:
                    # Replace existing section
                    import re
                    current_memory = re.sub(
                        r"\n## Discovered from session\n.*?(?=\n## |\Z)",
                        new_section,
                        current_memory,
                        flags=re.DOTALL,
                    )
                else:
                    current_memory += new_section

                memory.write(current_memory)

            # Clear processed notes
            if current_notes.strip():
                notes.write("# Session notes\n_Cleared by /dream command._\n")

            # Build response
            lines = ["## ✅ Dream Complete\n"]
            lines.append(f"**Session analyzed:** {len(messages)} messages")
            lines.append(f"**Knowledge extracted:** {len(knowledge_items)} items")

            if knowledge_items:
                lines.append("\n### Extracted Knowledge")
                for item in knowledge_items:
                    lines.append(f"- {item}")

            lines.append(f"\n**Memory updated:** `{base_dir}/MEMORY.md`")
            if current_notes.strip():
                lines.append(f"**Notes cleared:** `{base_dir}/notes.md`")

            return "\n".join(lines)

        except Exception as e:
            return f"❌ Dream failed: {e}"

    async def _handle_status_command(self, sess) -> str:
        """Handle /status — show session status, goal, context size, actors."""
        try:
            import os
            from src.agent.memory_persist import CheckpointStore, MemoryStore, NotesStore
            from src.agent.actor import ActorRegistry
            from src.agent_loop import estimate_tokens

            session_id = getattr(sess, "id", "")
            data_dir = os.environ.get("APP_DATA_DIR", "/app/data")
            base_dir = os.path.join(data_dir, "memory", session_id)

            lines = ["## 📊 Session Status\n"]

            # Session info
            lines.append(f"**Session ID:** `{session_id[:16]}...`")
            lines.append(f"**Model:** {getattr(sess, 'model', 'unknown')}")
            lines.append(f"**Messages:** {len(getattr(sess, 'history', []))}")

            # Context size
            messages = getattr(sess, "history", [])
            if messages:
                tokens = estimate_tokens(messages)
                lines.append(f"**Context size:** ~{tokens} tokens")

            # Goal
            metadata = getattr(sess, "metadata", {}) or {}
            goal = metadata.get("goal")
            if goal:
                lines.append(f"\n### 🎯 Active Goal")
                lines.append(f"**Condition:** {goal}")
                set_at = metadata.get("goal_set_at", "unknown")
                lines.append(f"**Set at:** {set_at}")
            else:
                lines.append(f"\n### 🎯 Goal")
                lines.append("_No goal set. Use `/goal <condition>` to set one._")

            # Checkpoint status
            try:
                cs = CheckpointStore(base_dir)
                checkpoint_content = cs.read()
                if checkpoint_content.strip():
                    lines.append(f"\n### 💾 Checkpoint")
                    lines.append(f"**Active intent:** {cs.get_section('active_intent')[:100] or 'empty'}")
                    lines.append(f"**Next action:** {cs.get_section('next_action')[:100] or 'empty'}")
                else:
                    lines.append(f"\n### 💾 Checkpoint")
                    lines.append("_No checkpoint saved yet._")
            except Exception:
                pass

            # Memory status
            try:
                ms = MemoryStore(base_dir)
                memory_content = ms.read()
                if memory_content.strip():
                    lines.append(f"\n### 🧠 Memory")
                    # Count sections
                    sections = memory_content.count("## ")
                    lines.append(f"**Sections:** {sections}")
                    lines.append(f"**Size:** {len(memory_content)} chars")
            except Exception:
                pass

            # Notes status
            try:
                ns = NotesStore(base_dir)
                notes_content = ns.read()
                if notes_content.strip():
                    lines.append(f"\n### 📝 Notes")
                    entries = notes_content.count("## [turn")
                    lines.append(f"**Entries:** {entries}")
            except Exception:
                pass

            # Active actors
            try:
                registry = ActorRegistry.get_instance()
                active = registry.list_active()
                if active:
                    lines.append(f"\n### 🤖 Active Actors")
                    for a in active:
                        status = a.status.value
                        outcome = f" ({a.outcome.value})" if a.outcome else ""
                        lines.append(f"- `{a.id}` — {a.mode.value}, {status}{outcome}")
            except Exception:
                pass

            return "\n".join(lines)

        except Exception as e:
            return f"❌ Status failed: {e}"

    async def _handle_task_command(self, sess, args: str) -> str:
        """Handle /task — manage tasks with subcommands.

        Usage:
            /task          — list all tasks
            /task add <description> — add a new task
            /task done <id> — mark task as done
            /task status   — show task summary
        """
        try:
            import os
            import json
            from src.agent.memory_persist import TaskProgressStore

            session_id = getattr(sess, "id", "")
            data_dir = os.environ.get("APP_DATA_DIR", "/app/data")
            base_dir = os.path.join(data_dir, "memory", session_id)
            task_store = TaskProgressStore(base_dir)

            # Parse subcommand
            parts = args.split(maxsplit=1) if args else []
            subcmd = parts[0].lower() if parts else "list"
            detail = parts[1] if len(parts) > 1 else ""

            if subcmd == "list" or subcmd == "":
                # List all tasks
                tasks = task_store.list_tasks()
                if not tasks:
                    return (
                        "## 📋 Tasks\n\n"
                        "_No tasks yet._\n\n"
                        "**Usage:**\n"
                        "- `/task add <description>` — add a new task\n"
                        "- `/task done <id>` — mark task as done\n"
                        "- `/task status` — show summary"
                    )

                lines = ["## 📋 Tasks\n"]
                for task_id in sorted(tasks):
                    progress = task_store.read_progress(task_id)
                    # Extract status from progress
                    status = "⏳ in progress"
                    if "Status: completed" in progress:
                        status = "✅ completed"
                    elif "Status: failed" in progress:
                        status = "❌ failed"

                    # Extract first line as summary
                    summary = ""
                    for line in progress.split("\n"):
                        if line.startswith("Task:"):
                            summary = line[5:].strip()[:80]
                            break
                    if not summary:
                        summary = progress.split("\n")[0][:80] if progress else "(no details)"

                    lines.append(f"- `{task_id}` {status} — {summary}")

                lines.append(f"\n**Total:** {len(tasks)} tasks")
                return "\n".join(lines)

            elif subcmd == "add":
                if not detail:
                    return "❌ Usage: `/task add <description>`"

                # Generate task ID
                existing = task_store.list_tasks()
                task_num = len(existing) + 1
                task_id = f"T{task_num}"

                # Write task
                task_store.write_progress(
                    task_id,
                    f"Status: in progress\nTask: {detail}\nCreated: {__import__('datetime').datetime.now(__import__('datetime').timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
                )

                return (
                    f"## ✅ Task Created\n\n"
                    f"**ID:** `{task_id}`\n"
                    f"**Description:** {detail}\n"
                    f"**Status:** in progress\n\n"
                    f"Use `/task done {task_id}` when complete."
                )

            elif subcmd == "done":
                if not detail:
                    return "❌ Usage: `/task done <id>`"

                task_id = detail.strip()
                progress = task_store.read_progress(task_id)
                if not progress:
                    return f"❌ Task `{task_id}` not found."

                # Mark as completed
                progress = progress.replace("Status: in progress", "Status: completed")
                if "Status:" not in progress:
                    progress = f"Status: completed\n{progress}"
                task_store.write_progress(task_id, progress)

                return f"## ✅ Task `{task_id}` marked as completed"

            elif subcmd == "status":
                tasks = task_store.list_tasks()
                if not tasks:
                    return "## 📊 Task Status\n\n_No tasks yet._"

                completed = 0
                in_progress = 0
                failed = 0
                for task_id in tasks:
                    progress = task_store.read_progress(task_id)
                    if "Status: completed" in progress:
                        completed += 1
                    elif "Status: failed" in progress:
                        failed += 1
                    else:
                        in_progress += 1

                total = len(tasks)
                pct = round(completed / total * 100) if total else 0

                lines = [
                    "## 📊 Task Status\n",
                    f"**Total:** {total}",
                    f"**Completed:** {completed} ✅",
                    f"**In Progress:** {in_progress} ⏳",
                    f"**Failed:** {failed} ❌",
                    f"**Progress:** {pct}%",
                ]

                # Progress bar
                filled = "█" * (pct // 10)
                empty = "░" * (10 - pct // 10)
                lines.append(f"**{filled}{empty}** {pct}%")

                return "\n".join(lines)

            else:
                return (
                    f"❌ Unknown subcommand: `{subcmd}`\n\n"
                    "**Available commands:**\n"
                    "- `/task` — list all tasks\n"
                    "- `/task add <description>` — add a new task\n"
                    "- `/task done <id>` — mark task as done\n"
                    "- `/task status` — show summary"
                )

        except Exception as e:
            return f"❌ Task command failed: {e}"

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
