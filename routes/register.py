import logging

from src.webhook_manager import WebhookManager
from src.task_scheduler import TaskScheduler
from src.event_bus import set_task_scheduler
from src.mcp_manager import McpManager
from src.agent_tools import set_mcp_manager
from src.ai_interaction import (
    set_session_manager as set_ai_session_manager,
    set_memory_manager as set_ai_memory_manager,
    set_rag_manager as set_ai_rag_manager,
)
from src.assistant_log import set_session_manager as _set_asst_sm

logger = logging.getLogger(__name__)


def register_routes(app, components, rag_manager, rag_available):
    session_manager = components["session_manager"]
    _set_asst_sm(session_manager)
    memory_manager = components["memory_manager"]
    memory_vector = components.get("memory_vector")
    upload_handler = components["upload_handler"]
    personal_docs_mgr = components["personal_docs_manager"]
    api_key_manager = components["api_key_manager"]
    preset_manager = components["preset_manager"]
    chat_processor = components["chat_processor"]
    research_handler = components["research_handler"]
    chat_handler = components["chat_handler"]
    model_discovery = components["model_discovery"]
    skills_manager = components["skills_manager"]

    from core.constants import REQUEST_TIMEOUT, OPENAI_API_KEY, SESSIONS_FILE

    # TTS / STT
    from services.tts import get_tts_service
    tts_service = get_tts_service()
    logger.info("TTS service initialized (provider managed via admin settings)")

    from services.stt import get_stt_service
    stt_service = get_stt_service()
    logger.info("STT service initialized (provider managed via settings)")

    # Webhook manager
    webhook_manager = WebhookManager(api_key_manager=api_key_manager)

    # Task scheduler
    task_scheduler = TaskScheduler(session_manager)
    set_task_scheduler(task_scheduler)

    # MCP manager
    mcp_manager = McpManager()
    set_mcp_manager(mcp_manager)

    # AI interaction wiring
    set_ai_session_manager(session_manager)
    set_ai_memory_manager(memory_manager, memory_vector)
    set_ai_rag_manager(rag_manager, personal_docs_mgr)
    logger.info("AI interaction tools initialized (session, memory, RAG, UI control)")

    # Store on app.state for lifecycle handlers
    app.state.session_manager = session_manager
    app.state.webhook_manager = webhook_manager
    app.state.task_scheduler = task_scheduler
    app.state.mcp_manager = mcp_manager
    app.state.model_discovery = model_discovery
    app.state.skills_manager = skills_manager

    # ── Auth ──
    from routes.auth_routes import setup_auth_routes
    app.include_router(setup_auth_routes(app.state.auth_manager))

    # ── Uploads ──
    from routes.upload_routes import setup_upload_routes
    upload_router, upload_cleanup_func = setup_upload_routes(upload_handler)
    app.include_router(upload_router)
    app.state.upload_cleanup_func = upload_cleanup_func

    # ── Emoji ──
    from routes.emoji_routes import setup_emoji_routes
    app.include_router(setup_emoji_routes())

    # ── Sessions ──
    from routes.session_routes import setup_session_routes
    session_config = {"REQUEST_TIMEOUT": REQUEST_TIMEOUT, "OPENAI_API_KEY": OPENAI_API_KEY, "SESSIONS_FILE": SESSIONS_FILE}
    app.include_router(setup_session_routes(session_manager, session_config, webhook_manager=webhook_manager))

    # ── Admin wipes ──
    from routes.admin_wipe_routes import setup_admin_wipe_routes
    app.include_router(setup_admin_wipe_routes(session_manager))

    # ── Memory + Skills ──
    from routes.memory_routes import setup_memory_routes
    app.include_router(setup_memory_routes(memory_manager, session_manager, memory_vector=memory_vector))
    from routes.skills_routes import setup_skills_routes
    app.include_router(setup_skills_routes(skills_manager))

    # ── Chat ──
    from routes.chat_routes import setup_chat_routes
    app.include_router(setup_chat_routes(
        session_manager, chat_handler, chat_processor,
        memory_manager, research_handler, upload_handler,
        memory_vector=memory_vector,
        webhook_manager=webhook_manager,
        skills_manager=skills_manager,
    ))

    # ── Research ──
    from routes.research_routes import setup_research_routes
    app.include_router(setup_research_routes(research_handler, session_manager=session_manager))

    # ── History ──
    from routes.history_routes import setup_history_routes
    app.include_router(setup_history_routes(session_manager))

    # ── Search ──
    from routes.search_routes import setup_search_routes
    from src.config import config
    app.include_router(setup_search_routes(config))

    # ── Presets ──
    from routes.preset_routes import setup_preset_routes
    app.include_router(setup_preset_routes(preset_manager))

    # ── Diagnostics ──
    from routes.diagnostics_routes import setup_diagnostics_routes
    app.include_router(setup_diagnostics_routes(rag_manager, rag_available, research_handler))

    # ── Cleanup ──
    from routes.cleanup_routes import setup_cleanup_routes
    app.include_router(setup_cleanup_routes(session_manager))

    # ── Personal docs ──
    from routes.personal_routes import setup_personal_routes
    app.include_router(setup_personal_routes(personal_docs_mgr, rag_manager, rag_available))

    # ── Embeddings ──
    from routes.embedding_routes import setup_embedding_routes
    app.include_router(setup_embedding_routes())

    # ── Models ──
    from routes.model_routes import setup_model_routes
    app.include_router(setup_model_routes(model_discovery))

    # ── TTS / STT ──
    from routes.tts_routes import setup_tts_routes
    app.include_router(setup_tts_routes(tts_service))
    from routes.stt_routes import setup_stt_routes
    app.include_router(setup_stt_routes(stt_service))

    # ── Documents ──
    from routes.document_routes import setup_document_routes
    app.include_router(setup_document_routes(session_manager, upload_handler))

    # ── Signatures ──
    from routes.signature_routes import setup_signature_routes
    app.include_router(setup_signature_routes())

    # ── Gallery ──
    from routes.gallery_routes import setup_gallery_routes
    app.include_router(setup_gallery_routes())

    # ── Editor drafts ──
    from routes.editor_draft_routes import setup_editor_draft_routes
    app.include_router(setup_editor_draft_routes())

    # ── Tasks ──
    from routes.task_routes import setup_task_routes
    app.include_router(setup_task_routes(task_scheduler))

    # ── Assistant ──
    from routes.assistant_routes import setup_assistant_routes
    app.include_router(setup_assistant_routes(task_scheduler))

    # ── Calendar ──
    from routes.calendar_routes import setup_calendar_routes
    app.include_router(setup_calendar_routes())

    # ── Shell ──
    from routes.shell_routes import setup_shell_routes
    app.include_router(setup_shell_routes())

    # ── Cookbook ──
    from routes.cookbook_routes import setup_cookbook_routes
    app.include_router(setup_cookbook_routes())

    # ── Hardware fit ──
    from routes.hwfit_routes import setup_hwfit_routes
    app.include_router(setup_hwfit_routes())

    # ── Compare ──
    from routes.compare_routes import setup_compare_routes
    app.include_router(setup_compare_routes(session_manager))

    # ── Preferences ──
    from routes.prefs_routes import setup_prefs_routes
    app.include_router(setup_prefs_routes())

    # ── Backup ──
    from routes.backup_routes import setup_backup_routes
    app.include_router(setup_backup_routes(memory_manager, preset_manager, skills_manager))

    # ── Fonts ──
    from routes.font_routes import setup_font_routes
    app.include_router(setup_font_routes())

    # ── MCP ──
    from routes.mcp_routes import setup_mcp_routes
    app.include_router(setup_mcp_routes(mcp_manager))
    logger.info("MCP routes initialized")

    # ── Webhooks ──
    from routes.webhook_routes import setup_webhook_routes
    app.include_router(setup_webhook_routes(webhook_manager, app.state.auth_manager, session_manager, api_key_manager))

    # ── API Tokens ──
    from routes.api_token_routes import setup_api_token_routes
    app.include_router(setup_api_token_routes())
    logger.info("Webhook & API token routes initialized")

    # ── Notes ──
    from routes.note_routes import setup_note_routes
    app.include_router(setup_note_routes(task_scheduler))

    # ── Email ──
    from routes.email_routes import setup_email_routes
    app.include_router(setup_email_routes())

    # ── Vault ──
    from routes.vault_routes import setup_vault_routes
    app.include_router(setup_vault_routes())

    # ── Contacts ──
    from routes.contacts_routes import setup_contacts_routes
    app.include_router(setup_contacts_routes())

    # ── SPA ──
    from routes.spa_routes import setup_spa_routes
    app.include_router(setup_spa_routes())
