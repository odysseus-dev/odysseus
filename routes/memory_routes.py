# routes/memory_routes.py
from fastapi import APIRouter, Form, HTTPException, Request, UploadFile, File
from typing import Dict, Any, Optional, List
import json
import os
import re
import tempfile
import time
from datetime import datetime
import logging

# Leading list-marker like "1.", "12)", or "3:" plus surrounding whitespace.
# Strips one prefix per call so import-from-LLM-output doesn't leave the
# numbering inside the saved memory text. Bullet markers (-, *, •) are
# also peeled here for the same reason.
_LIST_PREFIX_RE = re.compile(r"^\s*(?:\d{1,3}[.):]\s+|[-*•]\s+)")


def _strip_list_prefix(text: str) -> str:
    if not text:
        return text
    return _LIST_PREFIX_RE.sub("", text, count=1).strip()

from services.memory import MemoryManager
from core.session_manager import SessionManager
from src.request_models import MemoryAddRequest
from core.database import SessionLocal
from src.llm_core import llm_call_async
from services.memory.memory_extractor import audit_memories
from src.auth_helpers import get_current_user, require_user
from src.endpoint_resolver import resolve_endpoint
from src.upload_limits import read_upload_limited

logger = logging.getLogger(__name__)

MEMORY_IMPORT_MAX_BYTES = int(os.getenv("ODYSSEUS_MEMORY_IMPORT_MAX_BYTES", str(10 * 1024 * 1024)))

def setup_memory_routes(memory_manager: MemoryManager, session_manager: SessionManager, memory_vector=None):
    """Set up memory-related routes."""
    router = APIRouter(prefix="/api/memory", tags=["memory"])

    def _owner(request: Request) -> Optional[str]:
        return get_current_user(request)

    def _assert_session_owner(session_obj, user):
        """SECURITY: 404 if the caller does not own this session.

        SessionManager.get_session is NOT owner-scoped — it returns any
        session by id. These routes accept a caller-supplied session id, so
        without this gate a user could target another tenant's session and
        leak their chat history, their session-scoped LLM credentials, or the
        session title. Mirrors session_routes / webhook_routes ownership.
        """
        if user is not None and getattr(session_obj, "owner", None) != user:
            raise HTTPException(404, "Session not found")

    def _verify_memory_owner(memory: dict, user: Optional[str]):
        """Raise 404 if user doesn't own this memory.

        SECURITY: strict ownership — previously `mem_owner and mem_owner != user`
        allowed any user to read/edit/delete memories with an empty/null owner
        field, which leaked legacy data across the multi-user deploy.
        """
        if user is None:
            return  # Auth disabled
        if memory.get("owner") != user:
            raise HTTPException(404, "Memory not found")

    @router.post("/debug")
    def debug_memory_relevance(request: Request, query: str = Form(...)):
        """Debug which memories would be triggered for a query"""
        user = _owner(request)
        memories = memory_manager.load(owner=user)
        relevant = memory_manager.get_relevant_memories(query, memories, threshold=0.05)

        return {
            "query": query,
            "total_memories": len(memories),
            "relevant_count": len(relevant),
            "relevant_memories": [{"text": m["text"], "category": m.get("category", "unknown")}
                                 for m in relevant]
        }

    @router.post("/add", response_model=Dict[str, Any])
    async def api_add_memory(
        request: Request,
        memory_data: Optional[MemoryAddRequest] = None
    ):
        """Add a new memory entry with optional category, source, and session reference."""
        from src.auth_helpers import require_privilege
        require_privilege(request, "can_manage_memory")
        if memory_data is None:
            form = await request.form()
            memory_data = MemoryAddRequest(
                text=form.get("text"),
                category=form.get("category", "fact"),
                source=form.get("source", "user"),
                session_id=form.get("session_id")
            )

        user = _owner(request)
        text = (memory_data.text or "").strip()
        if not text:
            raise HTTPException(400, "empty memory")
        user_mem = memory_manager.load(owner=user)
        if memory_manager.find_duplicates(text, user_mem):
            return {"ok": True, "count": len(user_mem), "message": "Memory already exists"}

        new_entry = memory_manager.add_entry(text, memory_data.source, memory_data.category, owner=user)
        if memory_data.session_id:
            new_entry["session_id"] = memory_data.session_id
        all_mem = memory_manager.load_all()
        all_mem.append(new_entry)
        memory_manager.save(all_mem)
        # Sync vector index
        if memory_vector and memory_vector.healthy:
            memory_vector.add(new_entry["id"], text)
        try:
            from src.event_bus import fire_event
            fire_event("memory_added", user)
        except Exception:
            logger.debug("memory_added event dispatch failed", exc_info=True)
        return {"ok": True, "count": len([m for m in all_mem if m.get("owner") == user])}

    @router.get("")
    def api_get_memory(request: Request):
        """Return all memory entries with their metadata."""
        user = _owner(request)
        return {"memory": memory_manager.load(owner=user)}

    @router.post("/search")
    def search_memories(request: Request, query: str = Form(...), session_id: str = Form(None), category: str = Form(None)):
        """Search across all memories with optional filters."""
        user = _owner(request)
        memories = memory_manager.load(owner=user)

        if session_id:
            memories = [m for m in memories if m.get("session_id") == session_id]

        if category:
            memories = [m for m in memories if category in m.get("categories", [m.get("category", "")])]

        relevant = memory_manager.get_relevant_memories(query, memories, threshold=0.05, max_items=20)

        return {"memories": relevant, "total": len(relevant), "query": query}

    @router.get("/timeline")
    def memory_timeline(request: Request):
        """Get memories in chronological order with source session information."""
        user = _owner(request)
        memories = memory_manager.load(owner=user)
        sorted_memories = sorted(memories, key=lambda x: x.get("timestamp", 0), reverse=True)

        results = []
        for memory in sorted_memories:
            if "timestamp" in memory:
                try:
                    dt = datetime.fromtimestamp(memory["timestamp"])
                    memory["timestamp_str"] = dt.strftime("%Y-%m-%d %H:%M:%S")
                except (ValueError, OSError, OverflowError):
                    memory["timestamp_str"] = "Unknown"
            else:
                memory["timestamp_str"] = "Unknown"

            session_id = memory.get("session_id")
            if session_id and session_id in session_manager.sessions:
                session = session_manager.get_session(session_id)
                memory["session_name"] = session.name if session else f"Session {session_id[:6]}"
            else:
                memory["session_name"] = "Unknown"

            results.append(memory)

        return {"timeline": results, "total": len(results)}

    @router.get("/by-session/{session_id}")
    def get_memory_by_session(request: Request, session_id: str):
        """Get all memories associated with a specific session."""
        user = _owner(request)
        try:
            _session_obj = session_manager.get_session(session_id)
        except KeyError:
            raise HTTPException(404, f"Session {session_id} not found")
        _assert_session_owner(_session_obj, user)
        memories = memory_manager.load(owner=user)
        session_memories = [m for m in memories if m.get("session_id") == session_id]

        session_memories.sort(key=lambda x: x.get("timestamp", 0), reverse=True)

        try:
            session = session_manager.get_session(session_id)
            session_name = session.name if session else f"Session {session_id[:6]}"
        except KeyError:
            session_name = f"Session {session_id[:6]}"

        for memory in session_memories:
            memory["session_name"] = session_name

        return {
            "session_id": session_id,
            "session_name": session_name,
            "memory_count": len(session_memories),
            "memories": session_memories
        }

    @router.post("/extract")
    async def extract_memory(request: Request, session: str = Form(...)) -> Dict[str, List[str]]:
        """Analyze a session's chat history and return memory suggestions."""
        require_user(request)
        try:
            sess = session_manager.get_session(session)
        except KeyError:
            raise HTTPException(404, "Session not found")
        _assert_session_owner(sess, _owner(request))

        system_msg = {
            "role": "system",
            "content": (
                "You are a helpful assistant. Analyze the entire conversation history provided and extract any "
                "useful factual statements, contacts, addresses, phone numbers, or other information that the user "
                "might want to remember for future interactions. Return each piece of information as a JSON object "
                "with a 'text' field. For example: [{'text': 'Alice lives at 123 Main St'}, {'text': 'Bob works at Acme Corp'}]. "
                "Only include information that is specific and likely to be useful later."
            ),
        }
        messages = [system_msg] + sess.get_context_messages()

        try:
            suggestion_text = await llm_call_async(
                sess.endpoint_url,
                sess.model,
                messages,
                temperature=0.2,
                max_tokens=500,
                headers=sess.headers,
            )
            try:
                suggestions = json.loads(suggestion_text)
                if isinstance(suggestions, list):
                    suggestions = [s if isinstance(s, str) else s.get("text", "") for s in suggestions]
                else:
                    suggestions = []
            except json.JSONDecodeError:
                suggestions = [line.strip() for line in suggestion_text.splitlines() if line.strip()]

            return {"suggestions": [s for s in suggestions if s]}
        except Exception as e:
            logger.error(f"LLM memory extraction failed (session {session}): {e}")
            fallback = memory_manager.extract_memory_from_chat(sess.history, session)
            return {"suggestions": [item["text"] for item in fallback]}

    @router.post("/audit")
    async def api_audit_memories(request: Request, session: str = Form(None)):
        """Deduplicate and consolidate memories via LLM.

        Uses the default model from settings, or falls back to a session's model.
        Returns before and after memory counts.
        """
        from routes.model_routes import _load_settings, _normalize_base, build_chat_url
        from core.database import ModelEndpoint
        import json as _json

        endpoint_url = model = None
        headers = {}

        # Try default model from settings first
        settings = _load_settings()
        ep_id = settings.get("default_endpoint_id", "")
        default_model = settings.get("default_model", "")
        if ep_id:
            db = SessionLocal()
            try:
                ep = db.query(ModelEndpoint).filter(
                    ModelEndpoint.id == ep_id, ModelEndpoint.is_enabled == True
                ).first()
                if ep:
                    base = _normalize_base(ep.base_url)
                    endpoint_url = build_chat_url(base)
                    model = default_model
                    if not model and ep.models:
                        try:
                            models = _json.loads(ep.models) if isinstance(ep.models, str) else ep.models
                            if models:
                                model = models[0]
                        except Exception:
                            pass
                    if ep.api_key:
                        headers = {"Authorization": f"Bearer {ep.api_key}"}
            finally:
                db.close()

        # Fall back to session model if no default configured
        if not endpoint_url and session:
            try:
                sess = session_manager.get_session(session)
                _assert_session_owner(sess, _owner(request))
                endpoint_url = sess.endpoint_url
                model = sess.model
                headers = sess.headers
            except KeyError:
                pass

        if not endpoint_url or not model:
            raise HTTPException(400, "No default model configured — set one in Settings")

        user = _owner(request)
        result = await audit_memories(
            memory_manager,
            memory_vector,
            endpoint_url,
            model,
            headers,
            owner=user,
        )

        if "error" in result and "before" not in result:
            raise HTTPException(502, f"Audit failed: {result['error']}")

        return {
            "ok": "error" not in result,
            "before": result.get("before", 0),
            "after": result.get("after", 0),
            "removed": result.get("before", 0) - result.get("after", 0),
            # True when the audit skipped the LLM because nothing changed
            # since the last tidy. Frontend already says "Already clean"
            # for removed==0, so this is here for future use / debugging.
            "already_tidy": bool(result.get("already_tidy")),
        }

    @router.post("/import")
    async def import_memories_from_file(
        request: Request,
        session: str | None = Form(None),
        file: UploadFile = File(...)
    ):
        """Extract memory suggestions from an uploaded file (PDF, TXT, MD, etc.)."""
        from src.auth_helpers import require_privilege
        require_privilege(request, "can_manage_memory")

        endpoint_url = None
        model = None
        headers = {}

        if session:
            try:
                sess = session_manager.get_session(session)
                _assert_session_owner(sess, _owner(request))
                endpoint_url = sess.endpoint_url
                model = sess.model
                headers = sess.headers
            except KeyError:
                 raise HTTPException(404, "Session not found — needed for LLM config")
        else:
            endpoint_url, model, headers = resolve_endpoint("utility", owner=_owner(request))
    
        if not endpoint_url or not model:
            raise HTTPException(400, "No LLM model configured. Set a default model in Settings.")

        content = await read_upload_limited(file, MEMORY_IMPORT_MAX_BYTES, "Memory import")
        filename = file.filename or "upload"
        _, ext = os.path.splitext(filename.lower())

        allowed = {".txt", ".md", ".pdf", ".csv", ".log", ".json", ".py", ".js", ".html"}
        if ext not in allowed:
            raise HTTPException(400, f"Unsupported file type: {ext}")

        # Extract text based on file type
        if ext == ".pdf":
            from src.document_processor import _process_pdf
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            try:
                text = _process_pdf(tmp_path, owner=_owner(request))
            finally:
                os.unlink(tmp_path)
        else:
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                from charset_normalizer import detect
                encoding = (detect(content) or {}).get("encoding") or "utf-8"
                text = content.decode(encoding, errors="replace")

        if not text.strip():
            return {"suggestions": [], "message": "No readable content found"}

        # Fast path: a .json upload that already looks like a memories export
        # (list of {text, category, ...} dicts, or list of strings) round-trips
        # directly without spending an LLM call to re-extract its own output.
        # Without this, re-importing a memories.json from another account
        # ran the file through the extractor, which often re-emitted the
        # entries as a numbered list (and the numbering leaked into the
        # `text` field).
        if ext == ".json":
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list) and parsed:
                direct = []
                for item in parsed:
                    if isinstance(item, dict) and item.get("text"):
                        direct.append({
                            "text": _strip_list_prefix(str(item["text"])),
                            "category": item.get("category") or "fact",
                        })
                    elif isinstance(item, str) and item.strip():
                        direct.append({
                            "text": _strip_list_prefix(item.strip()),
                            "category": "fact",
                        })
                if direct:
                    return {"suggestions": direct, "filename": filename}

        # Truncate very long documents
        if len(text) > 15000:
            text = text[:15000] + "\n[Truncated]"

        # Send to LLM for memory extraction
        import_prompt = (
            "You are a memory extraction assistant. The user uploaded a document. "
            "Analyze the text below and extract specific, useful facts — things like "
            "names, preferences, jobs, locations, relationships, opinions, projects, "
            "goals, contacts, or any other personal details worth remembering.\n\n"
            "Rules:\n"
            "- Each fact should be a short, self-contained statement\n"
            "- Do NOT extract generic knowledge\n"
            "- Focus on personal, memorable information\n"
            "- If there are no useful facts, return an empty array\n\n"
            "Return a JSON array of objects with 'text' and 'category' fields.\n"
            "Categories: 'identity', 'preference', 'fact', 'contact', 'project', 'goal'\n\n"
            "Return ONLY valid JSON, no markdown fences."
        )

        try:
            raw = await llm_call_async(
                endpoint_url,
                model,
                [
                    {"role": "system", "content": import_prompt},
                    {"role": "user", "content": f"Document: {filename}\n\n{text}"},
                ],
                temperature=0.2,
                max_tokens=2000,
                headers=headers,
            )

            # Parse JSON
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            suggestions = json.loads(raw)
            if isinstance(suggestions, list):
                normalized = []
                for s in suggestions:
                    if not s:
                        continue
                    if isinstance(s, dict):
                        s = dict(s)
                        if s.get("text"):
                            s["text"] = _strip_list_prefix(str(s["text"]))
                        normalized.append(s)
                    else:
                        normalized.append({"text": _strip_list_prefix(str(s)), "category": "fact"})
                suggestions = normalized
            else:
                suggestions = []

            return {"suggestions": suggestions, "filename": filename}

        except json.JSONDecodeError:
            # Fallback: split by lines, stripping any "1.", "2)" markdown-list
            # numbering the model added so saved memories don't keep the prefix.
            lines = [_strip_list_prefix(l.strip()) for l in raw.splitlines() if l.strip() and len(l.strip()) > 5]
            return {"suggestions": [{"text": l, "category": "fact"} for l in lines[:20]], "filename": filename}
        except Exception as e:
            logger.error(f"Memory import extraction failed: {e}")
            raise HTTPException(502, f"LLM extraction failed: {str(e)}")

    @router.post("/{memory_id}/pin")
    def pin_memory(request: Request, memory_id: str, pinned: bool = Form(True)):
        """Pin or unpin a memory. Pinned memories are always included in context."""
        user = _owner(request)
        all_mem = memory_manager.load_all()
        for i, memory in enumerate(all_mem):
            if memory["id"] == memory_id:
                _verify_memory_owner(memory, user)
                all_mem[i]["pinned"] = pinned
                memory_manager.save(all_mem)
                return {"ok": True, "pinned": pinned}
        raise HTTPException(404, f"Memory item {memory_id} not found")

    # Wildcard routes MUST come last — otherwise they swallow /import, /search, etc.
    @router.get("/{memory_id}")
    def get_memory_item(request: Request, memory_id: str):
        """Get a specific memory item by ID."""
        user = _owner(request)
        memories = memory_manager.load(owner=user)
        for memory in memories:
            if memory["id"] == memory_id:
                return {"memory": memory}

        raise HTTPException(404, "Memory not found")

    @router.put("/{memory_id}")
    def update_memory(request: Request, memory_id: str, text: str = Form(...), category: str = Form(None)):
        """Update an existing memory item with new text and optional category."""
        user = _owner(request)
        all_mem = memory_manager.load_all()
        for i, memory in enumerate(all_mem):
            if memory["id"] == memory_id:
                _verify_memory_owner(memory, user)
                all_mem[i]["text"] = text.strip()
                if category:
                    all_mem[i]["category"] = category
                all_mem[i]["timestamp"] = int(time.time())

                memory_manager.save(all_mem)
                # Sync vector index (remove old, add updated)
                if memory_vector and memory_vector.healthy:
                    memory_vector.remove(memory_id)
                    memory_vector.add(memory_id, text.strip())
                return {"ok": True, "message": "Memory updated successfully"}

        raise HTTPException(404, f"Memory item {memory_id} not found")

    @router.delete("/{memory_id}")
    def delete_memory(request: Request, memory_id: str):
        """Delete a memory item by its ID."""
        user = _owner(request)
        all_mem = memory_manager.load_all()

        # Find and verify ownership before deleting
        target = next((m for m in all_mem if m["id"] == memory_id), None)
        if not target:
            raise HTTPException(404, f"Memory item {memory_id} not found")
        _verify_memory_owner(target, user)

        all_mem = [m for m in all_mem if m["id"] != memory_id]
        memory_manager.save(all_mem)
        # Sync vector index
        if memory_vector and memory_vector.healthy:
            memory_vector.remove(memory_id)
        return {"ok": True, "message": "Memory deleted successfully"}

    return router
