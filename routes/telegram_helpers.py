"""
telegram_helpers.py

Lower-level helpers used by both `telegram_routes.py` (the FastAPI route file)
and `telegram_poller.py` (the background loops):

    - auth dependencies (require_owner / require_user)
    - account config + settings persistence
    - Telegram API helpers (send message, get updates)
    - message parsing and formatting
    - security validation
"""

import logging
import json
import httpx
import secrets
import hashlib
import os
from typing import Optional, Dict, Any
from datetime import datetime

from core.database import Session as DbSession, SessionLocal
from src.auth_helpers import owner_filter
from src.secret_storage import decrypt, encrypt, is_encrypted
from src.constants import DATA_DIR, SETTINGS_FILE, TELEGRAM_BOT_TOKEN_ENV, TELEGRAM_API_BASE_URL

logger = logging.getLogger(__name__)

# Telegram API timeout
TELEGRAM_TIMEOUT_SECONDS = 30

# Message limits
TELEGRAM_MAX_MESSAGE_LENGTH = 4096

# Polling configuration
TELEGRAM_POLLING_TIMEOUT = 30
TELEGRAM_POLLING_INTERVAL_SECONDS = 5

# Conversation management
TELEGRAM_SESSION_TIMEOUT_MINUTES = 30  # Idle timeout for conversations
TELEGRAM_SESSION_TIMEOUT_SECONDS = TELEGRAM_SESSION_TIMEOUT_MINUTES * 60

# Security: Telegram user linking state (ephemeral, cleared after 5 minutes or linking)
_LINKING_STATES: Dict[str, Dict[str, Any]] = {}
_BOT_COMMANDS_SYNC_SIGNATURE: Optional[str] = None

_TELEGRAM_BOT_COMMANDS = (
    {"command": "start", "description": "Link your Telegram account"},
    {"command": "mode", "description": "Show the current Telegram mode"},
    {"command": "setmodeagent", "description": "Switch Telegram to agent mode"},
    {"command": "setmodechat", "description": "Switch Telegram to chat mode"},
)
_TELEGRAM_SYNC_EXCLUDED_NAMES = {
    "Nobody",
    "Incognito",
    "[Task] Chat Sessions Tidy",
    "[Task] Documents Tidy",
    "[Task] Memory Tidy",
    "[Task] Research Tidy",
    "[Task] Email Mark Boundaries",
    "[Task] Email Tags",
    "[Task] Skills Audit",
}
_COMPARE_SESSION_PREFIX = "[CMP] "


def _normalize_telegram_topic_mappings(raw: Any) -> Dict[str, Dict[str, Any]]:
    """Return a sanitized session_id -> topic mapping dictionary."""
    if not isinstance(raw, dict):
        return {}

    normalized: Dict[str, Dict[str, Any]] = {}
    for session_id, mapping in raw.items():
        if not isinstance(session_id, str) or not isinstance(mapping, dict):
            continue
        try:
            topic_id = int(mapping.get("topic_id"))
        except (TypeError, ValueError):
            continue
        normalized[session_id] = {
            "topic_id": topic_id,
            "topic_name": str(mapping.get("topic_name") or "").strip(),
            "session_name": str(mapping.get("session_name") or "").strip(),
            "synced_at": mapping.get("synced_at"),
        }
    return normalized


def get_telegram_topic_mappings(config: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Load per-session Telegram topic mappings from a user config."""
    return _normalize_telegram_topic_mappings((config or {}).get("topic_mappings"))


def set_telegram_topic_mappings(config: Dict[str, Any], mappings: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Persist sanitized topic mappings back into a mutable user config."""
    config["topic_mappings"] = _normalize_telegram_topic_mappings(mappings)
    return config


def build_telegram_topic_name(session_name: str, session_id: str) -> str:
    """Build a Telegram-safe topic name from an Odysseus session."""
    name = " ".join(str(session_name or "").strip().split())
    if not name:
        name = f"Chat {str(session_id or '')[:8]}".strip()
    return name[:128]


def _normalize_topic_lookup_name(name: str) -> str:
    return " ".join(str(name or "").strip().split()).casefold()


def list_syncable_telegram_sessions(owner: str) -> list[tuple[str, str]]:
    """Return active, user-owned sessions that should map to Telegram topics."""
    db = SessionLocal()
    try:
        q = (
            db.query(DbSession.id, DbSession.name)
            .filter(DbSession.archived == False)
            .order_by(DbSession.last_message_at.desc(), DbSession.updated_at.desc(), DbSession.created_at.desc())
        )
        q = owner_filter(q, DbSession, owner)
        rows = q.all()
        sessions: list[tuple[str, str]] = []
        for row in rows:
            name = str(row.name or "")
            stripped = name.strip()
            if stripped in _TELEGRAM_SYNC_EXCLUDED_NAMES:
                continue
            if stripped.startswith("telegram_"):
                continue
            if stripped.startswith(_COMPARE_SESSION_PREFIX):
                continue
            sessions.append((row.id, name))
        return sessions
    finally:
        db.close()


def find_telegram_session_by_topic_name(owner: str, topic_name: str) -> Optional[tuple[str, str]]:
    """Find the Odysseus session whose Telegram topic name matches `topic_name`."""
    wanted = _normalize_topic_lookup_name(topic_name)
    if not wanted:
        return None
    for session_id, session_name in list_syncable_telegram_sessions(owner):
        if _normalize_topic_lookup_name(build_telegram_topic_name(session_name, session_id)) == wanted:
            return session_id, session_name
    return None


def save_telegram_topic_mapping(
    owner: str,
    *,
    forum_chat_id: int,
    topic_id: int,
    session_id: str,
    session_name: str,
    topic_name: str = "",
    forum_chat_title: str = "",
) -> Optional[Dict[str, Any]]:
    """Persist a Telegram topic mapping for an explicit Odysseus session."""
    config = _get_telegram_user_config(owner)
    if not config:
        return None

    normalized_topic_name = build_telegram_topic_name(topic_name or session_name, session_id)
    config["forum_chat_id"] = int(forum_chat_id)
    if forum_chat_title:
        config["forum_chat_title"] = str(forum_chat_title).strip()

    mappings = get_telegram_topic_mappings(config)
    mappings = {
        sid: mapping for sid, mapping in mappings.items()
        if int(mapping.get("topic_id")) != int(topic_id) or sid == session_id
    }
    mappings[session_id] = {
        "topic_id": int(topic_id),
        "topic_name": normalized_topic_name,
        "session_name": str(session_name or "").strip(),
        "synced_at": datetime.utcnow().isoformat(),
    }
    set_telegram_topic_mappings(config, mappings)

    if not _save_telegram_user_config(owner, config):
        return None

    return {
        "session_id": session_id,
        "session_name": str(session_name or "").strip(),
        "topic_id": int(topic_id),
        "topic_name": normalized_topic_name,
    }


def bind_telegram_topic_to_session(
    owner: str,
    *,
    forum_chat_id: int,
    topic_id: int,
    topic_name: str,
    forum_chat_title: str = "",
) -> Optional[Dict[str, Any]]:
    """Persist a Telegram topic mapping by matching the topic name to an Odysseus session."""
    match = find_telegram_session_by_topic_name(owner, topic_name)
    if not match:
        return None

    session_id, session_name = match
    return save_telegram_topic_mapping(
        owner,
        forum_chat_id=forum_chat_id,
        topic_id=topic_id,
        session_id=session_id,
        session_name=session_name,
        topic_name=topic_name,
        forum_chat_title=forum_chat_title,
    )


def remember_telegram_forum_chat(
    owner: str,
    chat_id: int,
    *,
    chat_title: str = "",
) -> Optional[Dict[str, Any]]:
    """Persist the detected forum/supergroup chat for a linked Telegram user."""
    config = _get_telegram_user_config(owner)
    if not config:
        return None

    forum_chat_id = int(chat_id)
    previous_chat_id = config.get("forum_chat_id")
    try:
        previous_chat_id = int(previous_chat_id) if previous_chat_id is not None else None
    except (TypeError, ValueError):
        previous_chat_id = None

    config["forum_chat_id"] = forum_chat_id
    if chat_title:
        config["forum_chat_title"] = str(chat_title).strip()
    config["forum_detected_at"] = datetime.utcnow().isoformat()

    if previous_chat_id is not None and previous_chat_id != forum_chat_id:
        config["topic_mappings"] = {}

    if not _save_telegram_user_config(owner, config):
        return None
    return config


def resolve_telegram_topic_session_id(
    config: Optional[Dict[str, Any]],
    chat_id: int,
    message_thread_id: Optional[int],
) -> Optional[str]:
    """Return the Odysseus session bound to a Telegram forum topic."""
    if message_thread_id in (None, ""):
        return None

    try:
        forum_chat_id = int((config or {}).get("forum_chat_id"))
    except (TypeError, ValueError):
        return None
    if forum_chat_id != int(chat_id):
        return None

    try:
        thread_id = int(message_thread_id)
    except (TypeError, ValueError):
        return None

    for session_id, mapping in get_telegram_topic_mappings(config).items():
        if mapping.get("topic_id") == thread_id:
            return session_id
    return None


def _read_json_file(path: str, default: Any) -> Any:
    """Read a JSON file if present, otherwise return `default`."""
    try:
        if not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def _load_telegram_config() -> Dict[str, Any]:
    """Load Telegram configuration from environment or settings."""
    token = os.getenv(TELEGRAM_BOT_TOKEN_ENV)
    if token:
        return {
            "bot_token": token,
            "api_base": TELEGRAM_API_BASE_URL,
            "config_source": "environment",
            "managed_by_env": True,
        }

    saved = _load_telegram_system_config()
    if saved.get("bot_token"):
        return {
            "bot_token": saved.get("bot_token"),
            "api_base": TELEGRAM_API_BASE_URL,
            "config_source": "settings",
            "managed_by_env": False,
            "bot_username": saved.get("bot_username", ""),
            "bot_name": saved.get("bot_name", ""),
            "updated_at": saved.get("updated_at"),
        }

    return {
        "api_base": TELEGRAM_API_BASE_URL,
        "config_source": "none",
        "managed_by_env": False,
    }


def _load_telegram_system_config() -> Dict[str, Any]:
    """Load saved Telegram bot settings from settings.json."""
    try:
        settings = _read_json_file(SETTINGS_FILE, {})
        telegram = settings.get("telegram", {})
        if not isinstance(telegram, dict):
            return {}

        encrypted_token = telegram.get("bot_token", "")
        token = ""
        if encrypted_token:
            token = decrypt(encrypted_token) if is_encrypted(encrypted_token) else str(encrypted_token)

        return {
            "bot_token": token,
            "bot_username": str(telegram.get("bot_username") or "").strip(),
            "bot_name": str(telegram.get("bot_name") or "").strip(),
            "updated_at": telegram.get("updated_at"),
        }
    except Exception as e:
        logger.error("Error loading Telegram system config: %s", e, exc_info=True)
        return {}


def _save_telegram_system_config(bot_token: str = "", *, bot_username: str = "", bot_name: str = "") -> bool:
    """Persist Telegram bot settings in settings.json."""
    global _BOT_COMMANDS_SYNC_SIGNATURE
    try:
        from core.atomic_io import atomic_write_json

        settings = _read_json_file(SETTINGS_FILE, {})
        token = str(bot_token or "").strip()
        _BOT_COMMANDS_SYNC_SIGNATURE = None
        if token:
            settings["telegram"] = {
                "bot_token": encrypt(token),
                "bot_username": str(bot_username or "").strip(),
                "bot_name": str(bot_name or "").strip(),
                "updated_at": datetime.utcnow().isoformat(),
            }
        else:
            settings.pop("telegram", None)

        atomic_write_json(str(SETTINGS_FILE), settings, indent=2)
        return True
    except Exception as e:
        logger.error("Error saving Telegram system config: %s", e, exc_info=True)
        return False


def get_telegram_bot_commands() -> list[Dict[str, str]]:
    """Return the Telegram command list published to clients."""
    return [dict(command) for command in _TELEGRAM_BOT_COMMANDS]


def _get_telegram_user_config(owner: str) -> Optional[Dict[str, Any]]:
    """Get encrypted Telegram user configuration from user preferences.
    
    Returns dict with keys like 'telegram_user_id', 'chat_id', etc.
    """
    try:
        from src.constants import USER_PREFS_FILE
        
        prefs = _read_json_file(USER_PREFS_FILE, {})
        if not prefs:
            return None
        
        user_prefs = prefs.get(owner, {})
        telegram_config = user_prefs.get("telegram", {})
        
        if not telegram_config:
            return None
        
        # Decrypt sensitive fields
        if telegram_config.get("encrypted"):
            decrypted_data = decrypt(telegram_config.get("data", ""))
            if decrypted_data:
                try:
                    return json.loads(decrypted_data)
                except json.JSONDecodeError:
                    logger.error("Failed to decode decrypted Telegram config for user %s", owner)
                    return None
        
        return telegram_config
    except Exception as e:
        logger.error("Error loading Telegram config for user %s: %s", owner, e, exc_info=True)
        return None


def _save_telegram_user_config(owner: str, config: Dict[str, Any]) -> bool:
    """Save encrypted Telegram user configuration to user preferences."""
    try:
        from src.constants import USER_PREFS_FILE
        from core.atomic_io import atomic_write_json
        
        prefs = _read_json_file(USER_PREFS_FILE, {})
        if owner not in prefs:
            prefs[owner] = {}
        
        # Encrypt sensitive data
        encrypted_data = encrypt(json.dumps(config))
        prefs[owner]["telegram"] = {
            "encrypted": True,
            "data": encrypted_data,
            "updated_at": datetime.utcnow().isoformat(),
        }
        
        atomic_write_json(str(USER_PREFS_FILE), prefs)
        return True
    except Exception as e:
        logger.error("Error saving Telegram config for user %s: %s", owner, e, exc_info=True)
        return False


def _clear_telegram_user_config(owner: str) -> bool:
    """Remove Telegram user linking information for a user."""
    try:
        from src.constants import USER_PREFS_FILE
        from core.atomic_io import atomic_write_json

        prefs = _read_json_file(USER_PREFS_FILE, {})
        if owner in prefs and isinstance(prefs[owner], dict):
            prefs[owner].pop("telegram", None)
            atomic_write_json(str(USER_PREFS_FILE), prefs)
        return True
    except Exception as e:
        logger.error("Error clearing Telegram config for user %s: %s", owner, e, exc_info=True)
        return False


async def _telegram_api_call(method: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Make a call to the Telegram Bot API.
    
    Args:
        method: Telegram API method name (e.g., 'sendMessage', 'getUpdates')
        params: Parameters to send to the API
    
    Returns:
        JSON response from Telegram, or None on error
    """
    config = _load_telegram_config()
    if not config or not config.get("bot_token"):
        logger.error("Telegram bot token not configured")
        return None
    
    url = f"{config['api_base']}/bot{config['bot_token']}/{method}"
    
    try:
        async with httpx.AsyncClient(timeout=TELEGRAM_TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=params or {})
            response.raise_for_status()
            data = response.json()
            
            if not data.get("ok"):
                logger.error("Telegram API error: %s", data.get("description", "Unknown error"))
                return None
            
            return data.get("result")
    except Exception as e:
        logger.error("Telegram API call failed for method %s: %s", method, e, exc_info=True)
        return None


async def sync_telegram_bot_commands(force: bool = False) -> bool:
    """Publish bot commands so Telegram clients show slash-command suggestions."""
    global _BOT_COMMANDS_SYNC_SIGNATURE

    config = _load_telegram_config()
    token = str(config.get("bot_token") or "").strip()
    if not token:
        return False

    commands = get_telegram_bot_commands()
    signature_source = f"{token}|{json.dumps(commands, sort_keys=True)}"
    signature = hashlib.sha256(signature_source.encode("utf-8")).hexdigest()
    if not force and _BOT_COMMANDS_SYNC_SIGNATURE == signature:
        return True

    result = await _telegram_api_call("setMyCommands", {"commands": commands})
    if result is None:
        return False

    _BOT_COMMANDS_SYNC_SIGNATURE = signature
    logger.info("Published Telegram bot commands")
    return True


async def validate_telegram_bot_token(bot_token: str) -> Optional[Dict[str, Any]]:
    """Validate a raw Telegram bot token by calling Telegram's getMe API."""
    token = str(bot_token or "").strip()
    if not token:
        return None

    url = f"{TELEGRAM_API_BASE_URL}/bot{token}/getMe"
    try:
        async with httpx.AsyncClient(timeout=TELEGRAM_TIMEOUT_SECONDS) as client:
            response = await client.post(url, json={})
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                logger.warning("Telegram token validation failed: %s", data.get("description", "Unknown error"))
                return None
            result = data.get("result") or {}
            if not result.get("is_bot"):
                logger.warning("Telegram token validation returned a non-bot account")
                return None
            return result
    except Exception as e:
        logger.error("Telegram token validation failed: %s", e, exc_info=True)
        return None


async def create_telegram_forum_topic(chat_id: int, name: str) -> Optional[Dict[str, Any]]:
    """Create a forum topic inside a Telegram supergroup."""
    topic_name = build_telegram_topic_name(name, "")
    result = await _telegram_api_call("createForumTopic", {
        "chat_id": chat_id,
        "name": topic_name,
    })
    if not isinstance(result, dict):
        return None
    try:
        result["message_thread_id"] = int(result.get("message_thread_id"))
    except (TypeError, ValueError):
        return None
    return result


async def edit_telegram_forum_topic(chat_id: int, message_thread_id: int, name: str) -> bool:
    """Rename an existing Telegram forum topic."""
    result = await _telegram_api_call("editForumTopic", {
        "chat_id": chat_id,
        "message_thread_id": int(message_thread_id),
        "name": build_telegram_topic_name(name, ""),
    })
    return result is not None


async def send_telegram_message(chat_id: int, text: str, message_thread_id: Optional[int] = None) -> bool:
    """Send a message to a Telegram chat.
    
    Args:
        chat_id: Telegram chat ID
        text: Message text (will be split if > 4096 chars)
        message_thread_id: Optional topic/thread ID for forum messages
    
    Returns:
        True if successful, False otherwise
    """
    if not text:
        logger.warning("Empty message text for chat_id %s", chat_id)
        return False
    
    # Split long messages
    messages = [text[i:i+TELEGRAM_MAX_MESSAGE_LENGTH] 
                for i in range(0, len(text), TELEGRAM_MAX_MESSAGE_LENGTH)]
    
    for msg in messages:
        payload = {
            "chat_id": chat_id,
            "text": msg,
            "parse_mode": "HTML",
        }
        if message_thread_id is not None:
            payload["message_thread_id"] = int(message_thread_id)
        result = await _telegram_api_call("sendMessage", payload)
        if result is None:
            logger.error("Failed to send Telegram message to chat_id %s", chat_id)
            return False
    
    return True


async def send_typing_indicator(chat_id: int, message_thread_id: Optional[int] = None) -> bool:
    """Send a typing indicator to a Telegram chat."""
    payload = {
        "chat_id": chat_id,
        "action": "typing",
    }
    if message_thread_id is not None:
        payload["message_thread_id"] = int(message_thread_id)
    result = await _telegram_api_call("sendChatAction", payload)
    return result is not None


def format_for_telegram(text: str) -> str:
    """Format text for Telegram HTML mode.
    
    Escape special HTML characters and truncate if needed.
    """
    if not text:
        return ""
    
    # Escape HTML special characters
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    
    # Truncate to max message length
    if len(text) > TELEGRAM_MAX_MESSAGE_LENGTH:
        text = text[:TELEGRAM_MAX_MESSAGE_LENGTH - 3] + "..."
    
    return text


def parse_telegram_message(update: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parse a Telegram message from an update.
    
    Supports text messages, documents, and photos.
    Returns dict with keys: 'chat_id', 'user_id', 'text', 'message_id', 'media_type' (optional)
    """
    try:
        message = update.get("message", {})
        if not message:
            return None
        
        chat = message.get("chat", {}) or {}
        chat_id = chat.get("id")
        user_id = message.get("from", {}).get("id")
        text = message.get("text")
        message_id = message.get("message_id")
        chat_type = str(chat.get("type") or "").strip().lower()
        chat_title = str(chat.get("title") or "").strip()
        if not chat_title and chat_type == "private":
            first = str(chat.get("first_name") or "").strip()
            last = str(chat.get("last_name") or "").strip()
            chat_title = " ".join(part for part in (first, last) if part).strip()
        message_thread_id = message.get("message_thread_id")
        try:
            message_thread_id = int(message_thread_id) if message_thread_id is not None else None
        except (TypeError, ValueError):
            message_thread_id = None
        is_topic_message = bool(message.get("is_topic_message") or message_thread_id is not None)
        topic_event = None
        topic_name = ""
        forum_topic_created = message.get("forum_topic_created")
        if isinstance(forum_topic_created, dict):
            topic_name = str(forum_topic_created.get("name") or "").strip()
            if topic_name:
                topic_event = "created"
                text = text or f"[Topic created: {topic_name}]"

        # Check for media attachments (documents, photos)
        media_type = None
        file_id = None
        file_name = None
        caption = message.get("caption", "")
        
        if message.get("document"):
            media_type = "document"
            file_id = message["document"].get("file_id")
            file_name = message["document"].get("file_name", "document")
            text = caption or f"[Document: {file_name}]"
        elif message.get("photo"):
            media_type = "photo"
            photos = message.get("photo", [])
            if photos:
                # Get highest resolution photo
                file_id = photos[-1].get("file_id")
            text = caption or "[Photo attached]"
        elif message.get("video"):
            media_type = "video"
            file_id = message["video"].get("file_id")
            text = caption or "[Video attached]"
        elif message.get("audio"):
            media_type = "audio"
            file_id = message["audio"].get("file_id")
            text = caption or "[Audio attached]"
        
        # Require either text message or media with caption
        if not text or not all([chat_id, user_id, message_id]):
            return None
        
        result = {
            "chat_id": chat_id,
            "user_id": user_id,
            "text": text,
            "message_id": message_id,
            "chat_type": chat_type,
            "chat_title": chat_title,
            "is_topic_message": is_topic_message,
        }
        if message_thread_id is not None:
            result["message_thread_id"] = message_thread_id
        if topic_event:
            result["topic_event"] = topic_event
        if topic_name:
            result["topic_name"] = topic_name
        
        if media_type:
            result["media_type"] = media_type
            result["file_id"] = file_id
            if file_name:
                result["file_name"] = file_name
        
        return result
    except Exception as e:
        logger.error("Error parsing Telegram message: %s", e, exc_info=True)
        return None


def validate_telegram_chat_id(chat_id: Any) -> bool:
    """Validate that chat_id is a valid integer."""
    try:
        int(chat_id)
        return True
    except (TypeError, ValueError):
        return False


def generate_linking_token() -> str:
    """Generate a secure random token for Telegram user linking.
    
    Used to prevent unauthorized linking attacks: user must have both the
    Telegram bot and Odysseus app to complete the linking.
    """
    return secrets.token_urlsafe(32)


def create_linking_state(telegram_user_id: int, telegram_chat_id: int) -> str:
    """Create a temporary linking state for a Telegram user.
    
    Returns a linking token that must be provided to the /api/telegram/link endpoint
    within 5 minutes, along with the odysseus_user credentials.
    
    Args:
        telegram_user_id: Telegram user ID
        telegram_chat_id: Telegram chat ID
    
    Returns:
        Linking token (UUID-like string)
    """
    token = generate_linking_token()
    import time
    created_ts = time.time()
    _LINKING_STATES[token] = {
        "telegram_user_id": int(telegram_user_id),
        "telegram_chat_id": int(telegram_chat_id),
        "created_at": datetime.now().isoformat(),
        "created_ts": created_ts,
    }
    
    # Cleanup old states (older than 5 minutes)
    now = time.time()
    expired = []
    for t, state in _LINKING_STATES.items():
        try:
            created_ts = float(state.get("created_ts", 0))
            if not created_ts:
                created = datetime.fromisoformat(state.get("created_at", ""))
                created_ts = created.timestamp()
            if (now - created_ts) > 300:  # 5 minutes
                expired.append(t)
        except Exception:
            expired.append(t)
    for t in expired:
        _LINKING_STATES.pop(t, None)
    
    return token


def verify_linking_token(token: str) -> Optional[Dict[str, Any]]:
    """Verify a linking token and return the associated Telegram IDs.
    
    Returns the linking state if valid, None otherwise.
    Removes the token from the state after verification (one-time use).
    """
    state = _LINKING_STATES.pop(token, None)
    if not state:
        logger.warning("Invalid or expired linking token")
        return None
    
    # Check if token is still fresh (within 5 minutes)
    try:
        import time
        created_ts = float(state.get("created_ts", 0))
        if not created_ts:
            created = datetime.fromisoformat(state.get("created_at", ""))
            created_ts = created.timestamp()
        if (time.time() - created_ts) > 300:
            logger.warning("Linking token expired")
            return None
    except Exception as e:
        logger.error("Error checking token age: %s", e)
        return None
    
    return state


def hash_telegram_user_id(telegram_user_id: int) -> str:
    """Create a non-reversible hash of a Telegram user ID for logging.
    
    Used to log Telegram activity without exposing user IDs in clear text.
    """
    return hashlib.sha256(str(telegram_user_id).encode()).hexdigest()[:16]


def should_reset_conversation(session_updated_at: Optional[datetime]) -> bool:
    """Check if a Telegram conversation session should be reset due to inactivity.
    
    Args:
        session_updated_at: Last update time of the session
    
    Returns:
        True if the session has been idle for longer than TELEGRAM_SESSION_TIMEOUT_MINUTES
    """
    if not session_updated_at:
        return False
    
    try:
        now = datetime.utcnow()
        idle_seconds = (now - session_updated_at).total_seconds()
        return idle_seconds > TELEGRAM_SESSION_TIMEOUT_SECONDS
    except Exception as e:
        logger.warning("Error checking session timeout: %s", e)
        return False
