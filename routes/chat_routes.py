"""Chat routes — /api/chat, /api/chat_stream, /api/inject_context, /api/search."""

import asyncio
import json
import time
import logging
from datetime import datetime
from typing import Dict, Any, AsyncGenerator, List

from fastapi import APIRouter, Request, HTTPException, Form, Query
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from core.models import ChatMessage
from src.request_models import ChatRequest
from src.llm_core import llm_call_async, stream_llm, stream_llm_with_fallback
from src.agent_loop import stream_agent_loop
from src import agent_runs
from src.model_context import estimate_tokens
from src.chat_helpers import coerce_message_and_session
from src.endpoint_resolver import normalize_base as _normalize_base, build_chat_url
from src.prompt_security import untrusted_context_message
from core.exceptions import SessionNotFoundError
from src.auth_helpers import get_current_user
from routes.session_routes import _verify_session_owner
from core.database import SessionLocal, get_session_mode, set_session_mode
from core.database import Session as DBSession, ChatMessage as DBChatMessage
from core.database import Document as DBDocument, ModelEndpoint
from routes.research_routes import _resolve_research_endpoint
from routes.chat_helpers import (
    resolve_session_auth,
    build_chat_context,
    save_assistant_response,
    run_post_response_tasks,
    clean_thinking_for_save,
    _enforce_chat_privileges,
)
from src.action_intents import message_needs_tools as _message_needs_tools

logger = logging.getLogger(__name__)

# Track active streams for partial-save safety net
_active_streams: Dict[str, dict] = {}


def _stream_set(session_id: str, **fields) -> None:
    """Update fields on the active-stream entry for `session_id`, or
    no-op if the entry has already been popped. Using .get() avoids a
    KeyError race between `if x in d` and `d[x]["k"] = v` if a sibling
    finally pops the key in between (which becomes possible the moment
    a coroutine cancellation reaches an inner cleanup before the
    outermost cleanup runs)."""
    rec = _active_streams.get(session_id)
    if rec is None:
        return
    rec.update(fields)


def _session_url_matches_endpoint(session_url: str, endpoint_base: str) -> bool:
    if not session_url or not endpoint_base:
        return False
    sess = session_url.rstrip("/")
    base = _normalize_base(endpoint_base).rstrip("/")
    variants = {
        base,
        base + "/chat/completions",
        build_chat_url(base).rstrip("/"),
    }
    return sess in variants or sess.startswith(base + "/")


def _clear_orphaned_session_endpoint(sess) -> bool:
    """Clear a session model if its endpoint was deleted from ModelEndpoint."""
    if not getattr(sess, "endpoint_url", ""):
        return False
    db = SessionLocal()
    try:
        endpoints = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all()
        for ep in endpoints:
            if _session_url_matches_endpoint(sess.endpoint_url or "", ep.base_url or ""):
                return False
        db_session = db.query(DBSession).filter(DBSession.id == sess.id).first()
        if db_session:
            db_session.endpoint_url = ""
            db_session.model = ""
            db_session.updated_at = datetime.utcnow()
            db.commit()
        sess.endpoint_url = ""
        sess.model = ""
        sess.headers = {}
        return True
    except Exception:
        db.rollback()
        return False
    finally:
        db.close()


def setup_chat_routes(
    session_manager,
    chat_handler,
    chat_processor,
    memory_manager,
    research_handler,
    upload_handler,
    memory_vector=None,
    webhook_manager=None,
    skills_manager=None,
) -> APIRouter:
    router = APIRouter(tags=["chat"])

    # ------------------------------------------------------------------ #
    # POST /api/chat (non-streaming)
    # ------------------------------------------------------------------ #
    @router.post("/api/chat", response_model=Dict[str, str])
    async def chat_endpoint(request: Request, chat_request: ChatRequest) -> Dict[str, str]:
        message = chat_request.message
        session = chat_request.session
        att_ids = chat_request.attachments or []
        use_web = chat_request.use_web
        use_research = chat_request.use_research
        time_filter = chat_request.time_filter
        preset_id = chat_request.preset_id

        # Verify the caller owns this session before loading it.
        # Without this, any authenticated user can post into another user's chat.
        _verify_session_owner(request, session)

        try:
            sess = session_manager.get_session(session)
        except KeyError:
            raise HTTPException(404, f"Session '{session}' not found")
        if _clear_orphaned_session_endpoint(sess):
            raise HTTPException(400, "Selected model endpoint was removed. Pick another model in Settings.")

        # Same allowed_models + daily-cap gate as chat_stream (mirror so the
        # non-streaming path can't be used to bypass).
        _enforce_chat_privileges(request, sess)

        # Inline memory command
        memory_response = await chat_handler.handle_memory_command(sess, message)
        if memory_response:
            return {"response": memory_response}

        # Build shared context (preset, preprocess, preface, compact)
        ctx = await build_chat_context(
            sess, request, chat_handler, chat_processor,
            message=message,
            session_id=session,
            preset_id=preset_id,
            att_ids=att_ids,
            use_web=use_web,
            time_filter=time_filter,
            webhook_manager=webhook_manager,
        )

        # Research injection
        if use_research:
            try:
                _r_ep, _r_model, _r_headers = _resolve_research_endpoint(sess)
                research_ctx = await research_handler.call_research_service(
                    message, _r_ep, _r_model, llm_headers=_r_headers
                )
                ctx.messages.insert(
                    len(ctx.preface),
                    untrusted_context_message("research context", research_ctx),
                )
            except Exception as e:
                logger.error(f"Research failed: {e}")

        reply = await llm_call_async(
            sess.endpoint_url,
            sess.model,
            ctx.messages,
            headers=sess.headers,
            temperature=ctx.preset.temperature,
            max_tokens=ctx.preset.max_tokens,
            prompt_type=preset_id,
        )
        _clean_reply, _clean_md = clean_thinking_for_save(reply, {"model": sess.model})
        sess.add_message(ChatMessage("assistant", _clean_reply, metadata=_clean_md))

        from core.database import update_session_last_accessed
        update_session_last_accessed(session)
        session_manager.save_sessions()

        # Background tasks (memory, webhook, auto-name)
        run_post_response_tasks(
            sess, session_manager, session, message, reply, None,
            ctx.uprefs, memory_manager, memory_vector, webhook_manager,
            character_name=ctx.preset.character_name,
            owner=ctx.user,
        )

        return {"response": reply}

    # ------------------------------------------------------------------ #
    # POST /api/chat_stream
    # ------------------------------------------------------------------ #
    @router.post("/api/chat_stream")
    async def chat_stream(request: Request) -> StreamingResponse:
        body = None
        try:
            if request.headers.get("content-type", "").startswith("application/json"):
                try:
                    body = await request.json()
                except json.JSONDecodeError as e:
                    raise HTTPException(400, f"Invalid JSON: {e}")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, f"Request parsing error: {e}")

        # Stash the user's UTC offset (in minutes east of UTC) from the
        # frontend so tools like manage_notes interpret natural-language
        # times in the USER's tz, not the server's. See calendar_routes.
        try:
            _tz_hdr = request.headers.get("x-tz-offset")
            if _tz_hdr is not None:
                from routes.calendar_routes import set_user_tz_offset
                set_user_tz_offset(_tz_hdr)
        except Exception:
            pass

        form_data = await request.form()
        message = form_data.get("message")
        session = form_data.get("session")
        attachments = form_data.get("attachments")
        use_web = form_data.get("use_web")
        use_research = form_data.get("use_research")
        time_filter = form_data.get("time_filter")
        preset_id = form_data.get("preset_id")
        allow_bash = form_data.get("allow_bash")
        allow_web_search = form_data.get("allow_web_search")
        use_rag = form_data.get("use_rag")
        search_context = form_data.get("search_context")  # pre-fetched web search results (compare mode)
        compare_mode = str(form_data.get("compare_mode", "")).lower() == "true"
        incognito = str(form_data.get("incognito", "")).lower() == "true"
        chat_mode = str(form_data.get("mode", "")).lower()  # 'chat' or 'agent'
        # Did the USER explicitly pick agent mode? (vs. us auto-escalating
        # below). Skill extraction should only learn from real agent sessions,
        # not chats we quietly promoted for a notes/calendar intent.
        user_requested_agent = (chat_mode == "agent")
        # Intent auto-escalation: if the user is clearly asking the assistant
        # to create a todo, reminder, or calendar event, promote chat → agent
        # for this turn so the LLM has access to manage_notes / manage_calendar.
        # This is a LIGHT promotion — see the disabled_tools block below, which
        # withholds shell/code/file tools so the model doesn't try to `bash`
        # its way through a plain chat request (and fail, especially with the
        # shell disabled).
        auto_escalated = False
        if chat_mode == "chat" and isinstance(message, str) and _message_needs_tools(message):
            chat_mode = "agent"
            auto_escalated = True
            logger.info("chat→agent auto-escalation: message matched tool-intent pattern")
        active_doc_id = form_data.get("active_doc_id", "").strip()
        logger.info(f"[doc-inject] chat_mode={chat_mode}, active_doc_id={active_doc_id!r}")

        try:
            # Attachment-only sends: skip the message-required check when the
            # user has attached one or more files (the attachment IS the action).
            _has_atts = (
                bool(body and isinstance(body.get("attachments"), list) and body["attachments"])
                or bool(form_data.get("attachments"))
            )
            message, session = coerce_message_and_session(
                body, message, session, session_manager, allow_empty=_has_atts,
            )
            # Verify ownership AFTER coerce (which may resolve a default session)
            # but BEFORE loading. Prevents cross-user session hijack.
            _verify_session_owner(request, session)
            sess = session_manager.get_session(session)
            if _clear_orphaned_session_endpoint(sess):
                raise HTTPException(400, "Selected model endpoint was removed. Pick another model in Settings.")
        except SessionNotFoundError as e:
            raise HTTPException(404, str(e))
        except (ValueError, ValidationError):
            raise HTTPException(400, "Invalid request parameters")

        # ------------------------------------------------------------------ #
        # Privilege gates that must fire BEFORE any LLM work / token spend.
        #   1. allowed_models — reject if session.model isn't in the user's
        #      configured allowlist (empty list = "no restriction").
        #   2. max_messages_per_day — count user-role ChatMessage rows owned
        #      by this user in the last UTC day; 429 if at/over the cap.
        # Admins always have full privileges via get_privileges (returns
        # ADMIN_PRIVILEGES wholesale) so this is a no-op for them.
        _enforce_chat_privileges(request, sess)

        # Ensure session has auth headers
        resolve_session_auth(sess, session)

        # Check for research_pending BEFORE mode persist overwrites it
        do_research = str(use_research).lower() == "true"
        if not do_research:
            if get_session_mode(session) == 'research_pending':
                do_research = True
                logger.info(f"Session {session} in research_pending — auto-triggering research")

        # Persist session mode (research > agent > chat)
        _effective_mode = 'research' if do_research else (chat_mode or 'chat')
        if _effective_mode in ('agent', 'research', 'chat'):
            set_session_mode(session, _effective_mode)

        att_ids = []
        if body and isinstance(body.get("attachments"), list):
            att_ids = [str(x) for x in body["attachments"]]
        elif attachments:
            try:
                att_ids = [str(x) for x in json.loads(attachments)]
            except Exception:
                pass

        no_memory = str(form_data.get("no_memory", "")).lower() == "true"

        # Build shared context (stream path uses enhanced_message for context preface)
        ctx = await build_chat_context(
            sess, request, chat_handler, chat_processor,
            message=message,
            session_id=session,
            preset_id=preset_id,
            att_ids=att_ids,
            use_web=use_web,
            use_rag=use_rag,
            time_filter=time_filter,
            incognito=incognito,
            no_memory=no_memory,
            search_context=search_context,
            compare_mode=compare_mode,
            webhook_manager=webhook_manager,
            use_enhanced_message=True,
            # Skills index only ships when the model can actually call
            # manage_skills (agent mode). In plain chat or incognito the
            # index would be useless / unwanted noise.
            agent_mode=(chat_mode == "agent"),
        )

        _research_flags = {"do": do_research}  # Mutable container for generator scope

        # Query active document — prefer explicit ID from frontend, fall back to session lookup
        active_doc = None
        _doc_db = SessionLocal()
        try:
            if active_doc_id:
                logger.info(f"[doc-inject] active_doc_id from frontend: {active_doc_id}")
                active_doc = _doc_db.query(DBDocument).filter(
                    DBDocument.id == active_doc_id,
                ).first()
                if active_doc:
                    logger.info(f"[doc-inject] found by ID: title={active_doc.title!r}, lang={active_doc.language!r}, is_active={active_doc.is_active}, content_len={len(active_doc.current_content or '')}")
                else:
                    logger.warning(f"[doc-inject] NOT FOUND by ID {active_doc_id}")
            if not active_doc:
                active_doc = _doc_db.query(DBDocument).filter(
                    DBDocument.session_id == session,
                    DBDocument.is_active == True
                ).order_by(DBDocument.updated_at.desc()).first()
                if active_doc:
                    logger.info(f"[doc-inject] found by session fallback: title={active_doc.title!r}")
            # Last resort: the document the agent itself just created/edited
            # (tracked in-memory by the tool layer). This rescues docs that
            # got orphaned from their session (session_id NULL) — otherwise
            # neither lookup above can associate them with this conversation,
            # so the agent never sees what it just wrote. Guarded so we never
            # leak a doc that belongs to a DIFFERENT session.
            if not active_doc:
                try:
                    from src.tool_implementations import get_active_document
                    _mem_id = get_active_document()
                    if _mem_id:
                        cand = _doc_db.query(DBDocument).filter(DBDocument.id == _mem_id).first()
                        if cand and (not cand.session_id or cand.session_id == session):
                            active_doc = cand
                            logger.info(f"[doc-inject] found by in-memory active id: title={active_doc.title!r} (session_id={cand.session_id!r})")
                except Exception as _e:
                    logger.debug(f"[doc-inject] in-memory fallback failed: {_e}")
            if not active_doc:
                logger.info(f"[doc-inject] no active doc for session {session}")
            if active_doc:
                _doc_db.expunge(active_doc)
        except Exception as e:
            logger.warning(f"Failed to query active document: {e}")
        finally:
            _doc_db.close()

        # Build disabled-tools set from frontend toggles + user privileges
        disabled_tools = set()
        if str(allow_bash).lower() != "true":
            disabled_tools.add("bash")
        if str(allow_web_search).lower() != "true":
            disabled_tools.add("web_search")
            disabled_tools.add("web_fetch")

        # Nobody/incognito mode: deny tools that would expose the user's
        # persistent memory, past chats, or other identity-linked data.
        if incognito:
            disabled_tools.update({
                "manage_memory",      # persistent memory store
                "search_chats",       # past chat history
                "manage_skills",      # skill presets tied to user
            })

        # Enforce per-user privileges
        _privs = {}
        _user = ctx.user
        if _user and hasattr(request.app.state, 'auth_manager') and request.app.state.auth_manager:
            _privs = request.app.state.auth_manager.get_privileges(_user)
        if _privs:
            if not _privs.get("can_use_bash", True):
                disabled_tools.update({"bash", "python", "read_file", "write_file"})
            if not _privs.get("can_use_browser", True):
                disabled_tools.add("builtin_browser")
            if not _privs.get("can_use_documents", True):
                disabled_tools.update({"create_document", "edit_document", "update_document", "suggest_document"})
            if not _privs.get("can_generate_images", True):
                disabled_tools.add("generate_image")
            if not _privs.get("can_manage_memory", True):
                disabled_tools.update({"manage_memory", "manage_skills"})
            if not _privs.get("can_use_research", True):
                _research_flags["do"] = False
            if not _privs.get("can_use_agent", True):
                _effective_mode = 'chat'
                chat_mode = 'chat'
        # Global admin disabled tools
        from src.settings import get_setting
        _global_disabled = get_setting("disabled_tools", [])
        if _global_disabled and isinstance(_global_disabled, list):
            disabled_tools.update(_global_disabled)

        # Light auto-escalation: the user is in chat mode and just expressed a
        # notes/calendar/email intent. Grant the relevant managers but withhold
        # the heavy "do things on the computer" tools — otherwise the model
        # tries to shell out for a request that never needed it, then fails
        # (and looks broken when the shell is disabled).
        if auto_escalated:
            disabled_tools.update({
                "bash", "python", "read_file", "write_file", "builtin_browser",
            })

        # Disable document tools in compare sessions — they break the pane UI
        if sess.name and sess.name.startswith("[CMP]"):
            disabled_tools.update({"create_document", "edit_document", "update_document"})

        # Compare mode: disable tools based on compare type
        if compare_mode:
            _compare_strip = {
                "create_document", "edit_document", "update_document",
                "chat_with_model", "create_session", "list_sessions",
                "send_to_session",
                "pipeline", "manage_session", "manage_memory", "list_models",
                "generate_image", "ui_control",
            }
            disabled_tools.update(_compare_strip)
            # In chat mode compare, disable ALL agent tools (no bash, python, file ops)
            if chat_mode == 'chat':
                disabled_tools.update({"bash", "python", "read_file", "write_file", "web_search", "web_fetch", "search_chats", "manage_tasks"})

        async def stream_with_save() -> AsyncGenerator[str, None]:
            # _effective_mode is read-only here; closure captures it from
            # the outer scope. (Was `nonlocal` but never reassigned.)
            research_sources = None
            web_sources = ctx.web_sources

            # Register active stream for partial-save safety net
            _active_streams[session] = {"status": "streaming", "partial": "", "query": message, "is_research": do_research, "mode": _effective_mode}

            if ctx.preprocessed.attachment_meta:
                yield f"data: {json.dumps({'type': 'attachments', 'data': ctx.preprocessed.attachment_meta})}\n\n"

            # Announce any docs auto-created during preprocess (e.g. fillable
            # PDF → editable markdown) so the editor pane switches to them
            # before the model starts streaming.
            for _opened in ctx.auto_opened_docs:
                yield (
                    f'data: {json.dumps({"type": "doc_update", **_opened})}\n\n'
                )

            if ctx.rag_sources:
                yield f"data: {json.dumps({'type': 'rag_sources', 'data': ctx.rag_sources})}\n\n"

            if web_sources:
                yield f"data: {json.dumps({'type': 'web_sources', 'data': web_sources})}\n\n"

            # Emit which memories were injected into context (captured before stream)
            if ctx.used_memories:
                yield f"data: {json.dumps({'type': 'memories_used', 'data': ctx.used_memories})}\n\n"

            # Run research as a background task (survives page refresh)
            if do_research and _research_flags["do"]:
                _r_ep, _r_model, _r_headers = _resolve_research_endpoint(sess)
                _auth_keys = list(_r_headers.keys()) if _r_headers else []
                logger.info(f"Research endpoint resolved: model={_r_model}, endpoint={_r_ep}, auth_keys={_auth_keys}, sess_headers_keys={list(sess.headers.keys()) if isinstance(sess.headers, dict) else type(sess.headers)}")

                # Clarification round: only for very short/vague queries on first research message.
                # Skip in compare mode — each pane is a fresh session, so every one would
                # ask clarifying questions and the user would have to answer each pane
                # separately, breaking the parallel comparison.
                _prior_json = research_handler._get_session_json(session)
                _history_len = len(sess.history) if hasattr(sess, 'history') else 0
                _is_first_research = not _prior_json and _history_len <= 2 and not compare_mode

                if _is_first_research:
                    logger.info(f"First research message — asking clarifying questions for: {message[:60]}")
                    yield f'data: {json.dumps({"type": "model_info", "model": sess.model, "suffix": "Research"})}\n\n'
                    # Set DB mode to research_pending so the NEXT message auto-triggers research
                    set_session_mode(session, "research_pending")
                    ctx.messages.insert(0, {"role": "system", "content":
                        "The user wants to start deep web research. Before searching, ask 2-3 brief "
                        "clarifying questions to understand exactly what they want to know. For example: "
                        "what aspects matter most, are they comparing to something, what's their context "
                        "(moving, traveling, curiosity). Be conversational. Keep it short."
                    })
                    _skip_research = True
                else:
                    _skip_research = False

                if not _skip_research:
                    # Phase 2: Start actual research
                    def _on_research_done(_sid, _result, _sources, _findings):
                        """Persist research to DB when background task finishes."""
                        if incognito:
                            return
                        try:
                            _s = session_manager.get_session(_sid)
                            if not _s:
                                logger.warning(f"Session {_sid} expired before research completed")
                                return
                            _md = {"research": True, "model": _s.model}
                            if _sources:
                                _md["research_sources"] = _sources
                            if _findings:
                                _md["research_findings"] = _findings
                            _clean_res, _md = clean_thinking_for_save(_result, _md)
                            _s.add_message(ChatMessage("assistant", _clean_res, metadata=_md))
                            session_manager.save_sessions()
                            logger.info(f"Research result persisted to DB for session {_sid}")
                        except Exception as _e:
                            logger.error(f"Failed to persist research to DB: {_e}")

                    # Check for prior research to continue from
                    _prior_report = ""
                    _prior_findings = None
                    _prior_urls = None
                    _prior_json = research_handler._get_session_json(session)
                    if _prior_json:
                        _prior_report = _prior_json.get("raw_report", "")
                        _prior_findings = _prior_json.get("raw_findings")
                        _src_urls = {s.get("url", "") for s in (_prior_json.get("sources") or []) if s.get("url")}
                        _prior_urls = _src_urls if _src_urls else None
                        if _prior_report:
                            logger.info(f"Continuing research for session {session} with {len(_src_urls)} prior URLs")

                    # Synthesize conversation into a focused research query
                    _research_query = await research_handler.synthesize_query(
                        sess, message, _r_ep, _r_model, _r_headers,
                    )
                    logger.info(f"Research query: {_research_query[:120]}")

                    research_handler.start_research(
                        session, _research_query, _r_ep, _r_model,
                        llm_headers=_r_headers,
                        prior_report=_prior_report,
                        prior_findings=_prior_findings,
                        prior_urls=_prior_urls,
                        on_complete=_on_research_done,
                    )

                    _heartbeat_counter = 0
                    _last_progress = {}
                    _sent_avg = False
                    while True:
                        status = research_handler.get_status(session)
                        if not status or status["status"] != "running":
                            break
                        progress = status.get("progress", {})
                        if progress and progress != _last_progress:
                            _last_progress = progress
                            if not _sent_avg:
                                _sent_avg = True
                                progress = dict(progress)
                                progress["started_at"] = status.get("started_at")
                                avg = status.get("avg_duration")
                                if avg:
                                    progress["avg_duration"] = avg
                            yield f"data: {json.dumps({'type': 'research_progress', 'data': progress})}\n\n"
                            _heartbeat_counter = 0
                        else:
                            _heartbeat_counter += 1
                            yield f": heartbeat {_heartbeat_counter}\n\n"
                        await asyncio.sleep(1.0)

                    research_sources = research_handler.get_sources(session)
                    if research_sources:
                        yield f"data: {json.dumps({'type': 'research_sources', 'data': research_sources})}\n\n"

                    research_findings = research_handler.get_raw_findings(session)
                    if research_findings:
                        yield f"data: {json.dumps({'type': 'research_findings', 'data': research_findings})}\n\n"

                    # Signal frontend to fetch and render the research result
                    yield f"data: {json.dumps({'type': 'research_done', 'data': {'session_id': session}})}\n\n"
                    yield "data: [DONE]\n\n"
                    research_handler.clear_result(session)
                    _stream_set(session, status="done")
                    _active_streams.pop(session, None)
                    return

            messages = ctx.messages

            # Auto-compact notification
            if ctx.was_compacted:
                yield f"data: {json.dumps({'type': 'compacted', 'context_length': ctx.context_length})}\n\n"

            full_response = ""
            last_metrics = None

            # Configured fallback chain for the default chat model. Tried in
            # order if the session's primary model fails before producing
            # output. Resolved once per request.
            try:
                from src.endpoint_resolver import resolve_chat_fallback_candidates
                _fallback_candidates = resolve_chat_fallback_candidates()
            except Exception:
                _fallback_candidates = []

            # Send model name early so the frontend can show it during streaming
            _model_suffix = "Research" if do_research else None
            _model_info = {"type": "model_info", "model": sess.model}
            if _model_suffix:
                _model_info["suffix"] = _model_suffix
            if ctx.preset.character_name:
                _model_info["character_name"] = ctx.preset.character_name
            yield f'data: {json.dumps(_model_info)}\n\n'

            # Detect image models and route directly to image generation
            _IMAGE_MODEL_PREFIXES = ("gpt-image", "dall-e", "chatgpt-image")
            _is_image_model = any(sess.model.lower().startswith(p) for p in _IMAGE_MODEL_PREFIXES)

            # Also check if the endpoint is registered as an image-type endpoint
            if not _is_image_model:
                try:
                    from src.endpoint_resolver import normalize_base as _nb
                    _ep_base = _nb(sess.endpoint_url)
                    _db = SessionLocal()
                    try:
                        _is_image_model = _db.query(ModelEndpoint).filter(
                            ModelEndpoint.model_type == "image",
                            ModelEndpoint.is_enabled == True,
                            ModelEndpoint.base_url.contains(_ep_base.split("://")[-1].split("/")[0]),
                        ).first() is not None
                    finally:
                        _db.close()
                except Exception:
                    pass

            if _is_image_model:
                from src.settings import get_setting
                if not get_setting("image_gen_enabled", True):
                    yield f'data: {json.dumps({"delta": "Image generation is disabled by the administrator."})}\n\n'
                    yield "data: [DONE]\n\n"
                    _active_streams.pop(session, None)
                    return
                from src.ai_interaction import do_generate_image
                _user_msg = message or ""
                yield f'data: {json.dumps({"type": "tool_start", "tool": "generate_image", "command": _user_msg[:100]})}\n\n'
                yield ": heartbeat\n\n"
                _img_result = await do_generate_image(f"{_user_msg}\n{sess.model}", session)
                _img_output = _img_result.get("results", _img_result.get("error", ""))
                _img_tool_data = {"type": "tool_output", "tool": "generate_image", "command": _user_msg[:100], "output": _img_output, "exit_code": 0 if "error" not in _img_result else 1}
                for _k in ("image_url", "image_id", "image_prompt", "image_model", "image_size", "image_quality"):
                    if _k in _img_result:
                        _img_tool_data[_k] = _img_result[_k]
                yield f'data: {json.dumps(_img_tool_data)}\n\n'
                _desc = _img_result.get("results", _img_result.get("error", "Image generation complete"))
                full_response = _desc
                yield f'data: {json.dumps({"delta": _desc})}\n\n'
                # Save to session history
                if not incognito:
                    _ev = {"round": 1, "tool": "generate_image", "command": _user_msg[:100], "output": _img_output, "exit_code": 0 if "error" not in _img_result else 1}
                    for _ek in ("image_url", "image_id", "image_prompt", "image_model", "image_size", "image_quality"):
                        if _img_result.get(_ek):
                            _ev[_ek] = _img_result[_ek]
                    sess.add_message(ChatMessage("assistant", full_response, metadata={"tool_events": [_ev], "model": sess.model}))
                    session_manager.save_sessions()
                yield f'data: {json.dumps({"type": "metrics", "data": {"total_time": 0}})}\n\n'
                yield "data: [DONE]\n\n"
                _active_streams.pop(session, None)
                return
            elif chat_mode == "chat":
                _chat_start = time.time()
                # ── Chat mode: call stream_llm directly, NO tools, NO document access ──
                try:
                    _chat_candidates = [(sess.endpoint_url, sess.model, sess.headers)] + _fallback_candidates
                    async for chunk in stream_llm_with_fallback(
                        _chat_candidates,
                        messages,
                        temperature=ctx.preset.temperature,
                        # Respect the preset; 0/unset = let the server decide (no
                        # cap), matching agent mode. The old hard 4096 fallback
                        # truncated reasoning models mid-<think> — they'd burn the
                        # whole budget thinking and never emit the answer (seen in
                        # Compare on heavy generation prompts).
                        max_tokens=ctx.preset.max_tokens,
                        prompt_type=preset_id,
                        tools=None,
                    ):
                        if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
                            try:
                                data = json.loads(chunk[6:])
                                if "delta" in data:
                                    full_response += data["delta"]
                                    _stream_set(session, partial=full_response)
                                    yield chunk
                                elif data.get("type") == "usage":
                                    last_metrics = data.get("data", {})
                                    last_metrics["model"] = sess.model
                                    if ctx.context_length and last_metrics.get("input_tokens"):
                                        pct = min(round((last_metrics["input_tokens"] / ctx.context_length) * 100, 1), 100.0)
                                        last_metrics["context_percent"] = pct
                                        last_metrics["context_length"] = ctx.context_length
                                    yield f'data: {json.dumps({"type": "metrics", "data": last_metrics})}\n\n'
                            except json.JSONDecodeError:
                                yield chunk
                        elif chunk.startswith("event: error"):
                            logger.warning(f"Stream error for {sess.model} on {sess.endpoint_url}: {chunk!r}")
                            yield chunk
                        elif chunk.startswith("event: "):
                            yield chunk
                        elif chunk == "data: [DONE]\n\n":
                            # Generate fallback metrics if LLM didn't send usage
                            if not last_metrics and full_response:
                                _elapsed = time.time() - _chat_start
                                _est_in = estimate_tokens(messages)
                                _est_out = len(full_response) // 4
                                _tps = round(_est_out / _elapsed, 2) if _elapsed > 0 else 0
                                _ctx_pct = min(round((_est_in / ctx.context_length) * 100, 1), 100.0) if ctx.context_length else 0
                                last_metrics = {
                                    "response_time": round(_elapsed, 2),
                                    "input_tokens": _est_in,
                                    "output_tokens": _est_out,
                                    "tokens_per_second": _tps,
                                    "context_percent": _ctx_pct,
                                    "context_length": ctx.context_length,
                                    "model": sess.model,
                                    "usage_source": "estimated",
                                }
                                yield f'data: {json.dumps({"type": "metrics", "data": last_metrics})}\n\n'
                            if full_response:
                                _saved_id = save_assistant_response(
                                    sess, session_manager, session, full_response, last_metrics,
                                    character_name=ctx.preset.character_name,
                                    web_sources=web_sources,
                                    rag_sources=ctx.rag_sources,
                                    research_sources=research_sources,
                                    used_memories=ctx.used_memories,
                                    do_research=do_research,
                                    incognito=incognito,
                                )
                                if _saved_id:
                                    yield f'data: {json.dumps({"type": "message_saved", "id": _saved_id})}\n\n'
                                run_post_response_tasks(
                                    sess, session_manager, session, message, full_response,
                                    last_metrics, ctx.uprefs, memory_manager, memory_vector, webhook_manager,
                                    incognito=incognito, compare_mode=compare_mode,
                                    character_name=ctx.preset.character_name,
                                                            owner=_user,
                                )
                            _stream_set(session, status="done")
                            yield chunk
                except (asyncio.CancelledError, GeneratorExit):
                    if full_response:
                        logger.info("Client disconnected mid-stream (chat mode) for session %s, saving partial (%d chars)", session, len(full_response))
                        _stopped_content, _stopped_md = clean_thinking_for_save(full_response, {"stopped": True, "model": sess.model})
                        sess.add_message(ChatMessage("assistant", _stopped_content, metadata=_stopped_md))
                        if not incognito:
                            session_manager.save_sessions()
                    raise
                finally:
                    _active_streams.pop(session, None)
            else:
                # ── Agent mode: full agent loop with tools ──
                _agent_rounds = 0
                _agent_tool_calls = 0
                try:
                    from src.settings import get_setting
                    _tool_budget = int(get_setting("agent_max_tool_calls", 0))

                    async for chunk in stream_agent_loop(
                        sess.endpoint_url,
                        sess.model,
                        messages,
                        headers=sess.headers,
                        temperature=ctx.preset.temperature,
                        max_tokens=ctx.preset.max_tokens,
                        prompt_type=preset_id,
                        max_tool_calls=_tool_budget,
                        context_length=ctx.context_length,
                        active_document=active_doc,
                        session_id=session,
                        disabled_tools=disabled_tools if disabled_tools else None,
                        owner=_user,
                        fallbacks=_fallback_candidates,
                    ):
                        if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
                            try:
                                data = json.loads(chunk[6:])
                                if "delta" in data:
                                    full_response += data["delta"]
                                    _stream_set(session, partial=full_response)
                                    yield chunk
                                elif data.get("type") == "web_sources":
                                    web_sources = data.get("data", [])
                                    yield chunk
                                elif data.get("type") in (
                                    "tool_start", "tool_output", "agent_step",
                                    "doc_stream_open", "doc_stream_delta",
                                    "doc_update", "doc_suggestions", "ui_control",
                                ):
                                    if data.get("type") == "agent_step":
                                        _agent_rounds = max(_agent_rounds, data.get("round", 1))
                                    elif data.get("type") == "tool_start":
                                        _agent_tool_calls += 1
                                    yield chunk
                                elif data.get("type") == "metrics":
                                    last_metrics = data.get("data", {})
                                    last_metrics["model"] = sess.model
                                    yield f'data: {json.dumps({"type": "metrics", "data": last_metrics})}\n\n'
                            except json.JSONDecodeError:
                                yield chunk
                        elif chunk.startswith("event: "):
                            yield chunk
                        elif chunk == "data: [DONE]\n\n":
                            if full_response:
                                _saved_id = save_assistant_response(
                                    sess, session_manager, session, full_response, last_metrics,
                                    character_name=ctx.preset.character_name,
                                    web_sources=web_sources,
                                    rag_sources=ctx.rag_sources,
                                    used_memories=ctx.used_memories,
                                    incognito=incognito,
                                )
                                if _saved_id:
                                    yield f'data: {json.dumps({"type": "message_saved", "id": _saved_id})}\n\n'
                                run_post_response_tasks(
                                    sess, session_manager, session, message, full_response,
                                    last_metrics, ctx.uprefs, memory_manager, memory_vector, webhook_manager,
                                    incognito=incognito, compare_mode=compare_mode,
                                    character_name=ctx.preset.character_name,
                                                            agent_rounds=_agent_rounds,
                                    agent_tool_calls=_agent_tool_calls,
                                    skills_manager=skills_manager,
                                    owner=_user,
                                    extract_skills=user_requested_agent,
                                )
                            _stream_set(session, status="done")
                            yield chunk
                except (asyncio.CancelledError, GeneratorExit):
                    # Client disconnected — save partial response. Wrap
                    # the save in its own try so an exception inside
                    # add_message / save_sessions doesn't mask the
                    # original CancelledError (which prevented the
                    # outer finally from running and left _active_streams
                    # with a stale entry).
                    try:
                        if full_response:
                            logger.info("Client disconnected mid-stream for session %s, saving partial response (%d chars)", session, len(full_response))
                            _stopped_content2, _stopped_md2 = clean_thinking_for_save(full_response, {"stopped": True, "model": sess.model})
                            sess.add_message(ChatMessage("assistant", _stopped_content2, metadata=_stopped_md2))
                            if not incognito:
                                session_manager.save_sessions()
                    except Exception:
                        logger.exception("Failed to save partial response on disconnect (session %s)", session)
                    raise
                finally:
                    _active_streams.pop(session, None)

        async def _safe_stream() -> AsyncGenerator[str, None]:
            """Wrapper that guarantees _active_streams cleanup even if stream_with_save
            raises before reaching a mode-specific finally block."""
            try:
                async for chunk in stream_with_save():
                    yield chunk
            finally:
                _active_streams.pop(session, None)

        # Run the stream as a DETACHED background task so it survives the client
        # closing the tab / navigating away (true terminal-agent behavior). The
        # SSE response just subscribes (replay buffered output + live); dropping
        # the SSE only removes a subscriber — the run keeps going and saves the
        # assistant message on completion regardless. Reconnect via /api/chat/resume.
        agent_runs.start(session, _safe_stream())
        return StreamingResponse(agent_runs.subscribe(session), media_type="text/event-stream")

    # ------------------------------------------------------------------ #
    # GET /api/chat/resume — reconnect to a detached run that's still going
    # (e.g. after reopening a session whose agent kept running in the background)
    # ------------------------------------------------------------------ #
    @router.get("/api/chat/resume/{session_id}")
    async def chat_resume(request: Request, session_id: str) -> StreamingResponse:
        _verify_session_owner(request, session_id)
        if not agent_runs.is_active(session_id):
            raise HTTPException(404, "No active run for this session")
        return StreamingResponse(agent_runs.subscribe(session_id), media_type="text/event-stream")

    # ------------------------------------------------------------------ #
    # POST /api/chat/stop — cancel a detached run (Stop button). Closing the SSE
    # no longer stops it (it's detached), so the Stop button must call this.
    # ------------------------------------------------------------------ #
    @router.post("/api/chat/stop/{session_id}")
    async def chat_stop(request: Request, session_id: str) -> Dict[str, Any]:
        _verify_session_owner(request, session_id)
        stopped = agent_runs.stop(session_id)
        return {"stopped": stopped}

    # ------------------------------------------------------------------ #
    # GET /api/chat/stream_status — check if a stream is active for a session
    # ------------------------------------------------------------------ #
    @router.get("/api/chat/stream_status/{session_id}")
    async def chat_stream_status(request: Request, session_id: str) -> Dict[str, Any]:
        _verify_session_owner(request, session_id)
        # A detached run can still be going even if _active_streams was popped;
        # report it as active so the client knows to reconnect via /resume.
        # Read once via .get() to avoid a KeyError race between the membership
        # check and the indexed read if a sibling stream's finally pops the
        # entry in between (same pattern _stream_set already uses).
        rec = _active_streams.get(session_id)
        if rec is None:
            if agent_runs.is_active(session_id):
                return {"status": "streaming", "detached": True}
            raise HTTPException(404, "No active stream for this session")
        return rec

    # ------------------------------------------------------------------ #
    # POST /api/inject_context
    # ------------------------------------------------------------------ #
    @router.post("/api/inject_context/{session_id}")
    async def inject_context(request: Request, session_id: str, context: str = Form(...)) -> Dict[str, str]:
        _verify_session_owner(request, session_id)
        try:
            sess = session_manager.get_session(session_id)
            msg = untrusted_context_message("injected research context", f"Research Context: {context}")
            sess.add_message(ChatMessage(msg["role"], msg["content"], metadata=msg.get("metadata")))
            session_manager.save_sessions()
            return {"status": "context_injected"}
        except KeyError:
            raise HTTPException(404, "Session not found")

    # ------------------------------------------------------------------ #
    # GET /api/search — search across chat messages
    # ------------------------------------------------------------------ #
    @router.get("/api/search")
    async def search_messages(
        request: Request,
        q: str = Query("", min_length=0),
        limit: int = Query(20, ge=1, le=100),
    ) -> List[Dict[str, Any]]:
        if not q or not q.strip():
            return []

        _user = get_current_user(request)
        query_term = q.strip()
        db = SessionLocal()
        try:
            base_q = (
                db.query(DBChatMessage, DBSession.name)
                .join(DBSession, DBChatMessage.session_id == DBSession.id)
                .filter(
                    DBSession.archived == False,
                    DBChatMessage.content.ilike(f"%{query_term}%"),
                    DBChatMessage.role.in_(["user", "assistant"]),
                )
            )
            if _user:
                base_q = base_q.filter(DBSession.owner == _user)
            rows = base_q.order_by(DBChatMessage.timestamp.desc()).limit(limit).all()

            results = []
            for msg, session_name in rows:
                content = msg.content or ""
                lower_content = content.lower()
                idx = lower_content.find(query_term.lower())
                if idx == -1:
                    snippet = content[:120]
                else:
                    start = max(0, idx - 50)
                    end = min(len(content), idx + len(query_term) + 50)
                    snippet = ("..." if start > 0 else "") + content[start:end] + ("..." if end < len(content) else "")

                results.append({
                    "session_id": msg.session_id,
                    "session_name": session_name or "Untitled",
                    "role": msg.role,
                    "content_snippet": snippet,
                    "timestamp": msg.timestamp.isoformat() if msg.timestamp else None,
                })

            return results
        finally:
            db.close()

    # ------------------------------------------------------------------ #
    # POST /api/rewrite — lightweight rewrite of last AI message (no tools)
    # ------------------------------------------------------------------ #
    @router.post("/api/rewrite")
    async def rewrite_message(request: Request) -> StreamingResponse:
        """Rewrite the last AI message with an instruction (shorter/simpler/etc).

        Unlike the full chat pipeline, this does NOT run the agent loop or tools.
        It just asks the LLM to rewrite the given text.
        """
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Invalid JSON")

        session_id = body.get("session_id")
        original_text = body.get("original_text", "")
        instruction = body.get("instruction", "")

        if not session_id or not original_text or not instruction:
            raise HTTPException(400, "session_id, original_text, and instruction are required")

        _verify_session_owner(request, session_id)

        try:
            sess = session_manager.get_session(session_id)
        except (KeyError, SessionNotFoundError):
            raise HTTPException(404, "Session not found")

        messages = [
            {"role": "system", "content": (
                "You are rewriting a previous response. Follow the instruction exactly. "
                "Output ONLY the rewritten text — no preamble, no explanation, no meta-commentary. "
                "Preserve any formatting (markdown, code blocks, lists) from the original."
            )},
            {"role": "user", "content": (
                f"Here is the original response:\n\n{original_text}\n\n"
                f"Instruction: {instruction}"
            )},
        ]

        async def stream_rewrite() -> AsyncGenerator[str, None]:
            full_response = ""
            try:
                async for chunk in stream_llm(
                    sess.endpoint_url,
                    sess.model,
                    messages,
                    headers=sess.headers,
                    temperature=0.7,
                    # 0 = let the server decide (no cap). A hardcoded 4096 made
                    # local reasoning models (Qwen3 / R1) burn the whole budget
                    # inside <think> and emit no rewrite — the bubble just hung
                    # on "Rewriting...". Same fix as the chat max_tokens cap.
                    max_tokens=0,
                    tools=None,
                ):
                    if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
                        try:
                            data = json.loads(chunk[6:])
                            if "delta" in data:
                                # Forward the chunk (so the client can show a
                                # thinking indicator) but DON'T fold reasoning
                                # tokens into the saved rewrite — only real
                                # content. reasoning_content arrives flagged
                                # with thinking:true.
                                if not data.get("thinking"):
                                    full_response += data["delta"]
                                yield chunk
                        except json.JSONDecodeError:
                            yield chunk
                    elif chunk.startswith("event: "):
                        yield chunk
                    elif chunk == "data: [DONE]\n\n":
                        # Update the last assistant message in session history.
                        # Strip reasoning-model <think> blocks so the persisted
                        # rewrite is just the rewritten text, not its scratchpad.
                        from src.research_utils import strip_thinking
                        full_response = strip_thinking(full_response).strip() or full_response
                        if full_response:
                            for msg in reversed(sess.history):
                                if (isinstance(msg, ChatMessage) and msg.role == 'assistant') or \
                                   (isinstance(msg, dict) and msg.get('role') == 'assistant'):
                                    if isinstance(msg, ChatMessage):
                                        msg.content = full_response
                                    else:
                                        msg['content'] = full_response
                                    break
                            # Update in DB too
                            db = SessionLocal()
                            try:
                                db_msg = (
                                    db.query(DBChatMessage)
                                    .filter(DBChatMessage.session_id == session_id, DBChatMessage.role == 'assistant')
                                    .order_by(DBChatMessage.created_at.desc())
                                    .first()
                                )
                                if db_msg:
                                    db_msg.content = full_response
                                    db.commit()
                            except Exception as e:
                                logger.warning("Failed to update rewritten message in DB: %s", e)
                                db.rollback()
                            finally:
                                db.close()
                            session_manager.save_sessions()
                        yield chunk
            except Exception as e:
                logger.error("Rewrite stream error: %s", e)
                yield f'event: error\ndata: {json.dumps({"error": str(e), "status": 500})}\n\n'

        return StreamingResponse(stream_rewrite(), media_type="text/event-stream")

    return router
