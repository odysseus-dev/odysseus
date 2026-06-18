"""
telegram_poller.py

Background polling loop that periodically fetches Telegram updates and routes
messages to the chat handler. Follows the same pattern as email_pollers.py.

Key components:
    - _telegram_poller: Main loop that polls getUpdates and processes messages
    - _start_poller: Entry point called at app startup
"""

import logging
import json
import asyncio
import os
from typing import Optional, Dict, Any

from routes.telegram_helpers import (
    _load_telegram_config,
    _get_telegram_user_config,
    _telegram_api_call,
    bind_telegram_topic_to_session,
    sync_telegram_bot_commands,
    send_telegram_message,
    send_typing_indicator,
    parse_telegram_message,
    format_for_telegram,
    remember_telegram_forum_chat,
    resolve_telegram_topic_session_id,
    TELEGRAM_POLLING_INTERVAL_SECONDS,
)

logger = logging.getLogger(__name__)

# Global state
_telegram_poller_running = False
_telegram_offset = 0
_chat_handler_instance = None

# Polling state file
_TELEGRAM_POLLING_STATE_FILE = None


def _get_polling_state_file():
    """Get path to Telegram polling state file."""
    global _TELEGRAM_POLLING_STATE_FILE
    if _TELEGRAM_POLLING_STATE_FILE:
        return _TELEGRAM_POLLING_STATE_FILE
    try:
        from src.constants import DATA_DIR
        import os
        _TELEGRAM_POLLING_STATE_FILE = os.path.join(DATA_DIR, "telegram_polling_state.json")
        return _TELEGRAM_POLLING_STATE_FILE
    except Exception:
        return None


def _load_polling_offset():
    """Load saved polling offset to resume from where we left off."""
    global _telegram_offset
    try:
        state_file = _get_polling_state_file()
        if not state_file:
            return

        state = {}
        if os.path.exists(state_file):
            with open(state_file, "r", encoding="utf-8") as fh:
                state = json.load(fh)
        _telegram_offset = state.get("offset", 0)
        logger.debug(f"Loaded Telegram polling offset: {_telegram_offset}")
    except Exception as e:
        logger.debug(f"Could not load polling offset (starting fresh): {e}")


def _save_polling_offset():
    """Save current polling offset to resume correctly on restart."""
    global _telegram_offset
    try:
        from core.atomic_io import atomic_write_json
        state_file = _get_polling_state_file()
        if not state_file:
            return
        
        state = {"offset": _telegram_offset}
        atomic_write_json(state_file, state)
    except Exception as e:
        logger.warning(f"Failed to save polling offset: {e}")


async def _map_telegram_user_to_odysseus_user(telegram_user_id: int) -> Optional[str]:
    """Find which Odysseus user is linked to this Telegram user ID."""
    try:
        from src.constants import USER_PREFS_FILE

        prefs = {}
        if os.path.exists(USER_PREFS_FILE):
            with open(USER_PREFS_FILE, "r", encoding="utf-8") as fh:
                prefs = json.load(fh)
        
        for owner, owner_prefs in prefs.items():
            telegram_config = owner_prefs.get("telegram", {})
            if telegram_config.get("encrypted"):
                try:
                    from src.secret_storage import decrypt
                    decrypted_data = decrypt(telegram_config.get("data", ""))
                    if decrypted_data:
                        config = json.loads(decrypted_data)
                        if config.get("telegram_user_id") == telegram_user_id:
                            return owner
                except Exception:
                    continue
            else:
                if telegram_config.get("telegram_user_id") == telegram_user_id:
                    return owner
        
        return None
    except Exception as e:
        logger.error("Error mapping Telegram user to Odysseus user: %s", e, exc_info=True)
        return None


async def _process_telegram_message(parsed_msg: Dict[str, Any]) -> bool:
    """Process a parsed Telegram message: handle commands or route to LLM.
    
    Args:
        parsed_msg: Dict with 'chat_id', 'user_id', 'text', 'message_id'
    
    Returns:
        True if successfully processed, False otherwise
    """
    try:
        chat_id = parsed_msg.get("chat_id")
        user_id = parsed_msg.get("user_id")
        text = parsed_msg.get("text", "").strip()
        chat_type = str(parsed_msg.get("chat_type") or "").strip().lower()
        chat_title = str(parsed_msg.get("chat_title") or "").strip()
        message_thread_id = parsed_msg.get("message_thread_id")
        is_topic_message = bool(parsed_msg.get("is_topic_message"))
        topic_event = str(parsed_msg.get("topic_event") or "").strip().lower()
        topic_name = str(parsed_msg.get("topic_name") or "").strip()
        
        # Handle /start command - initiates account linking
        if text.startswith("/start"):
            logger.info("Received /start command from Telegram user %s", user_id)
            if chat_type != "private":
                await send_telegram_message(
                    chat_id,
                    format_for_telegram("Send /start to me in a direct chat first, then come back here for forum topics."),
                    message_thread_id=message_thread_id,
                )
                return False
            
            try:
                from src.constants import internal_api_base
                from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN
                import httpx
                
                # Call the /start-linking endpoint to generate a linking token
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.post(
                        f"{internal_api_base()}/api/telegram/start-linking",
                        headers={
                            INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN,
                        },
                        json={
                            "telegram_user_id": user_id,
                            "telegram_chat_id": chat_id,
                        }
                    )
                    response.raise_for_status()
                    data = response.json()
                    
                    # Send linking instructions to user
                    instructions = data.get("instructions", "Failed to generate linking token")
                    formatted_msg = format_for_telegram(instructions)
                    await send_telegram_message(chat_id, formatted_msg, message_thread_id=message_thread_id)
                    return True
            except Exception as e:
                logger.error("Error handling /start command: %s", e, exc_info=True)
                error_msg = format_for_telegram("❌ Error: Could not initialize account linking. Please try again.")
                await send_telegram_message(chat_id, error_msg, message_thread_id=message_thread_id)
                return False
        
        # Handle /help command
        if text.startswith("/help"):
            logger.info("Received /help command from Telegram user %s", user_id)
            help_msg = format_for_telegram(
                "🤖 Odysseus Telegram Bot Help\n\n"
                "**Commands:**\n"
                "/start - Link your Telegram account to Odysseus\n"
                "/help - Show this help message\n"
                "/settings - View your chat settings\n"
                "/web <question> - Force a web-backed answer\n"
                "/research <topic> - Run deep research and send the report here\n"
                "/mode - Show the current Telegram mode\n"
                "/setmodechat - Switch to chat mode\n"
                "/setmodeagent - Switch to agent mode\n\n"
                "**Usage:**\n"
                "Send any message to chat with the AI. Chat mode answers directly and can use web search for current-info questions. Agent mode can use Odysseus tools when needed.\n\n"
                "**Features:**\n"
                "• Multi-turn conversations with full context\n"
                "• Persistent chat sessions per user\n"
                "• Web-backed answers for current information\n"
                "• Deep research on demand with /research\n"
                "• Automatic message splitting for long responses"
            )
            await send_telegram_message(chat_id, help_msg, message_thread_id=message_thread_id)
            return True
        
        # Handle /settings command
        if text.startswith("/settings"):
            logger.info("Received /settings command from Telegram user %s", user_id)
            owner = await _map_telegram_user_to_odysseus_user(user_id)
            if not owner:
                help_msg = format_for_telegram(
                    "👋 Hi! I don't recognize you yet.\n\n"
                    "Send /start to link your Telegram account to Odysseus."
                )
                await send_telegram_message(chat_id, help_msg, message_thread_id=message_thread_id)
                return False
            
            settings_msg = format_for_telegram(
                "⚙️ **Your Telegram Settings**\n\n"
                "**Account:** Linked ✅\n"
                f"**User ID:** `{user_id}`\n"
                f"**Chat ID:** `{chat_id}`\n"
                f"**Mode:** `{(_get_telegram_user_config(owner) or {}).get('mode', 'chat')}`\n\n"
                "To change your AI model, writing style, or other preferences, visit your Settings panel in Odysseus.\n\n"
                "For more help, use /help"
            )
            await send_telegram_message(chat_id, settings_msg, message_thread_id=message_thread_id)
            return True
        
        # Map Telegram user to Odysseus user for regular messages
        owner = await _map_telegram_user_to_odysseus_user(user_id)
        if not owner:
            logger.debug("Received message from unlinked Telegram user %s", user_id)
            help_msg = format_for_telegram(
                "👋 Hi! I don't recognize you yet.\n\n"
                "Send /start to link your Telegram account to Odysseus in a direct chat with the bot."
            )
            await send_telegram_message(chat_id, help_msg, message_thread_id=message_thread_id)
            return False

        user_config = _get_telegram_user_config(owner) or {}
        if chat_type in {"group", "supergroup"}:
            updated_config = remember_telegram_forum_chat(owner, chat_id, chat_title=chat_title)
            if updated_config:
                user_config = updated_config

        if (
            chat_type in {"group", "supergroup"}
            and topic_event == "created"
            and message_thread_id is not None
            and topic_name
        ):
            binding = bind_telegram_topic_to_session(
                owner,
                forum_chat_id=int(chat_id),
                topic_id=int(message_thread_id),
                topic_name=topic_name,
                forum_chat_title=chat_title,
            )
            if binding is None and _chat_handler_instance:
                session = _chat_handler_instance.get_or_create_telegram_topic_session(
                    owner,
                    forum_chat_id=int(chat_id),
                    topic_id=int(message_thread_id),
                    topic_name=topic_name,
                    forum_chat_title=chat_title,
                )
                binding = {
                    "session_id": session.id,
                    "session_name": session.name,
                    "topic_id": int(message_thread_id),
                    "topic_name": topic_name,
                }
            if binding:
                await send_telegram_message(
                    chat_id,
                    format_for_telegram(
                        f"Linked Telegram topic **{binding['topic_name']}** to Odysseus chat **{binding['session_name']}**."
                    ),
                    message_thread_id=message_thread_id,
                )
            return True

        topic_session_id = resolve_telegram_topic_session_id(user_config, chat_id, message_thread_id)
        if chat_type in {"group", "supergroup"} and not text.startswith("/"):
            if message_thread_id is None:
                await send_telegram_message(
                    chat_id,
                    format_for_telegram(
                        "Use one of the synced forum topics for chat messages. "
                        "If you just added me here, open Odysseus Settings → Integrations → Telegram and run Sync Topics."
                    ),
                    message_thread_id=message_thread_id,
                )
                return False
            if topic_session_id is None:
                if _chat_handler_instance:
                    session = _chat_handler_instance.get_or_create_telegram_topic_session(
                        owner,
                        forum_chat_id=int(chat_id),
                        topic_id=int(message_thread_id),
                        topic_name=topic_name or f"Topic {message_thread_id}",
                        forum_chat_title=chat_title,
                    )
                    topic_session_id = session.id
                else:
                    await send_telegram_message(
                        chat_id,
                        format_for_telegram(
                            "This topic is not linked to an Odysseus chat yet. "
                            "Run Sync Topics from Odysseus Settings → Integrations → Telegram."
                        ),
                        message_thread_id=message_thread_id,
                    )
                    return False
        
        # Send typing indicator
        await send_typing_indicator(chat_id, message_thread_id=message_thread_id)
        
        # Route to Telegram chat handler
        if _chat_handler_instance:
            return await _chat_handler_instance.handle_telegram_message(
                message_text=text,
                owner=owner,
                telegram_user_id=user_id,
                chat_id=chat_id,
                session_id=topic_session_id,
                message_thread_id=message_thread_id,
            )
        else:
            logger.error("Telegram chat handler not initialized")
            error_msg = format_for_telegram("❌ Chat handler not available. Please try again.")
            await send_telegram_message(chat_id, error_msg, message_thread_id=message_thread_id)
            return False
    except Exception as e:
        logger.error("Error processing Telegram message: %s", e, exc_info=True)
        return False


async def _handle_telegram_update(update: Dict[str, Any]) -> bool:
    """Handle a single Telegram update.
    
    Returns True if update was processed successfully.
    """
    try:
        # Parse the message
        parsed_msg = parse_telegram_message(update)
        if not parsed_msg:
            # This update doesn't contain a regular message (could be callback, etc.)
            return True
        
        # Process the message
        return await _process_telegram_message(parsed_msg)
    except Exception as e:
        logger.error("Error handling Telegram update: %s", e, exc_info=True)
        return False


async def _telegram_poller():
    """Main polling loop: fetch updates from Telegram and process them.
    
    Polls every TELEGRAM_POLLING_INTERVAL_SECONDS seconds using the
    getUpdates API with offset tracking for idempotency.
    """
    global _telegram_offset, _telegram_poller_running
    
    logger.info("Starting Telegram poller")
    _telegram_poller_running = True
    
    # Load saved offset on startup
    _load_polling_offset()
    
    consecutive_errors = 0
    max_consecutive_errors = 5
    
    try:
        while _telegram_poller_running:
            try:
                # Check if Telegram is configured
                config = _load_telegram_config()
                if not config or not config.get("bot_token"):
                    await asyncio.sleep(TELEGRAM_POLLING_INTERVAL_SECONDS)
                    continue

                await sync_telegram_bot_commands()
                
                # Fetch updates
                updates = await _telegram_api_call("getUpdates", {
                    "offset": _telegram_offset,
                    "timeout": 30,
                    "allowed_updates": ["message"],  # Only fetch messages
                })
                
                if updates is None:
                    consecutive_errors += 1
                    if consecutive_errors >= max_consecutive_errors:
                        logger.warning(
                            "Telegram poller hit %d consecutive errors, backing off",
                            consecutive_errors
                        )
                        await asyncio.sleep(TELEGRAM_POLLING_INTERVAL_SECONDS * 5)
                        consecutive_errors = 0
                    else:
                        await asyncio.sleep(TELEGRAM_POLLING_INTERVAL_SECONDS)
                    continue
                
                consecutive_errors = 0
                
                # Process updates
                if updates:
                    for update in updates:
                        try:
                            await _handle_telegram_update(update)
                            # Update offset for next poll
                            update_id = update.get("update_id", _telegram_offset)
                            _telegram_offset = max(_telegram_offset, update_id + 1)
                        except Exception as e:
                            logger.error("Error processing update %s: %s", 
                                       update.get("update_id"), e, exc_info=True)
                    
                    # Save offset periodically
                    _save_polling_offset()
                
                # Sleep before next poll
                await asyncio.sleep(TELEGRAM_POLLING_INTERVAL_SECONDS)
            
            except asyncio.CancelledError:
                logger.info("Telegram poller cancelled")
                break
            except Exception as e:
                logger.error("Telegram poller error: %s", e, exc_info=True)
                consecutive_errors += 1
                await asyncio.sleep(TELEGRAM_POLLING_INTERVAL_SECONDS)
    
    finally:
        _telegram_poller_running = False
        _save_polling_offset()
        logger.info("Telegram poller stopped")


def _start_poller(chat_handler=None):
    """Entry point to start the Telegram poller background task.
    
    Called once at app startup. Handles the deferred-start trick when
    the event loop is not yet running.
    
    Args:
        chat_handler: TelegramChatHandler instance for processing messages
    """
    global _telegram_poller_running, _chat_handler_instance
    
    if chat_handler:
        _chat_handler_instance = chat_handler
    
    if _telegram_poller_running:
        logger.warning("Telegram poller already running")
        return
    
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_telegram_poller())
    except RuntimeError:
        logger.debug("Telegram poller start requested before event loop was running; will retry on next Telegram request")
    except Exception as e:
        logger.error("Failed to start Telegram poller: %s", e, exc_info=True)
