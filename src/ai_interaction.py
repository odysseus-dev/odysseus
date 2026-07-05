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
from typing import Dict, Optional, Tuple

from src.constants import GENERATED_IMAGES_DIR

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


def _resolve_model(spec: str, owner: Optional[str] = None) -> Tuple[str, str, Dict]:
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

    db = SessionLocal()
    try:
        query = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
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
                try:
                    models_url = build_models_url(base)
                    if models_url:
                        r = httpx.get(models_url, headers=headers, timeout=5)
                        r.raise_for_status()
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
                        model_ids = json.loads(ep.cached_models or "[]")
                except Exception:
                    model_ids = []

                # Exact match first
                for mid in model_ids:
                    if mid.lower() == model_name.lower():
                        return build_chat_url(base), mid, headers

                # Partial match
                for mid in model_ids:
                    if model_name.lower() in mid.lower() or mid.lower() in model_name.lower():
                        return build_chat_url(base), mid, headers

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
        memories = _memory_manager.load_all()
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
# List models tool
# ---------------------------------------------------------------------------

async def do_list_models(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """List all available models across configured endpoints.

    Content = optional filter keyword.
    """
    import httpx
    from src.database import SessionLocal, ModelEndpoint
    from src.llm_core import _detect_provider, ANTHROPIC_MODELS
    from src.auth_helpers import owner_filter

    keyword = content.strip().lower() if content.strip() else None

    db = SessionLocal()
    try:
        query = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
        if owner:
            query = owner_filter(query, ModelEndpoint, owner)
        endpoints = query.all()
        if not endpoints:
            return {"results": "No enabled model endpoints configured."}

        result_lines = []
        total_models = 0

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
                base, api_key = resolve_endpoint_runtime(ep, owner=owner)
            except Exception:
                continue
            provider = _detect_provider(base)
            headers = build_headers(api_key, base)

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

    # Resolve the model to find the right endpoint
    try:
        url, model_id, headers = await asyncio.to_thread(_resolve_model, model_spec, owner=owner)
    except ValueError:
        return {"error": f"No endpoint found with image model '{model_spec}'. "
                "Configure an OpenAI-compatible endpoint with image generation support."}

    # Detect if this is a GPT image model vs DALL-E vs local diffusion
    is_gpt_image = "gpt-image" in model_id.lower()
    is_dalle = "dall-e" in model_id.lower()
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

            if model_ids:
                result_lines.append(f"\n**{ep.name or base}** ({provider}):")
                for mid in model_ids:
                    result_lines.append(f"  - `{mid}`")
                    total_models += 1

        if not result_lines:
            return {"results": "No models found" + (f" matching '{keyword}'" if keyword else "") + "."}

        header = f"Available models ({total_models} total):"
        return {"results": header + "\n".join(result_lines)}
    except Exception as e:
        logger.error(f"list_models failed: {e}")
        return {"error": str(e)}
    finally:
        db.close()




# ---------------------------------------------------------------------------
# Dispatcher (called from agent_tools.execute_tool_block)
# ---------------------------------------------------------------------------

async def dispatch_ai_tool(
    tool: str, content: str, session_id: Optional[str] = None, owner: Optional[str] = None
) -> Tuple[str, Dict]:
    """Dispatch an AI interaction tool. Returns (description, result_dict).

    Delegates to TOOL_HANDLERS (registry-based dispatch, #3629).  Kept as a
    convenience wrapper used by stream_ai_tool and for backward compatibility.
    """
    from src.agent_tools import TOOL_HANDLERS

    if tool in TOOL_HANDLERS:
        result = await TOOL_HANDLERS[tool](content, {"session_id": session_id, "owner": owner})
        first_line = (content or "").split("\n")[0].strip()[:60]
        desc = f"{tool}: {first_line}" if first_line else tool
        return desc, result

    desc = f"unknown ai tool: {tool}"
    result = {"error": f"Unknown AI interaction tool: {tool}"}
    return desc, result
