"""
src/app_routers.py

Router registration extracted from app.py (P8-T12).

register_routers(app, deps) performs every app.include_router(...) call in the
ORIGINAL order (route precedence is significant), wiring the routers built from
pre-constructed singletons passed in via `deps`. A few objects are constructed
inside this block in the original module body (task_scheduler, mcp_manager,
stt_service) together with their set_* side effects — those are preserved here
verbatim and the ones the lifespan/shutdown need (task_scheduler, mcp_manager,
upload_cleanup_func/_task) are returned so app.py can wire the lifespan.

The inline @app.get HTML routes and @app.exception_handler handlers stay in
app.py (tightly bound to the app object); this module owns the include_router
block only.
"""

from types import SimpleNamespace

import logging

logger = logging.getLogger("app")


def register_routers(app, deps):
    # setup_auth_routes was imported earlier in app.py (outside the moved block);
    # import it here so the relocated registration resolves it.
    from routes.auth_routes import setup_auth_routes

    # Unpack pre-built singletons (constructed in app.py before this call).
    auth_manager = deps.auth_manager
    session_manager = deps.session_manager
    webhook_manager = deps.webhook_manager
    memory_manager = deps.memory_manager
    skills_manager = deps.skills_manager
    chat_handler = deps.chat_handler
    chat_processor = deps.chat_processor
    research_handler = deps.research_handler
    upload_handler = deps.upload_handler
    memory_vector = deps.memory_vector
    preset_manager = deps.preset_manager
    rag_manager = deps.rag_manager
    rag_available = deps.rag_available
    personal_docs_mgr = deps.personal_docs_mgr
    model_discovery = deps.model_discovery
    tts_service = deps.tts_service
    config = deps.config
    api_key_manager = deps.api_key_manager
    REQUEST_TIMEOUT = deps.REQUEST_TIMEOUT
    OPENAI_API_KEY = deps.OPENAI_API_KEY
    SESSIONS_FILE = deps.SESSIONS_FILE


    # Auth
    auth_router = setup_auth_routes(auth_manager)
    app.include_router(auth_router)

    # Uploads
    from routes.upload_routes import setup_upload_routes
    upload_router, upload_cleanup_func = setup_upload_routes(upload_handler)
    app.include_router(upload_router)
    upload_cleanup_task = None

    # Emoji SVG proxy (same-origin, lazy-cached Twemoji) — lets the chat render
    # emojis as flat SVG instead of system color glyphs.
    from routes.emoji_routes import setup_emoji_routes
    app.include_router(setup_emoji_routes())

    # Sessions
    from routes.session_routes import setup_session_routes
    session_config = {"REQUEST_TIMEOUT": REQUEST_TIMEOUT, "OPENAI_API_KEY": OPENAI_API_KEY, "SESSIONS_FILE": SESSIONS_FILE}
    app.include_router(setup_session_routes(session_manager, session_config, webhook_manager=webhook_manager))

    # Admin Danger Zone wipes (Settings → System → Danger Zone)
    from routes.admin_wipe_routes import setup_admin_wipe_routes
    app.include_router(setup_admin_wipe_routes(session_manager))

    # Memory
    from routes.memory_routes import setup_memory_routes
    memory_router = setup_memory_routes(memory_manager, session_manager, memory_vector=memory_vector)
    app.include_router(memory_router)
    from routes.skills_routes import setup_skills_routes
    app.include_router(setup_skills_routes(skills_manager))

    # Chat
    from routes.chat_routes import setup_chat_routes
    app.include_router(setup_chat_routes(
        session_manager, chat_handler, chat_processor,
        memory_manager, research_handler, upload_handler,
        memory_vector=memory_vector,
        webhook_manager=webhook_manager,
        skills_manager=skills_manager,
    ))

    # Research (background deep-research tasks)
    from routes.research_routes import setup_research_routes
    app.include_router(setup_research_routes(research_handler, session_manager=session_manager))

    # History
    from routes.history_routes import setup_history_routes
    app.include_router(setup_history_routes(session_manager))

    # Search
    from routes.search_routes import setup_search_routes
    app.include_router(setup_search_routes(config))

    # Presets
    from routes.preset_routes import setup_preset_routes
    app.include_router(setup_preset_routes(preset_manager))

    # Diagnostics
    from routes.diagnostics_routes import setup_diagnostics_routes
    app.include_router(setup_diagnostics_routes(rag_manager, rag_available, research_handler, memory_vector))

    # Cleanup
    from routes.cleanup_routes import setup_cleanup_routes
    app.include_router(setup_cleanup_routes(session_manager))

    # Personal docs
    from routes.personal_routes import setup_personal_routes
    app.include_router(setup_personal_routes(personal_docs_mgr, rag_manager, rag_available))

    # Embedding model management
    from routes.embedding_routes import setup_embedding_routes
    app.include_router(setup_embedding_routes())

    # Models
    from routes.model_routes import setup_model_routes
    app.include_router(setup_model_routes(model_discovery))

    # GitHub Copilot device-flow login
    from routes.copilot_routes import setup_copilot_routes
    app.include_router(setup_copilot_routes())

    # ChatGPT Subscription device-flow login
    from routes.chatgpt_subscription_routes import setup_chatgpt_subscription_routes
    app.include_router(setup_chatgpt_subscription_routes())

    # TTS
    from routes.tts_routes import setup_tts_routes
    app.include_router(setup_tts_routes(tts_service))

    # STT
    from services.stt import get_stt_service
    stt_service = get_stt_service()
    from routes.stt_routes import setup_stt_routes
    app.include_router(setup_stt_routes(stt_service))
    logger.info("STT service initialized (provider managed via settings)")

    # Documents (artifacts/canvas)
    from routes.document_routes import setup_document_routes
    document_router = setup_document_routes(session_manager, upload_handler)
    app.include_router(document_router)

    # Signatures (reusable image stamps)
    from routes.signature_routes import setup_signature_routes
    app.include_router(setup_signature_routes())

    # Gallery (image library)
    from routes.gallery_routes import setup_gallery_routes
    app.include_router(setup_gallery_routes())

    # Persisted image-editor drafts (server-backed projects)
    from routes.editor_draft_routes import setup_editor_draft_routes
    app.include_router(setup_editor_draft_routes())

    # Scheduled tasks + event bus
    from src.task_scheduler import TaskScheduler
    task_scheduler = TaskScheduler(session_manager)
    from src.event_bus import set_task_scheduler
    set_task_scheduler(task_scheduler)
    from routes.task_routes import setup_task_routes
    app.include_router(setup_task_routes(task_scheduler))

    from routes.assistant_routes import setup_assistant_routes
    app.include_router(setup_assistant_routes(task_scheduler))

    # Calendar (CalDAV)
    from routes.calendar_routes import setup_calendar_routes
    calendar_router = setup_calendar_routes()
    app.include_router(calendar_router)

    # Shell (user-facing command execution)
    from routes.shell_routes import setup_shell_routes
    app.include_router(setup_shell_routes())

    # Cookbook (model download/serve/cache, cookbook state sync)
    from routes.cookbook_routes import setup_cookbook_routes
    app.include_router(setup_cookbook_routes())

    # Hardware model fitting (cookbook "What Fits?" tab)
    from routes.hwfit_routes import setup_hwfit_routes
    app.include_router(setup_hwfit_routes())

    # Model A/B Comparison
    from routes.compare_routes import setup_compare_routes
    app.include_router(setup_compare_routes(session_manager))

    # User Preferences
    from routes.prefs_routes import setup_prefs_routes
    app.include_router(setup_prefs_routes())

    # Backup (export/import user data)
    from routes.backup_routes import setup_backup_routes
    app.include_router(setup_backup_routes(memory_manager, preset_manager, skills_manager))

    from routes.font_routes import setup_font_routes
    app.include_router(setup_font_routes())


    # MCP (Model Context Protocol)
    from src.mcp_manager import McpManager
    from src.agent_tools import set_mcp_manager, set_api_key_manager
    from routes.mcp_routes import setup_mcp_routes

    mcp_manager = McpManager()
    set_mcp_manager(mcp_manager)
    set_api_key_manager(api_key_manager)
    app.include_router(setup_mcp_routes(mcp_manager))
    logger.info("MCP routes initialized")

    # AI Interaction tools (debates, pipelines, self-managing AI, UI control)
    from src.ai_interaction import set_session_manager as set_ai_session_manager, set_memory_manager as set_ai_memory_manager, set_rag_manager as set_ai_rag_manager
    set_ai_session_manager(session_manager)
    set_ai_memory_manager(memory_manager, memory_vector)
    set_ai_rag_manager(rag_manager, personal_docs_mgr)
    logger.info("AI interaction tools initialized (session, memory, RAG, UI control)")

    # Webhooks
    from routes.webhook_routes import setup_webhook_routes
    app.include_router(setup_webhook_routes(webhook_manager, auth_manager, session_manager, api_key_manager))

    # API Tokens
    from routes.api_token_routes import setup_api_token_routes
    app.include_router(setup_api_token_routes())

    logger.info("Webhook & API token routes initialized")

    # Notes (Google Keep-style notes/todos)
    from routes.note_routes import setup_note_routes
    app.include_router(setup_note_routes(task_scheduler))

    # Email
    from routes.email_routes import setup_email_routes
    email_router = setup_email_routes()
    app.include_router(email_router)

    # Codex integration — HTTP surface for the Codex plugin/MCP bridge. Reuses
    # api_token scopes (todos:read|write, email:read|draft|send) so external
    # Codex sessions can only touch the data the user explicitly allowed. Mounted
    # AFTER email so the codex_routes can borrow the email router for shared
    # search/threading helpers.
    from routes.codex_routes import setup_codex_routes, setup_claude_routes
    codex_router, codex_compat_router = setup_codex_routes(
        email_router=email_router,
        memory_router=memory_router,
        calendar_router=calendar_router,
        document_router=document_router,
    )
    app.include_router(codex_router)
    app.include_router(codex_compat_router)
    app.include_router(setup_claude_routes())

    from routes.vault_routes import setup_vault_routes
    app.include_router(setup_vault_routes(api_key_manager))

    # Contacts (CardDAV)
    from routes.contacts_routes import setup_contacts_routes
    app.include_router(setup_contacts_routes())

    from companion import setup_companion_routes
    app.include_router(setup_companion_routes())

    return SimpleNamespace(
        task_scheduler=task_scheduler,
        mcp_manager=mcp_manager,
        upload_cleanup_func=upload_cleanup_func,
        upload_cleanup_task=upload_cleanup_task,
    )
