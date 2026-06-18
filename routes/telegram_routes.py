"""
telegram_routes.py

FastAPI route handlers for the Telegram integration. All non-route logic
lives in routes/telegram_helpers.py and routes/telegram_poller.py.

Endpoints:
    GET  /api/telegram/config - Get current Telegram configuration status
    POST /api/telegram/config - Update Telegram bot token and enable/disable
    POST /api/telegram/link - Link Telegram account (one-time)
    POST /api/telegram/start-linking - Begin linking flow (generates token)
    GET  /api/telegram/linking-status/{token} - Check linking status
"""

import logging
import secrets
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from src.auth_helpers import get_current_user
from core.middleware import require_admin, INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN, INTERNAL_TOOL_USER
from routes.telegram_helpers import (
    _load_telegram_config,
    _load_telegram_system_config,
    _save_telegram_system_config,
    _get_telegram_user_config,
    _save_telegram_user_config,
    _clear_telegram_user_config,
    _clear_all_telegram_user_configs,
    clear_all_linking_states,
    create_linking_state,
    verify_linking_token,
    validate_telegram_bot_token,
    hash_telegram_user_id,
    build_telegram_topic_name,
    list_syncable_telegram_sessions,
    create_telegram_forum_topic,
    edit_telegram_forum_topic,
    get_telegram_topic_mappings,
    set_telegram_topic_mappings,
    find_owner_by_telegram_user_id,
)
from routes.telegram_chat_handler import TelegramChatHandler
from routes.telegram_poller import _start_poller

logger = logging.getLogger(__name__)


def _is_internal_tool_request(request: Request) -> bool:
    """Return True for in-process internal tool loopback calls."""
    try:
        hdr = request.headers.get(INTERNAL_TOOL_HEADER)
        if hdr and secrets.compare_digest(hdr, INTERNAL_TOOL_TOKEN):
            return True
        return getattr(request.state, "current_user", None) == INTERNAL_TOOL_USER
    except Exception:
        return False


class TelegramConfigResponse(BaseModel):
    """Response with current Telegram configuration status."""
    enabled: bool
    bot_token_configured: bool
    user_linked: bool
    is_admin: bool = False
    can_manage_bot: bool = False
    managed_by_env: bool = False
    config_source: str = "none"
    bot_username: Optional[str] = None
    bot_name: Optional[str] = None
    chat_id: Optional[int] = None
    last_update: Optional[str] = None
    chat_mode: str = "chat"
    forum_chat_id: Optional[int] = None
    forum_chat_title: Optional[str] = None
    topic_count: int = 0


class TelegramConfigUpdateRequest(BaseModel):
    """Request to save or clear the Telegram bot token."""
    bot_token: str = ""


class TelegramConfigUpdateResponse(BaseModel):
    """Response after saving bot configuration."""
    ok: bool
    bot_token_configured: bool
    managed_by_env: bool = False
    bot_username: Optional[str] = None
    bot_name: Optional[str] = None
    message: str


class StartLinkingRequest(BaseModel):
    """Request to start the Telegram linking process."""
    telegram_user_id: int
    telegram_chat_id: int


class StartLinkingResponse(BaseModel):
    """Response with linking token and instructions."""
    linking_token: str
    instructions: str


class CompleteLinkingRequest(BaseModel):
    """Request to complete the Telegram account linking."""
    linking_token: str


class CompleteLinkingResponse(BaseModel):
    """Response confirming successful linking."""
    status: str
    telegram_user_id: int
    chat_id: int
    message: str


class TelegramUnlinkResponse(BaseModel):
    """Response confirming account unlink."""
    status: str
    message: str


class TelegramResetResponse(BaseModel):
    """Response confirming a full Telegram integration reset."""
    ok: bool
    bot_token_cleared: bool
    user_links_cleared: int
    linking_codes_cleared: int
    managed_by_env: bool = False
    message: str


class TelegramModeUpdateRequest(BaseModel):
    """Request to update Telegram chat mode."""
    mode: str


class TelegramModeUpdateResponse(BaseModel):
    """Response after updating Telegram chat mode."""
    ok: bool
    mode: str
    message: str


class TelegramTopicSyncItem(BaseModel):
    session_id: str
    session_name: str
    topic_id: int
    topic_name: str
    status: str


class TelegramTopicSyncResponse(BaseModel):
    ok: bool
    forum_chat_id: int
    forum_chat_title: Optional[str] = None
    synced_count: int
    created_count: int
    updated_count: int
    skipped_count: int
    topics: list[TelegramTopicSyncItem]


def setup_telegram_routes(chat_handler=None, session_manager=None, research_handler=None) -> APIRouter:
    """Setup and return the Telegram routes router."""
    telegram_chat_handler = None
    if chat_handler is not None and session_manager is not None:
        telegram_chat_handler = TelegramChatHandler(
            chat_handler,
            session_manager,
            research_handler=research_handler,
        )

    def _ensure_poller_started() -> None:
        _start_poller(telegram_chat_handler)

    @asynccontextmanager
    async def _telegram_router_lifespan(_app):
        _ensure_poller_started()
        yield

    router = APIRouter(
        prefix="/api/telegram",
        tags=["telegram"],
        lifespan=_telegram_router_lifespan,
    )
    router._telegram_chat_handler = telegram_chat_handler
    router._ensure_poller_started = _ensure_poller_started

    # Start the polling loop
    _ensure_poller_started()
    
    @router.get("/config", response_model=TelegramConfigResponse)
    async def get_telegram_config(request: Request):
        """Get current Telegram configuration status for the authenticated user."""
        _ensure_poller_started()
        user = get_current_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="Not authenticated")
        
        try:
            sys_config = _load_telegram_config()
            saved_config = _load_telegram_system_config()
            user_config = _get_telegram_user_config(user)
            auth_mgr = getattr(request.app.state, "auth_manager", None)
            is_admin = bool(auth_mgr and getattr(auth_mgr, "is_admin", None) and auth_mgr.is_admin(user))
            
            return TelegramConfigResponse(
                enabled=bool(sys_config.get("bot_token")),
                bot_token_configured=bool(sys_config.get("bot_token")),
                user_linked=user_config is not None and user_config.get("enabled") is True,
                is_admin=is_admin,
                can_manage_bot=is_admin,
                managed_by_env=bool(sys_config.get("managed_by_env")),
                config_source=str(sys_config.get("config_source") or "none"),
                bot_username=sys_config.get("bot_username") or saved_config.get("bot_username"),
                bot_name=sys_config.get("bot_name") or saved_config.get("bot_name"),
                chat_id=user_config.get("chat_id") if user_config else None,
                last_update=user_config.get("updated_at") if user_config else None,
                chat_mode=str((user_config or {}).get("mode") or "chat"),
                forum_chat_id=user_config.get("forum_chat_id") if user_config else None,
                forum_chat_title=user_config.get("forum_chat_title") if user_config else None,
                topic_count=len(get_telegram_topic_mappings(user_config)),
            )
        except Exception as e:
            logger.error("Error getting Telegram config for user %s: %s", user, e, exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to get Telegram configuration")

    @router.post("/config", response_model=TelegramConfigUpdateResponse)
    async def update_telegram_config(request: Request, data: TelegramConfigUpdateRequest):
        """Create, update, or clear the Telegram bot token via the settings UI."""
        _ensure_poller_started()
        require_admin(request)

        try:
            existing = _load_telegram_config()
            if existing.get("managed_by_env"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Telegram is managed by the {existing.get('config_source')} source. Remove {existing.get('config_source') == 'environment' and 'TELEGRAM_BOT_TOKEN environment variable' or 'saved config'} first."
                )

            token = str(data.bot_token or "").strip()
            if not token:
                if not _save_telegram_system_config(""):
                    raise HTTPException(status_code=500, detail="Failed to clear Telegram configuration")
                return TelegramConfigUpdateResponse(
                    ok=True,
                    bot_token_configured=False,
                    managed_by_env=False,
                    message="Telegram bot configuration cleared.",
                )

            bot_info = await validate_telegram_bot_token(token)
            if not bot_info:
                raise HTTPException(status_code=400, detail="Invalid Telegram bot token")

            if not _save_telegram_system_config(
                token,
                bot_username=bot_info.get("username", ""),
                bot_name=bot_info.get("first_name", ""),
            ):
                raise HTTPException(status_code=500, detail="Failed to save Telegram configuration")

            return TelegramConfigUpdateResponse(
                ok=True,
                bot_token_configured=True,
                managed_by_env=False,
                bot_username=bot_info.get("username"),
                bot_name=bot_info.get("first_name"),
                message="Telegram bot configured successfully.",
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Error updating Telegram config: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to update Telegram configuration")
    
    @router.post("/start-linking", response_model=StartLinkingResponse)
    async def start_telegram_linking(request: Request, data: StartLinkingRequest):
        """Begin the Telegram account linking process.
        
        This endpoint is called by the bot when it receives /start from a user.
        It generates a secure token that the user must provide in Odysseus to
        complete the linking. This prevents unauthorized linking attacks.
        """
        _ensure_poller_started()
        try:
            if not _is_internal_tool_request(request):
                raise HTTPException(status_code=403, detail="Forbidden")

            # Validate inputs
            if not data.telegram_user_id or not data.telegram_chat_id:
                raise HTTPException(status_code=400, detail="Missing telegram_user_id or telegram_chat_id")
            
            if data.telegram_user_id <= 0 or data.telegram_chat_id == 0:
                raise HTTPException(status_code=400, detail="Invalid Telegram IDs")
            
            # Create linking state (generates one-time token)
            token = create_linking_state(data.telegram_user_id, data.telegram_chat_id)
            
            logger.info("Started Telegram linking for user %s", hash_telegram_user_id(data.telegram_user_id))
            
            return StartLinkingResponse(
                linking_token=token,
                instructions=(
                    f"To link your Telegram account:\n\n"
                    f"1. Go to Odysseus Settings → Integrations → Telegram\n"
                    f"2. Paste this code into the Linking token field: {token}\n"
                    f"3. Click Link Account\n\n"
                    f"This code expires in 5 minutes."
                ),
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Error starting Telegram linking: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to start linking process")
    
    @router.post("/link", response_model=CompleteLinkingResponse)
    async def complete_telegram_linking(request: Request, data: CompleteLinkingRequest):
        """Complete the Telegram account linking.
        
        User calls this after receiving the linking token from the bot.
        Validates the token and links the Telegram account to the Odysseus user.
        """
        _ensure_poller_started()
        user = get_current_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="Not authenticated")
        
        try:
            # Verify the linking token
            state = verify_linking_token(data.linking_token)
            if not state:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid or expired linking token. Start over from the Telegram bot."
                )
            
            telegram_user_id = state.get("telegram_user_id")
            chat_id = state.get("telegram_chat_id")
            
            # Check if this Telegram account is already linked to a different Odysseus user
            linked_owner = find_owner_by_telegram_user_id(int(telegram_user_id), exclude_owner=user)
            if linked_owner:
                logger.warning(
                    "Attempted to link already-linked Telegram user %s to different account",
                    hash_telegram_user_id(telegram_user_id)
                )
                raise HTTPException(
                    status_code=400,
                    detail="This Telegram account is already linked to another Odysseus user"
                )
            
            # Save the linking
            user_config = _get_telegram_user_config(user) or {}
            user_config["telegram_user_id"] = int(telegram_user_id)
            user_config["chat_id"] = int(chat_id)
            user_config["enabled"] = True
            
            if not _save_telegram_user_config(user, user_config):
                raise HTTPException(status_code=500, detail="Failed to save linking")
            
            logger.info(
                "Successfully linked Telegram user %s to Odysseus user %s",
                hash_telegram_user_id(telegram_user_id),
                user
            )
            
            return CompleteLinkingResponse(
                status="linked",
                telegram_user_id=telegram_user_id,
                chat_id=chat_id,
                message="✅ Telegram account linked successfully! Start chatting with the bot.",
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Error completing Telegram linking for user %s: %s", user, e, exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to complete linking")

    @router.post("/unlink", response_model=TelegramUnlinkResponse)
    async def unlink_telegram_account(request: Request):
        """Unlink the authenticated user's Telegram account."""
        _ensure_poller_started()
        user = get_current_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="Not authenticated")

        try:
            existing = _get_telegram_user_config(user)
            if not existing:
                raise HTTPException(status_code=400, detail="No Telegram account is linked")
            if not _clear_telegram_user_config(user):
                raise HTTPException(status_code=500, detail="Failed to unlink Telegram account")
            return TelegramUnlinkResponse(
                status="unlinked",
                message="Telegram account unlinked successfully.",
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Error unlinking Telegram account for user %s: %s", user, e, exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to unlink Telegram account")

    @router.post("/reset", response_model=TelegramResetResponse)
    async def reset_telegram_integration(request: Request):
        """Reset Telegram integration: clear token, all user links, and pending link codes."""
        _ensure_poller_started()
        require_admin(request)

        try:
            existing = _load_telegram_config()
            managed_by_env = bool(existing.get("managed_by_env"))

            bot_token_cleared = False
            if managed_by_env:
                logger.info("Telegram reset requested while bot token is environment-managed")
            else:
                if not _save_telegram_system_config(""):
                    raise HTTPException(status_code=500, detail="Failed to clear Telegram bot token")
                bot_token_cleared = True

            user_links_cleared = _clear_all_telegram_user_configs()
            linking_codes_cleared = clear_all_linking_states()

            if managed_by_env:
                message = (
                    "Telegram account links and pending linking codes were cleared. "
                    "Bot token is managed by TELEGRAM_BOT_TOKEN and was not removed here."
                )
            else:
                message = "Telegram integration removed. Bot token, account links, and pending linking codes were cleared."

            return TelegramResetResponse(
                ok=True,
                bot_token_cleared=bot_token_cleared,
                user_links_cleared=user_links_cleared,
                linking_codes_cleared=linking_codes_cleared,
                managed_by_env=managed_by_env,
                message=message,
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Error resetting Telegram integration: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to reset Telegram integration")

    @router.post("/mode", response_model=TelegramModeUpdateResponse)
    async def update_telegram_mode(request: Request, data: TelegramModeUpdateRequest):
        """Update the authenticated user's Telegram chat mode."""
        _ensure_poller_started()
        user = get_current_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="Not authenticated")

        try:
            mode = str(data.mode or "").strip().lower()
            if mode not in {"chat", "agent"}:
                raise HTTPException(status_code=400, detail="Mode must be 'chat' or 'agent'")

            config = _get_telegram_user_config(user) or {}
            config["mode"] = mode
            if not _save_telegram_user_config(user, config):
                raise HTTPException(status_code=500, detail="Failed to save Telegram mode")

            return TelegramModeUpdateResponse(
                ok=True,
                mode=mode,
                message=f"Telegram mode updated to {mode}.",
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Error updating Telegram mode for user %s: %s", user, e, exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to update Telegram mode")

    @router.post("/sync-topics", response_model=TelegramTopicSyncResponse)
    async def sync_telegram_topics(request: Request):
        """Create or refresh Telegram forum topics for the user's active Odysseus chats."""
        _ensure_poller_started()
        user = get_current_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if session_manager is None:
            raise HTTPException(status_code=503, detail="Telegram chat sync is unavailable")

        try:
            config = _get_telegram_user_config(user) or {}
            if not config.get("enabled") or not config.get("telegram_user_id"):
                raise HTTPException(status_code=400, detail="Link your Telegram account first")

            try:
                forum_chat_id = int(config.get("forum_chat_id"))
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "No Telegram forum chat detected yet. Add the bot to your forum-enabled "
                        "group chat, send a message there, then try again."
                    ),
                )

            sessions = list_syncable_telegram_sessions(user)
            mappings = get_telegram_topic_mappings(config)
            active_session_ids = {session_id for session_id, _ in sessions}
            mappings = {sid: mapping for sid, mapping in mappings.items() if sid in active_session_ids}

            created_count = 0
            updated_count = 0
            skipped_count = 0
            topics: list[TelegramTopicSyncItem] = []

            for session_id, session_name in sessions:
                topic_name = build_telegram_topic_name(session_name, session_id)
                existing = mappings.get(session_id) or {}
                existing_topic_id = existing.get("topic_id")
                existing_topic_name = str(existing.get("topic_name") or "").strip()
                status = "unchanged"
                topic_id: Optional[int] = None

                if existing_topic_id is not None:
                    topic_id = int(existing_topic_id)
                    if existing_topic_name != topic_name:
                        renamed = await edit_telegram_forum_topic(forum_chat_id, topic_id, topic_name)
                        if renamed:
                            updated_count += 1
                            status = "updated"
                        else:
                            topic = await create_telegram_forum_topic(forum_chat_id, topic_name)
                            if topic is None:
                                raise HTTPException(
                                    status_code=502,
                                    detail=(
                                        "Failed to refresh Telegram forum topics. Make sure the bot is "
                                        "in a forum-enabled supergroup and allowed to manage topics."
                                    ),
                                )
                            topic_id = int(topic["message_thread_id"])
                            created_count += 1
                            status = "created"
                    else:
                        skipped_count += 1
                else:
                    topic = await create_telegram_forum_topic(forum_chat_id, topic_name)
                    if topic is None:
                        raise HTTPException(
                            status_code=502,
                            detail=(
                                "Failed to create Telegram forum topics. Make sure the bot is "
                                "in a forum-enabled supergroup and allowed to manage topics."
                            ),
                        )
                    topic_id = int(topic["message_thread_id"])
                    created_count += 1
                    status = "created"

                if topic_id is None:
                    raise HTTPException(status_code=500, detail=f"Missing topic id for session {session_id}")

                mappings[session_id] = {
                    "topic_id": topic_id,
                    "topic_name": topic_name,
                    "session_name": str(session_name or "").strip(),
                }
                topics.append(
                    TelegramTopicSyncItem(
                        session_id=session_id,
                        session_name=str(session_name or "").strip(),
                        topic_id=topic_id,
                        topic_name=topic_name,
                        status=status,
                    )
                )

            set_telegram_topic_mappings(config, mappings)
            if not _save_telegram_user_config(user, config):
                raise HTTPException(status_code=500, detail="Failed to save Telegram topic mappings")

            return TelegramTopicSyncResponse(
                ok=True,
                forum_chat_id=forum_chat_id,
                forum_chat_title=str(config.get("forum_chat_title") or "").strip() or None,
                synced_count=len(topics),
                created_count=created_count,
                updated_count=updated_count,
                skipped_count=skipped_count,
                topics=topics,
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Error syncing Telegram topics for user %s: %s", user, e, exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to sync Telegram topics")
    
    return router
