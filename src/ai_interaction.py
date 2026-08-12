"""
ai_interaction.py

AI-to-AI interaction tools: pipeline and manage_memory, plus shared model
resolution (_resolve_model), the session-manager singleton, and dispatch_ai_tool.

As part of the tool -> registry migration (#3629), chat_with_model, ask_teacher
and list_models moved to src/agent_tools/model_interaction_tools.py, and
create_session, list_sessions, send_to_session and manage_session moved to
src/agent_tools/session_tools.py. Those modules reuse get_session_manager /
_resolve_model / AI_CHAT_TIMEOUT from here.

These are agent tools — the LLM writes fenced code blocks and they execute
through the standard agent_tools.py pipeline.
"""

import asyncio
import json
import logging
import uuid
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from src.constants import GENERATED_IMAGES_DIR
from src.memory import MemoryStoreUnreadable

logger = logging.getLogger(__name__)

AI_CHAT_TIMEOUT = 120  # seconds for a single LLM call
MAX_DEBATE_ROUNDS = 5
MAX_PIPELINE_STEPS = 10

# ---------------------------------------------------------------------------
# Global managers (set from app.py, same pattern as _mcp_manager)
# _session_manager is kept as a local cache for performance (avoiding
# repeated get_session_manager_instance() calls). It's synced with
# the authoritative singleton in core.models.
_session_manager = None
_memory_manager = None
_memory_vector = None
_rag_manager = None
_personal_docs_manager = None


def set_session_manager(mgr):
    """Set the global session manager. Syncs local cache + core singleton."""
    global _session_manager
    _session_manager = mgr
    from core.models import set_session_manager_instance
    set_session_manager_instance(mgr)


def get_session_manager():
    """Get the global session manager."""
    return _session_manager


def set_memory_manager(mgr, vector=None):
    global _memory_manager, _memory_vector
    _memory_manager = mgr
    _memory_vector = vector


def set_rag_manager(rag_mgr, personal_docs_mgr=None):
    global _rag_manager, _personal_docs_manager
    _rag_manager = rag_mgr
    _personal_docs_manager = personal_docs_mgr


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------

from src.endpoint_resolver import build_chat_url, build_headers, build_models_url, resolve_endpoint_runtime
from src.image_model_ids import looks_like_image_generation_model, model_id_leaf


def _resolve_model(spec: str, owner: Optional[str] = None, model_type: Optional[str] = None) -> Tuple[str, str, Dict]:
    """Resolve a model specifier to (endpoint_url, model_id, headers).

    Accepts:
      "model_name"              — searches all configured endpoints
      "model_name@endpoint_name" — looks up specific endpoint by display name

    Raises ValueError if model not found.
    """
    import httpx
    from src.database import SessionLocal, ModelEndpoint
    from src.llm_core import _detect_provider, ANTHROPIC_MODELS
    from src.auth_helpers import owner_filter

    spec = spec.strip()
    target_endpoint_name = None

    if "@" in spec:
        model_name, target_endpoint_name = spec.rsplit("@", 1)
        model_name = model_name.strip()
        target_endpoint_name = target_endpoint_name.strip()
    else:
        model_name = spec

    def _json_list(value) -> list[str]:
        try:
            data = json.loads(value or "[]")
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        return [str(x) for x in data if isinstance(x, (str, int, float)) and str(x)]

    def _image_like(name: str) -> bool:
        n = (name or "").lower()
        if looks_like_image_generation_model(n):
            return True
        return any(k in n for k in (
            "qwen-image", "qwen/image", "z-image", "flux", "stable-diffusion",
            "sdxl", "hidream", "boogu", "krea-2", "image-edit",
        ))

    db = SessionLocal()
    try:
        query = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
        if model_type:
            query = query.filter(ModelEndpoint.model_type == model_type)
        if target_endpoint_name:
            query = query.filter(ModelEndpoint.name.ilike(f"%{target_endpoint_name}%"))
        if owner:
            query = owner_filter(query, ModelEndpoint, owner)
        endpoints = query.all()

        if not endpoints:
            raise ValueError("No enabled endpoints found" +
                             (f" matching '{target_endpoint_name}'" if target_endpoint_name else ""))

        for ep in endpoints:
            try:
                base, api_key = resolve_endpoint_runtime(ep, owner=owner)
            except Exception:
                continue
            provider = _detect_provider(base)
            headers = build_headers(api_key, base)

            if provider == "anthropic":
                # Anthropic: match against hardcoded model list
                matched = None
                for am in ANTHROPIC_MODELS:
                    if model_name.lower() in am.lower() or am.lower() in model_name.lower():
                        matched = am
                        break
                if matched:
                    return build_chat_url(base), matched, headers
            else:
                # OpenAI-compatible and native Ollama: probe the provider's model list.
                endpoint_reachable = False
                try:
                    models_url = build_models_url(base)
                    if models_url:
                        r = httpx.get(models_url, headers=headers, timeout=5)
                        r.raise_for_status()
                        endpoint_reachable = True
                        data = r.json()
                        items = data if isinstance(data, list) else (data.get("data") or [])
                        model_ids = [m.get("id") for m in items if isinstance(m, dict) and m.get("id")]
                        if not model_ids:
                            model_ids = [
                                m.get("name") or m.get("model")
                                for m in (data.get("models") or [])
                                if m.get("name") or m.get("model")
                            ]
                    else:
                        endpoint_reachable = True
                        model_ids = json.loads(ep.cached_models or "[]")
                except Exception:
                    model_ids = []

                # Manual/local image endpoints are often registered with pinned
                # model ids, while /models may return a runtime alias or only the
                # served internal id. Include pinned/cached ids in the match set
                # so chat sessions using the HF repo id still resolve. Do not use
                # stale cached aliases when the endpoint itself is unreachable.
                if model_type == "image" and endpoint_reachable:
                    for extra in _json_list(getattr(ep, "pinned_models", None)) + _json_list(getattr(ep, "cached_models", None)):
                        if extra not in model_ids:
                            model_ids.append(extra)

                # Exact match first
                for mid in model_ids:
                    if mid.lower() == model_name.lower():
                        return build_chat_url(base), mid, headers

                # Partial match
                for mid in model_ids:
                    if model_name.lower() in mid.lower() or mid.lower() in model_name.lower():
                        return build_chat_url(base), mid, headers

                # Last resort for local image endpoints: if the requested model
                # name is clearly an image model, use the endpoint's first known
                # image model id. This prevents a harmless alias mismatch from
                # blocking image generation.
                if model_type == "image" and _image_like(model_name) and model_ids:
                    return build_chat_url(base), model_ids[0], headers

        raise ValueError(f"Model '{spec}' not found on any configured endpoint")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------



async def stream_ai_tool(tool: str, content: str, session_id: Optional[str] = None, owner: Optional[str] = None):
    """Dispatcher for streaming AI tools. Yields events as async generator."""
    # Fallback: run non-streaming and yield final result
    desc, result = await dispatch_ai_tool(tool, content, session_id, owner=owner)
    yield {"_final": True, "desc": desc, "result": result}


async def do_pipeline(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """Execute a multi-step pipeline where each model's output feeds the next.

    Content format (JSON):
      {"steps": [
        {"model": "model_a", "instruction": "Draft an essay about X"},
        {"model": "model_b", "instruction": "Critique the following draft"},
        {"model": "model_a", "instruction": "Revise based on this critique"}
      ]}

    Or line format:
      Line 1: step1_model | step1_instruction
      Line 2: step2_model | step2_instruction
      ...
    """
    from src.llm_core import llm_call_async

    # Try JSON parse first
    steps = None
    try:
        data = json.loads(content.strip())
        if isinstance(data, dict) and "steps" in data:
            steps = data["steps"]
        elif isinstance(data, list):
            steps = data
    except (json.JSONDecodeError, TypeError):
        pass

    # Fall back to line format: model | instruction
    if not steps:
        steps = []
        for line in content.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            if "|" in line:
                parts = line.split("|", 1)
                steps.append({"model": parts[0].strip(), "instruction": parts[1].strip()})
            else:
                return {"error": "Each line must be: model | instruction (or use JSON format)"}

    if not steps:
        return {"error": "No pipeline steps provided"}
    if len(steps) > MAX_PIPELINE_STEPS:
        return {"error": f"Maximum {MAX_PIPELINE_STEPS} steps allowed"}

    # Resolve all models first (fail fast)
    resolved = []
    for i, step in enumerate(steps):
        model_spec = step.get("model", "").strip()
        instruction = step.get("instruction", "").strip()
        if not model_spec or not instruction:
            return {"error": f"Step {i + 1}: both 'model' and 'instruction' are required"}
        try:
            url, model, headers = await asyncio.to_thread(_resolve_model, model_spec, owner=owner)
            resolved.append((url, model, headers, instruction))
        except ValueError as e:
            return {"error": f"Step {i + 1}: {e}"}

    # Execute pipeline
    step_outputs = []
    previous_output = None

    try:
        for i, (url, model, headers, instruction) in enumerate(resolved):
            if previous_output:
                user_content = (
                    f"Previous step's output:\n\n{previous_output}\n\n"
                    f"Your task: {instruction}"
                )
            else:
                user_content = instruction

            messages = [
                {"role": "system", "content": f"You are step {i + 1} in a processing pipeline. {instruction}"},
                {"role": "user", "content": user_content},
            ]

            response = await llm_call_async(
                url, model, messages, headers=headers, timeout=AI_CHAT_TIMEOUT
            )

            step_outputs.append({
                "step": i + 1,
                "model": model,
                "instruction": instruction,
                "output": response[:5000] if len(response) > 5000 else response,
            })

            previous_output = response

        # Build readable result
        result_lines = [f"# Pipeline Results ({len(resolved)} steps)\n"]
        for so in step_outputs:
            result_lines.append(f"## Step {so['step']}: {so['model']}")
            result_lines.append(f"*Instruction: {so['instruction']}*\n")
            result_lines.append(so["output"])
            result_lines.append("\n---\n")

        return {
            "results": "\n".join(result_lines),
            "steps": step_outputs,
            "final_output": previous_output,
        }
    except Exception as e:
        logger.error(f"pipeline failed at step {len(step_outputs) + 1}: {e}")
        return {"error": f"Pipeline failed at step {len(step_outputs) + 1}: {e}"}


# ---------------------------------------------------------------------------
# Session management tool
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Memory management tool
# ---------------------------------------------------------------------------

async def do_manage_memory(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """Manage memories: list, add, edit, delete, search.

    Content format:
      Line 1: action (list|add|edit|delete|search)
      Line 2+: action-specific params

    Actions:
      list                    — list all memories (optional line 2: category filter)
      add                     — line 2: text, optional line 3: category (fact|event|contact|preference)
      edit                    — line 2: memory_id, line 3: new text
      delete                  — line 2: memory_id
      search                  — line 2: query
    """
    if not _memory_manager:
        return {"error": "Memory manager not available"}

    lines = content.strip().split("\n")
    if not lines:
        return {"error": "Need at least 1 line: action"}

    action = lines[0].strip().lower()

    if action == "list":
        category_filter = lines[1].strip().lower() if len(lines) > 1 and lines[1].strip() else None
        memories = _memory_manager.load(owner=owner)
        if category_filter:
            memories = [m for m in memories if m.get("category", "").lower() == category_filter]
        if not memories:
            return {"results": "No memories found" + (f" in category '{category_filter}'" if category_filter else "") + "."}

        result_lines = [f"Found {len(memories)} memory entries:\n"]
        for m in memories:
            cat = m.get("category", "fact")
            mid = m.get("id", "?")[:8]
            text = m.get("text", "")
            if len(text) > 150:
                text = text[:150] + "..."
            result_lines.append(f"- [{cat}] `{mid}` — {text}")
        return {"results": "\n".join(result_lines)}

    elif action == "add":
        if len(lines) < 2:
            return {"error": "Add needs line 2: memory text"}
        text = lines[1].strip()
        category = lines[2].strip().lower() if len(lines) > 2 and lines[2].strip() else "fact"
        if not text:
            return {"error": "Memory text cannot be empty"}

        entry = _memory_manager.add_entry(text, source="ai_agent", category=category, owner=owner)
        # Strict load: this is a read-modify-write, and it is the path an
        # ordinary "remember that I prefer X" takes. Degrading to [] here would
        # save just this one entry over a store we only failed to read,
        # atomically destroying every memory in it (issue #5673).
        try:
            memories = _memory_manager.load_all_for_update()
        except MemoryStoreUnreadable as e:
            logger.error("Refusing to add memory, store unreadable: %s", e)
            return {"error": "Memory store is temporarily unreadable — nothing was saved."}
        memories.append(entry)
        _memory_manager.save(memories)

        # Update vector index if available
        if _memory_vector and hasattr(_memory_vector, 'healthy') and _memory_vector.healthy:
            try:
                _memory_vector.add(entry["id"], text)
            except Exception:
                pass
        try:
            from src.event_bus import fire_event
            fire_event("memory_added", owner)
        except Exception:
            logger.debug("memory_added event dispatch failed", exc_info=True)

        return {"action": "add", "memory_id": entry["id"],
                "results": f"Memory added: [{category}] {text}"}

    elif action == "edit":
        if len(lines) < 3:
            return {"error": "Edit needs line 2: memory_id, line 3: new text"}
        memory_id = lines[1].strip()
        new_text = lines[2].strip()
        if not new_text:
            return {"error": "New text cannot be empty"}

        memories = _memory_manager.load_all()
        found = False
        for m in memories:
            if m.get("id", "").startswith(memory_id):
                # Verify ownership
                if owner and m.get("owner") != owner:
                    return {"error": f"Memory '{memory_id}' not found"}
                m["text"] = new_text
                m["timestamp"] = int(time.time())
                found = True
                full_id = m["id"]
                break
        if not found:
            return {"error": f"Memory '{memory_id}' not found"}
        _memory_manager.save(memories)

        # Update vector index
        if _memory_vector and hasattr(_memory_vector, 'healthy') and _memory_vector.healthy:
            try:
                _memory_vector.add(full_id, new_text)
            except Exception:
                pass

        return {"action": "edit", "memory_id": memory_id,
                "results": f"Memory updated: {new_text}"}

    elif action == "delete":
        if len(lines) < 2:
            return {"error": "Delete needs line 2: memory_id"}
        memory_id = lines[1].strip()

        memories = _memory_manager.load_all()
        original_len = len(memories)
        full_id = None
        delete_id = None
        for m in memories:
            if m.get("id", "").startswith(memory_id):
                # Verify ownership
                if owner and m.get("owner") != owner:
                    return {"error": f"Memory '{memory_id}' not found"}
                full_id = m["id"]
                delete_id = m["id"]
                break
        memories = [m for m in memories if m.get("id") != delete_id]
        if len(memories) == original_len:
            return {"error": f"Memory '{memory_id}' not found"}
        _memory_manager.save(memories)

        # Remove from vector index
        if _memory_vector and full_id and hasattr(_memory_vector, 'healthy') and _memory_vector.healthy:
            try:
                _memory_vector.remove(full_id)
            except Exception:
                pass

        return {"action": "delete", "memory_id": memory_id,
                "results": f"Memory '{memory_id}' deleted"}

    elif action == "search":
        if len(lines) < 2:
            return {"error": "Search needs line 2: query"}
        query = lines[1].strip()
        memories = _memory_manager.load(owner=owner)
        query_lower = query.lower()
        exact_results = [m for m in memories if query_lower in (m.get("text", "").lower())]

        if hasattr(_memory_manager, 'get_relevant_memories'):
            vector_results = _memory_manager.get_relevant_memories(query, memories, threshold=0.05, max_items=20)
        else:
            vector_results = []
        seen = set()
        results = []
        for m in [*exact_results, *vector_results]:
            mid = m.get("id")
            if mid in seen:
                continue
            seen.add(mid)
            results.append(m)
            if len(results) >= 20:
                break

        if not results:
            return {"results": f"No memories found matching '{query}'."}
        result_lines = [f"Found {len(results)} matching memories:\n"]
        for m in results:
            cat = m.get("category", "fact")
            mid = m.get("id", "?")[:8]
            text = m.get("text", "")
            result_lines.append(f"- [{cat}] `{mid}` — {text}")
        return {"results": "\n".join(result_lines)}

    else:
        return {"error": f"Unknown action '{action}'. Use: list, add, edit, delete, search"}


# ---------------------------------------------------------------------------
# RAG management tool
# ---------------------------------------------------------------------------

async def do_manage_rag(content: str, session_id: Optional[str] = None) -> Dict:
    """Manage RAG indexed documents: list, add_directory, remove_directory.

    Content format:
      Line 1: action (list|add_directory|remove_directory)
      Line 2: directory path (for add/remove)
    """
    lines = content.strip().split("\n")
    if not lines:
        return {"error": "No action specified"}
    action = lines[0].strip().lower()

    if action == "list":
        if not _personal_docs_manager:
            return {"results": "Personal docs manager not available. RAG may not be configured."}
        try:
            files = []
            if hasattr(_personal_docs_manager, 'index'):
                files = _personal_docs_manager.index or []
            dirs = []
            if hasattr(_personal_docs_manager, 'get_indexed_directories'):
                dirs = _personal_docs_manager.get_indexed_directories()

            result_lines = []
            if dirs:
                result_lines.append(f"**Indexed directories ({len(dirs)}):**")
                for d in dirs:
                    result_lines.append(f"  - `{d}`")
            if files:
                result_lines.append(f"\n**Indexed files ({len(files)}):**")
                for f in files[:50]:
                    name = f.get("name", str(f)) if isinstance(f, dict) else str(f)
                    result_lines.append(f"  - {name}")
                if len(files) > 50:
                    result_lines.append(f"  ... and {len(files) - 50} more")

            if not result_lines:
                return {"results": "No files or directories indexed in RAG."}
            return {"results": "\n".join(result_lines)}
        except Exception as e:
            return {"error": str(e)}

    elif action == "add_directory":
        if len(lines) < 2:
            return {"error": "add_directory needs line 2: directory path"}
        directory = lines[1].strip()

        import os
        directory = os.path.expanduser(directory)
        if not os.path.isdir(directory):
            return {"error": f"Directory not found: {directory}"}

        if not _rag_manager:
            return {"error": "RAG manager not available"}

        try:
            result = _rag_manager.index_personal_documents(directory)
            indexed = result.get("indexed", 0) if isinstance(result, dict) else 0
            return {"action": "add_directory", "directory": directory,
                    "results": f"Directory '{directory}' added to RAG index ({indexed} files indexed)"}
        except Exception as e:
            return {"error": f"Failed to index directory: {e}"}

    elif action == "remove_directory":
        if len(lines) < 2:
            return {"error": "remove_directory needs line 2: directory path"}
        directory = lines[1].strip()

        if not _personal_docs_manager:
            return {"error": "Personal docs manager not available"}

        try:
            if hasattr(_personal_docs_manager, 'remove_directory'):
                # Performs a targeted per-directory delete (#1660). The previous
                # unconditional _rag_manager.rebuild_index() here wiped the whole
                # collection on every remove (even for untracked dirs) and has
                # been removed.
                _personal_docs_manager.remove_directory(directory)
            return {"action": "remove_directory", "directory": directory,
                    "results": f"Directory '{directory}' removed from RAG index"}
        except Exception as e:
            return {"error": f"Failed to remove directory: {e}"}

    else:
        return {"error": f"Unknown action '{action}'. Use: list, add_directory, remove_directory"}


# ---------------------------------------------------------------------------
# UI control tool (returns events for frontend to apply)
# ---------------------------------------------------------------------------

async def do_ui_control(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """Control frontend UI: toggle settings, switch model, change theme.

    Content format:
      Line 1: action
      Line 2+: action-specific params

    Actions:
      toggle <name> <on|off>  — Toggle a setting (web, bash, rag, research, incognito, document_editor)
      set_mode <agent|chat>   — Switch between agent and chat mode
      switch_model <model>    — Change the model for the current session
      set_theme <preset>      — Apply a built-in theme preset (dark, light, midnight, paper, cyberpunk, retrowave, forest, ocean, ume, copper, terminal, organs, lavender, gpt, claude, cute)
      create_theme <name> <bg> <fg> <panel> <border> <accent> [key=val ...] — Create custom theme. Optional key=val: advanced color overrides AND background effects: bgPattern=<none|dots|synapse|rain|constellations|perlin-flow|petals|sparkles|embers>, bgEffectColor=#RRGGBB, bgEffectIntensity=<num>, bgEffectSize=<num>, frosted=true|false
      open_panel <name>       — Open a panel (documents, gallery, email, sessions, notes, memories, skills, settings, cookbook)
      open_email_reply <uid> [folder] [reply|reply-all|ai-reply] [body text] — Open a reply draft document for an email; does not send. ALWAYS append the body text when the user told you what to say (one-shot draft); only omit body when the user just asked to "open a reply" without content.
      get_toggles             — Return current toggle states (server-side knowledge)
    """
    lines = content.strip().split("\n")
    if not lines:
        return {"error": "No action specified"}

    parts = lines[0].strip().split(None, 2)
    action = parts[0].lower()

    if action == "toggle":
        if len(parts) < 3:
            return {"error": "toggle needs: toggle <name> <on|off>"}
        toggle_name = parts[1].lower()
        state = parts[2].lower() in ("on", "true", "1", "yes", "enable", "enabled")
        # Friendly aliases — users say "shell" / "search" naturally.
        _toggle_aliases = {
            "shell": "bash",
            "terminal": "bash",
            "search": "web",
            "websearch": "web",
            "web_search": "web",
            "deepresearch": "research",
            "deep_research": "research",
            "documents": "document_editor",
            "doc": "document_editor",
            "docs": "document_editor",
            "private": "incognito",
        }
        toggle_name = _toggle_aliases.get(toggle_name, toggle_name)
        valid_toggles = {"web", "bash", "rag", "research", "incognito", "document_editor"}
        if toggle_name not in valid_toggles:
            return {"error": f"Unknown toggle '{toggle_name}'. Valid: {', '.join(sorted(valid_toggles))}"}
        return {
            "ui_event": "toggle",
            "toggle_name": toggle_name,
            "state": state,
            "results": f"Toggle '{toggle_name}' set to {'on' if state else 'off'}",
        }

    elif action == "set_mode":
        if len(parts) < 2:
            return {"error": "set_mode needs: set_mode <agent|chat>"}
        mode = parts[1].lower()
        if mode not in ("agent", "chat"):
            return {"error": f"Invalid mode '{mode}'. Use: agent, chat"}
        return {
            "ui_event": "set_mode",
            "mode": mode,
            "results": f"Mode changed to '{mode}'",
        }

    elif action == "switch_model":
        model_spec = " ".join(parts[1:]) if len(parts) > 1 else ""
        if not model_spec:
            model_spec = lines[1].strip() if len(lines) > 1 else ""
        if not model_spec:
            return {"error": "switch_model needs a model name"}

        # Resolve the model to validate it exists
        try:
            url, model_id, headers = await asyncio.to_thread(_resolve_model, model_spec, owner=owner)
        except ValueError as e:
            return {"error": str(e)}

        # Update current session's model if we have a session
        if session_id and _session_manager:
            from src.database import SessionLocal as SL2, Session as DbSess2
            db2 = SL2()
            try:
                db_s = db2.query(DbSess2).filter(DbSess2.id == session_id).first()
                if db_s:
                    db_s.endpoint_url = url
                    db_s.model = model_id
                    db2.commit()
            finally:
                db2.close()

            sess = _session_manager.get_session(session_id)
            if sess:
                sess.endpoint_url = url
                sess.model = model_id
                if headers:
                    sess.headers = headers

        return {
            "ui_event": "switch_model",
            "model": model_id,
            "endpoint_url": url,
            "results": f"Model switched to '{model_id}'",
        }

    elif action == "set_theme":
        theme_name = parts[1].lower() if len(parts) > 1 else ""
        # Theme colors are defined in static/js/theme.js on the frontend.
        # We pass the name; the frontend looks it up from presets + custom themes.
        # Also check user's custom themes stored in prefs.
        # Must match the THEMES keys in static/js/theme.js.
        known_presets = [
            "dark", "light", "midnight", "paper", "cyberpunk", "retrowave",
            "forest", "ocean", "ume", "copper", "terminal", "organs",
            "lavender", "gpt", "claude", "cute",
        ]
        custom_themes = {}
        try:
            from routes.prefs_routes import _load as _load_prefs
            custom_themes = _load_prefs().get("custom-themes", {}) or {}
        except Exception:
            pass
        all_known = set(known_presets) | set(custom_themes.keys())
        if theme_name not in all_known:
            custom_label = f" | Custom: {', '.join(sorted(custom_themes.keys()))}" if custom_themes else ""
            return {"error": f"Unknown theme '{theme_name}'. Available: {', '.join(sorted(known_presets))}{custom_label}"}
        return {
            "ui_event": "set_theme",
            "theme_name": theme_name,
            "results": f"Theme changed to '{theme_name}'",
        }

    elif action == "create_theme":
        # Re-split without limit to get all parts
        parts = lines[0].strip().split()
        # create_theme <name> <bg> <fg> <panel> <border> <accent> [key=value ...]
        if len(parts) < 7:
            return {"error": "create_theme needs: create_theme <name> <bg> <fg> <panel> <border> <accent> (all hex colors). Optional advanced color key=value pairs (userBubbleBg, aiBubbleBg, bubbleBorder, sidebarBg, sectionAccent, brandColor, inputBg, inputBorder, sendBtnBg, sendBtnHover, codeBg, codeFg, toggleBg, toggleActive, accentPrimary, accentError). Optional background EFFECTS: bgPattern=<none|dots|synapse|rain|constellations|perlin-flow|petals|sparkles|embers>, bgEffectColor=#RRGGBB, bgEffectIntensity=<num e.g. 1>, bgEffectSize=<num e.g. 1>, frosted=true|false"}
        name = parts[1].lower().replace(" ", "-")
        colors = {"bg": parts[2], "fg": parts[3], "panel": parts[4], "border": parts[5], "red": parts[6]}
        # Validate base hex colors
        import re as _re
        for k, v in colors.items():
            if not _re.match(r'^#[0-9a-fA-F]{6}$', v):
                return {"error": f"Invalid hex color for {k}: '{v}'. Use format #RRGGBB"}
        # Parse optional advanced key=value pairs
        adv_keys = {
            "userBubbleBg", "aiBubbleBg", "bubbleBorder", "sidebarBg",
            "sectionAccent", "brandColor", "inputBg", "inputBorder",
            "sendBtnBg", "sendBtnHover", "codeBg", "codeFg",
            "toggleBg", "toggleActive", "accentPrimary", "accentError",
        }
        advanced = {}
        # Background-effect fields (animated pattern + frosted glass). Different
        # value types than the hex-only advanced keys, so parse separately.
        _BG_PATTERNS = {"none", "dots", "synapse", "rain", "constellations",
                        "perlin-flow", "petals", "sparkles", "embers"}
        bg = {}
        for part in parts[7:]:
            if "=" not in part:
                continue
            ak, av = part.split("=", 1)
            if ak in adv_keys:
                if not _re.match(r'^#[0-9a-fA-F]{6}$', av):
                    return {"error": f"Invalid hex color for advanced key {ak}: '{av}'. Use format #RRGGBB"}
                advanced[ak] = av
            elif ak == "bgPattern":
                if av not in _BG_PATTERNS:
                    return {"error": f"Invalid bgPattern '{av}'. Use one of: {', '.join(sorted(_BG_PATTERNS))}"}
                bg["pattern"] = av
            elif ak == "bgEffectColor":
                if not _re.match(r'^#[0-9a-fA-F]{6}$', av):
                    return {"error": f"Invalid hex color for bgEffectColor: '{av}'. Use format #RRGGBB"}
                bg["effectColor"] = av
            elif ak in ("bgEffectIntensity", "bgEffectSize"):
                try:
                    bg["effectIntensity" if ak == "bgEffectIntensity" else "effectSize"] = float(av)
                except ValueError:
                    return {"error": f"Invalid number for {ak}: '{av}'"}
            elif ak == "frosted":
                bg["frosted"] = av.lower() in ("true", "1", "yes", "on")
        if advanced:
            colors["advanced"] = advanced
        return {
            "ui_event": "create_theme",
            "theme_name": name,
            "colors": colors,
            "bg": bg or None,
            "results": f"Custom theme '{name}' created and applied"
                       + (f" with {len(advanced)} advanced overrides" if advanced else "")
                       + (f" + background effect ({bg.get('pattern', 'frosted' if bg.get('frosted') else 'custom')})" if bg else ""),
        }

    elif action == "highlight":
        selector = parts[1] if len(parts) > 1 else ""
        label = " ".join(parts[2:]) if len(parts) > 2 else ""
        if not selector:
            return {"error": "highlight needs: highlight <css-selector> [label]"}
        return {
            "ui_event": "highlight",
            "selector": selector,
            "label": label,
            "results": f"Highlighting '{selector}'",
        }

    elif action == "clear_highlight":
        return {
            "ui_event": "clear_highlight",
            "results": "Highlights cleared",
        }

    elif action == "open_panel":
        # Open a top-level panel/modal: documents/library, gallery,
        # email, sessions, notes, memories, skills, settings, cookbook.
        panel = parts[1].lower() if len(parts) > 1 else ""
        _panel_aliases = {
            "documents": "documents",
            "document": "documents",
            "doc": "documents",
            "docs": "documents",
            "library": "documents",
            "doclib": "documents",
            "gallery": "gallery",
            "images": "gallery",
            "email": "email",
            "emails": "email",
            "inbox": "email",
            "mail": "email",
            "sessions": "sessions",
            "chats": "sessions",
            "history": "sessions",
            "notes": "notes",
            "note": "notes",
            "todo": "notes",
            "todos": "notes",
            "memories": "memories",
            "memory": "memories",
            "brain": "memories",
            "skills": "skills",
            "settings": "settings",
            "preferences": "settings",
            "cookbook": "cookbook",
            "models": "cookbook",
            "llm": "cookbook",
            "serve": "cookbook",
            "serving": "cookbook",
        }
        target = _panel_aliases.get(panel)
        if not target:
            return {"error": f"Unknown panel '{panel}'. Valid: documents, gallery, email, sessions, notes, memories, skills, settings, cookbook."}
        return {
            "ui_event": "open_panel",
            "panel": target,
            "results": f"Opening {target} panel",
        }

    elif action == "open_email_reply":
        # Two forms supported:
        #   open_email_reply <uid> [folder] [reply|reply-all|ai-reply]
        #   open_email_reply <uid> [folder] [reply|reply-all|ai-reply]
        #     <body text on subsequent lines or after the mode token>
        # The body text (if any) gets pre-filled into the reply draft so the
        # agent can compose-and-open in one tool call instead of opening an
        # empty draft and leaving the user to wonder what happened.
        first_line = lines[0].strip()
        parts = first_line.split(maxsplit=4)
        uid = parts[1].strip() if len(parts) > 1 else ""
        folder = parts[2].strip() if len(parts) > 2 else "INBOX"
        mode = parts[3].strip().lower() if len(parts) > 3 else "reply"
        # Body: everything on the first line after the mode token, plus any
        # subsequent lines. Allows multi-line bodies.
        inline_body = parts[4] if len(parts) > 4 else ""
        rest_lines = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
        body = (inline_body + ("\n" + rest_lines if rest_lines else "")).strip()
        if not uid:
            return {"error": "open_email_reply needs: open_email_reply <uid> [folder] [reply|reply-all|ai-reply] [body text]"}
        if mode not in ("reply", "reply-all", "ai-reply"):
            mode = "reply"
        # Body is REQUIRED for the agent path. Opening an empty draft is what
        # users do by clicking the Reply button — they don't ask the agent
        # for that. Every agent invocation of open_email_reply MUST include
        # the body. Reject empty so the agent retries with the content the
        # user asked for. Exception: ai-reply mode triggers the existing
        # AI-Reply path on the frontend which generates its own body.
        if not body and mode != "ai-reply":
            return {
                "error": (
                    "open_email_reply called without body. The agent path REQUIRES a body — "
                    "opening an empty draft is the wrong response when the user asked you to write. "
                    "Re-call with the reply text included: "
                    f"`open_email_reply {uid} {folder or 'INBOX'} {mode} <your reply text here>`. "
                    "Compose the reply now based on the open email's content and the user's request, "
                    "then call this tool again with the body. Do NOT call create_document instead."
                ),
            }
        result = {
            "ui_event": "open_email_reply",
            "uid": uid,
            "folder": folder or "INBOX",
            "mode": mode,
            "results": f"Opening reply draft for email UID {uid}" + (" with pre-filled body" if body else ""),
        }
        if body:
            result["body"] = body
        return result

    elif action == "get_toggles":
        return {
            "results": (
                "Toggle states are managed client-side in localStorage. "
                "Available toggles: web, bash, rag, research, incognito, document_editor. "
                "Use 'toggle <name> <on|off>' to change them."
            )
        }

    else:
        return {"error": f"Unknown action '{action}'. Use: toggle, set_mode, switch_model, set_theme, highlight, clear_highlight, get_toggles"}


# ---------------------------------------------------------------------------
# Image generation
# ---------------------------------------------------------------------------

async def do_generate_image(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """Generate an image using an image-capable model (e.g. gpt-image-1).

    Content format:
      Line 1: prompt describing the image
      Line 2: model name (optional, default auto-detects: prefers gpt-image-1.5 > gpt-image-1)
      Line 3: size (optional, defaults to 1024x1024)
      Line 4: quality (optional, defaults to medium — options: low, medium, high, auto)
    """
    import base64
    import httpx
    import os
    from pathlib import Path
    from src.url_safety import check_outbound_url

    lines = content.strip().split("\n")
    prompt = lines[0].strip() if lines else ""
    model_spec = lines[1].strip() if len(lines) > 1 and lines[1].strip() else ""
    size = lines[2].strip() if len(lines) > 2 and lines[2].strip() else "1024x1024"
    quality = lines[3].strip() if len(lines) > 3 and lines[3].strip() else "medium"

    if not prompt:
        return {"error": "Image prompt is required (line 1)"}

    # Load admin settings for defaults
    try:
        from src.settings import load_settings
        _settings = load_settings()
    except Exception:
        _settings = {}

    # Use admin-configured model/quality if not specified by the tool call
    if not model_spec:
        model_spec = _settings.get("image_model", "")
    if quality == "medium" and _settings.get("image_quality"):
        quality = _settings["image_quality"]

    # Auto-detect best available image model if still not set
    if not model_spec:
        for candidate in ("gpt-image-1.5", "gpt-image-1", "dall-e-3"):
            try:
                await asyncio.to_thread(_resolve_model, candidate, owner=owner)
                model_spec = candidate
                break
            except ValueError:
                continue
        # Fallback: find any locally registered image-type endpoint
        if not model_spec:
            try:
                from src.database import SessionLocal, ModelEndpoint
                from src.auth_helpers import owner_filter
                import httpx as _req
                _idb = SessionLocal()
                try:
                    _img_q = _idb.query(ModelEndpoint).filter(
                        ModelEndpoint.is_enabled == True,
                        ModelEndpoint.model_type == "image",
                    )
                    if owner:
                        _img_q = owner_filter(_img_q, ModelEndpoint, owner)
                    _img_eps = _img_q.all()
                    for _iep in _img_eps:
                        _ibase = _iep.base_url.rstrip("/")
                        if not _ibase.endswith("/v1"):
                            _ibase += "/v1"
                        try:
                            _r = _req.get(_ibase + "/models", timeout=3)
                            _r.raise_for_status()
                            _data = _r.json()
                            _ditems = _data if isinstance(_data, list) else (_data.get("data") or [])
                            _mids = [m.get("id") for m in _ditems if isinstance(m, dict) and m.get("id")]
                            if _mids:
                                model_spec = _mids[0]
                                break
                        except Exception:
                            continue
                finally:
                    _idb.close()
            except Exception:
                pass
        if not model_spec:
            return {"error": "No image model found. Configure one in Admin → Image Generation."}

    async def _resolve_image_model(model_name: str):
        def _call():
            try:
                return _resolve_model(model_name, owner=owner, model_type="image")
            except TypeError as exc:
                if "model_type" not in str(exc):
                    raise
                return _resolve_model(model_name, owner=owner)
        return await asyncio.to_thread(_call)

    # Resolve the model to find the right endpoint
    try:
        try:
            url, model_id, headers = await _resolve_image_model(model_spec)
        except ValueError:
            _lower_model_spec = model_spec.lower()
            if not (
                any(_name in _lower_model_spec for _name in ("gpt-image", "dall-e"))
                or looks_like_image_generation_model(_lower_model_spec)
            ):
                raise
            url, model_id, headers = await asyncio.to_thread(_resolve_model, model_spec, owner=owner)
    except ValueError:
        return {"error": f"No endpoint found with image model '{model_spec}'. "
                "Configure an OpenAI-compatible endpoint with image generation support."}

    # Detect if this is a GPT image model vs DALL-E vs local diffusion
    _model_leaf = model_id_leaf(model_id)
    is_gpt_image = _model_leaf.startswith("gpt-image") or (_model_leaf.startswith("gpt-") and "-image" in _model_leaf)
    is_dalle = _model_leaf.startswith("dall-e")
    is_local_diffusion = not is_gpt_image and not is_dalle

    # Build the images endpoint URL from the chat completions URL
    base_url = url.replace("/chat/completions", "").replace("/v1/messages", "").rstrip("/")
    images_url = base_url + "/images/generations"

    # Validate size for cloud image models (local diffusion accepts any WxH)
    valid_gpt_sizes = {"1024x1024", "1024x1536", "1536x1024", "auto"}
    valid_dalle3_sizes = {"1024x1024", "1024x1792", "1792x1024"}
    if is_gpt_image and size not in valid_gpt_sizes:
        size = "1024x1024"
    elif is_dalle and size not in valid_dalle3_sizes:
        size = "1024x1024"

    payload = {
        "model": model_id,
        "prompt": prompt,
        "n": 1,
        "size": size,
    }

    # GPT image models and local diffusion support quality; DALL-E does not
    if is_gpt_image or is_local_diffusion:
        if quality in ("low", "medium", "high", "auto"):
            payload["quality"] = quality
        else:
            payload["quality"] = "medium"

    logger.info(f"Image generation: model={model_id}, size={size}, quality={quality}, prompt={prompt[:80]}")

    try:
        # GPT image models can take 30-120s+ depending on quality
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0)) as client:
            resp = await client.post(images_url, json=payload, headers=headers)

            if resp.status_code != 200:
                error_text = resp.text[:500]
                try:
                    err_json = resp.json()
                    error_text = err_json.get("error", {}).get("message", error_text) if isinstance(err_json.get("error"), dict) else str(err_json.get("error", error_text))
                except Exception:
                    pass
                return {"error": f"Image generation failed ({resp.status_code}): {error_text}"}

            data = resp.json()
            images = data.get("data", [])
            if not images:
                return {"error": "No images returned from API"}

            img = images[0]
            image_url = None
            image_id = None

            def _save_to_gallery(filename: str) -> str:
                """Insert a GalleryImage row and return the new id (or '')."""
                try:
                    from src.database import SessionLocal as _GallerySL, GalleryImage
                    new_id = str(uuid.uuid4())
                    _gdb = _GallerySL()
                    _gdb.add(GalleryImage(
                        id=new_id,
                        filename=filename,
                        prompt=prompt,
                        model=model_id,
                        size=size,
                        quality=payload.get("quality", "medium"),
                        session_id=session_id,
                        owner=owner,
                    ))
                    _gdb.commit()
                    _gdb.close()
                    return new_id
                except Exception as _ge:
                    logger.warning(f"Failed to save gallery record: {_ge}")
                    return ""

            # GPT image models always return b64_json; DALL-E may return url
            if img.get("b64_json"):
                img_dir = Path(GENERATED_IMAGES_DIR)
                img_dir.mkdir(parents=True, exist_ok=True)
                filename = f"{uuid.uuid4().hex[:12]}.png"
                img_path = img_dir / filename
                img_path.write_bytes(base64.b64decode(img.get("b64_json")))
                image_url = f"/api/generated-image/{filename}"
                image_id = _save_to_gallery(filename)

            elif img.get("url"):
                # Download external URL and save locally (DALL-E returns temp URLs)
                result_url = img["url"]
                ok, reason = check_outbound_url(
                    result_url,
                    block_private=os.getenv("IMAGE_BLOCK_PRIVATE_IPS", "false").lower() == "true",
                )
                if not ok:
                    return {"error": f"Image API returned unsafe image URL: {reason}"}
                try:
                    dl_resp = httpx.get(result_url, timeout=60)
                    if dl_resp.status_code == 200:
                        img_dir = Path(GENERATED_IMAGES_DIR)
                        img_dir.mkdir(parents=True, exist_ok=True)
                        filename = f"{uuid.uuid4().hex[:12]}.png"
                        img_path = img_dir / filename
                        img_path.write_bytes(dl_resp.content)
                        image_url = f"/api/generated-image/{filename}"
                        image_id = _save_to_gallery(filename)
                    else:
                        image_url = result_url  # fallback to external URL
                except Exception as _dl_e:
                    logger.warning(f"Failed to download DALL-E image: {_dl_e}")
                    image_url = result_url  # fallback to external URL
            else:
                return {"error": "Image API returned unexpected format (no b64_json or url)"}

            return {
                "results": f"Generated image for: {prompt[:100]}",
                "image_url": image_url,
                "image_id": image_id,
                "image_prompt": prompt,
                "image_model": model_id,
                "image_size": size,
                "image_quality": payload.get("quality", "medium"),
            }

    except httpx.TimeoutException:
        return {"error": "Image generation timed out (300s). The model may be overloaded — try again or use quality=low."}
    except Exception as e:
        return {"error": f"Image generation error: {str(e)}"}


async def do_edit_image(
    prompt: str,
    image_path: str,
    model_spec: str = "",
    session_id: Optional[str] = None,
    owner: Optional[str] = None,
    size: str = "1024x1024",
    quality: str = "medium",
    progress_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
) -> Dict:
    """Edit an uploaded image using the configured image endpoint."""
    import base64
    import httpx
    import mimetypes
    import os
    from pathlib import Path
    from src.url_safety import check_outbound_url

    prompt = (prompt or "").strip()
    if not prompt:
        return {"error": "Image edit prompt is required"}
    path = Path(image_path)
    if not path.exists() or not path.is_file():
        return {"error": "Attached image file was not found"}

    try:
        from src.settings import load_settings
        _settings = load_settings()
    except Exception:
        _settings = {}

    if not model_spec:
        model_spec = _settings.get("image_model", "")
    if quality == "medium" and _settings.get("image_quality"):
        quality = _settings["image_quality"]
    if not model_spec:
        return {"error": "No image model selected for image editing"}

    try:
        try:
            def _call():
                try:
                    return _resolve_model(model_spec, owner=owner, model_type="image")
                except TypeError as exc:
                    if "model_type" not in str(exc):
                        raise
                    return _resolve_model(model_spec, owner=owner)
            url, model_id, headers = await asyncio.to_thread(_call)
        except ValueError:
            url, model_id, headers = await asyncio.to_thread(_resolve_model, model_spec, owner=owner)
    except ValueError:
        return {"error": f"No endpoint found with image model '{model_spec}'."}

    base_url = url.replace("/chat/completions", "").replace("/v1/messages", "").rstrip("/")
    edits_url = base_url + "/images/edits"
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    payload = {
        "model": model_id,
        "prompt": prompt,
        "n": "1",
        "size": size,
        "quality": quality if quality in ("low", "medium", "high", "auto") else "medium",
        "response_format": "b64_json",
    }
    request_id = uuid.uuid4().hex
    payload["request_id"] = request_id

    logger.info("Image edit: model=%s, size=%s, quality=%s, image=%s, prompt=%s", model_id, size, quality, path.name, prompt[:80])

    def _save_edited_image_to_gallery(filename: str) -> str:
        try:
            from src.database import SessionLocal as _GallerySL, GalleryImage
            new_id = str(uuid.uuid4())
            _gdb = _GallerySL()
            _gdb.add(GalleryImage(
                id=new_id,
                filename=filename,
                prompt=prompt,
                model=model_id,
                size=size,
                quality=payload.get("quality", "medium"),
                session_id=session_id,
                owner=owner,
            ))
            _gdb.commit()
            _gdb.close()
            return new_id
        except Exception as _ge:
            logger.warning("Failed to save edited image gallery record: %s", _ge)
            return ""

    def _save_image_bytes(image_bytes: bytes, suffix: str = ".png") -> tuple[str, str]:
        img_dir = Path(GENERATED_IMAGES_DIR)
        img_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex[:12]}{suffix}"
        (img_dir / filename).write_bytes(image_bytes)
        return f"/api/generated-image/{filename}", _save_edited_image_to_gallery(filename)

    async def _try_local_img2img_fallback(client: httpx.AsyncClient) -> Optional[Dict[str, Any]]:
        """Try Odysseus' local diffusion img2img endpoint.

        Some self-hosted SD/SDXL endpoints expose text-to-image plus
        `/images/harmonize`/img2img, but not OpenAI's multipart
        `/images/edits`. For chat uploads ("image + prompt"), this gives the
        expected instruction-edit behavior instead of stopping at a 400.
        """
        harmonize_url = base_url + "/images/harmonize"
        try:
            image_bytes = path.read_bytes()
            image_b64 = base64.b64encode(image_bytes).decode()
            fallback_payload = {
                "image": image_b64,
                "prompt": prompt,
                "strength": 0.35,
                "steps": 0,
                "max_side": 1024,
            }
            if progress_callback:
                await progress_callback({
                    "status": "running",
                    "message": "Trying image-to-image fallback",
                    "step": 0,
                    "total": 0,
                })
            fallback_resp = await client.post(harmonize_url, json=fallback_payload, headers=headers)
            if fallback_resp.status_code == 404:
                return None
            if fallback_resp.status_code != 200:
                error_text = fallback_resp.text[:500]
                try:
                    err_json = fallback_resp.json()
                    error_text = err_json.get("detail") or err_json.get("error") or error_text
                except Exception:
                    pass
                return {"error": f"Image edit fallback failed ({fallback_resp.status_code}): {error_text}"}
            fallback_data = fallback_resp.json()
            image_b64 = fallback_data.get("image")
            if not image_b64:
                return {"error": "Image edit fallback returned no image"}
            image_url, image_id = _save_image_bytes(base64.b64decode(image_b64))
            return {
                "results": f"Edited image for: {prompt[:100]}",
                "image_url": image_url,
                "image_id": image_id,
                "image_prompt": prompt,
                "image_model": model_id,
                "image_size": size,
                "image_quality": payload.get("quality", "medium"),
                "edit_route": "img2img",
            }
        except httpx.TimeoutException:
            return {"error": "Image edit fallback timed out. The model may still be loading or overloaded."}
        except Exception as fallback_error:
            logger.warning("Image edit fallback failed: %s", fallback_error)
            return {"error": f"Image edit fallback error: {fallback_error}"}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=30.0, read=600.0, write=60.0, pool=30.0)) as client:
            progress_task = None
            if progress_callback:
                progress_url = base_url + f"/images/progress/{request_id}"

                async def _poll_progress():
                    last_sig = None
                    while True:
                        try:
                            pr = await client.get(progress_url, headers=headers, timeout=5.0)
                            if pr.status_code == 404:
                                return
                            if pr.status_code == 200:
                                data = pr.json()
                                sig = (data.get("status"), data.get("step"), data.get("total"), data.get("percent"))
                                if sig != last_sig:
                                    last_sig = sig
                                    await progress_callback(data)
                                if data.get("status") in {"done", "error"}:
                                    return
                        except Exception:
                            return
                        await asyncio.sleep(1)

                progress_task = asyncio.create_task(_poll_progress())
            try:
                with path.open("rb") as f:
                    files = {"image": (path.name, f, mime)}
                    resp = await client.post(edits_url, data=payload, files=files, headers=headers)
            finally:
                if progress_task:
                    progress_task.cancel()
                    try:
                        await progress_task
                    except asyncio.CancelledError:
                        pass

            if resp.status_code != 200:
                error_text = resp.text[:500]
                try:
                    err_json = resp.json()
                    err = err_json.get("error")
                    error_text = (
                        err.get("message", error_text)
                        if isinstance(err, dict)
                        else str(err or err_json.get("detail") or error_text)
                    )
                except Exception:
                    pass
                if resp.status_code in (400, 404, 405, 422):
                    fallback = await _try_local_img2img_fallback(client)
                    if fallback:
                        return fallback
                    if resp.status_code == 404:
                        return {
                            "error": (
                                f"Image model '{model_id}' is reachable, but this endpoint does not expose image editing. "
                                "Use it without an attached image for text-to-image generation, or serve an edit/img2img "
                                "model for attached-image prompts."
                            )
                        }
                return {"error": f"Image edit failed ({resp.status_code}): {error_text}"}

            data = resp.json()
            images = data.get("data", [])
            if not images:
                return {"error": "No image returned from edit API"}

            img = images[0]
            image_url = None
            image_id = None

            if img.get("b64_json"):
                image_url, image_id = _save_image_bytes(base64.b64decode(img.get("b64_json")))
            elif img.get("url"):
                result_url = img["url"]
                ok, reason = check_outbound_url(
                    result_url,
                    block_private=os.getenv("IMAGE_BLOCK_PRIVATE_IPS", "false").lower() == "true",
                )
                if not ok:
                    return {"error": f"Image edit API returned unsafe image URL: {reason}"}
                dl_resp = httpx.get(result_url, timeout=60)
                if dl_resp.status_code != 200:
                    return {"error": f"Could not download edited image ({dl_resp.status_code})"}
                image_url, image_id = _save_image_bytes(dl_resp.content)
            else:
                return {"error": "Image edit API returned unexpected format (no b64_json or url)"}

            return {
                "results": f"Edited image for: {prompt[:100]}",
                "image_url": image_url,
                "image_id": image_id,
                "image_prompt": prompt,
                "image_model": model_id,
                "image_size": size,
                "image_quality": payload.get("quality", "medium"),
            }
    except httpx.TimeoutException:
        return {"error": "Image edit timed out. The model may still be loading or overloaded."}
    except Exception as e:
        return {"error": f"Image edit error: {str(e)}"}


# ---------------------------------------------------------------------------
# Dispatcher (called from agent_tools.execute_tool_block)
# ---------------------------------------------------------------------------

async def dispatch_ai_tool(
    tool: str, content: str, session_id: Optional[str] = None, owner: Optional[str] = None
) -> Tuple[str, Dict]:
    """Dispatch an AI interaction tool. Returns (description, result_dict)."""

    if tool == "pipeline":
        desc = "pipeline: running steps"
        result = await do_pipeline(content, session_id, owner=owner)

    elif tool == "manage_memory":
        action = content.split("\n")[0].strip()[:40]
        desc = f"manage_memory: {action}"
        result = await do_manage_memory(content, session_id, owner=owner)

    elif tool == "ui_control":
        action = content.split("\n")[0].strip()[:60]
        desc = f"ui_control: {action}"
        result = await do_ui_control(content, session_id, owner=owner)

    else:
        desc = f"unknown ai tool: {tool}"
        result = {"error": f"Unknown AI interaction tool: {tool}"}

    return desc, result
