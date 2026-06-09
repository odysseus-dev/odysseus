# src/constants.py
"""Application-wide constants and configuration values."""
import os

APP_VERSION = "1.0.0"

# Base paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/"
STATIC_DIR = os.path.join(BASE_DIR, "static")
DATA_DIR = os.getenv("ODYSSEUS_DATA_DIR", os.path.join(BASE_DIR, "data"))

# Data file paths
# Single source of truth: every persisted file/dir lives under DATA_DIR, which
# is the ONLY place ODYSSEUS_DATA_DIR is read. Import these constants instead of
# re-deriving paths from __file__ or a relative "data" literal.
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions.json")
MEMORY_FILE = os.path.join(DATA_DIR, "memory.json")
MEMORY_DOC = os.path.join(DATA_DIR, "memory_doc.md")
PERSONAL_DIR = os.path.join(DATA_DIR, "personal_docs")
RUNBOOK_DIR = os.path.join(PERSONAL_DIR, "runbook")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
FEATURES_FILE = os.path.join(DATA_DIR, "features.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
AUTH_FILE = os.path.join(DATA_DIR, "auth.json")
USER_PREFS_FILE = os.path.join(DATA_DIR, "user_prefs.json")
PRESETS_FILE = os.path.join(DATA_DIR, "presets.json")
INTEGRATIONS_FILE = os.path.join(DATA_DIR, "integrations.json")
CONTACTS_FILE = os.path.join(DATA_DIR, "contacts.json")
APP_KEY_FILE = os.path.join(DATA_DIR, ".app_key")
EMBEDDING_ENDPOINT_FILE = os.path.join(DATA_DIR, "embedding_endpoint.json")
COOKBOOK_STATE_FILE = os.path.join(DATA_DIR, "cookbook_state.json")
BG_JOBS_FILE = os.path.join(DATA_DIR, "bg_jobs.json")
VAULT_FILE = os.path.join(DATA_DIR, "vault.json")
TIDY_CALENDAR_STATE_FILE = os.path.join(DATA_DIR, "tidy_calendar_state.json")
SKILLS_FILE = os.path.join(DATA_DIR, "skills.json")
APP_DB = os.path.join(DATA_DIR, "app.db")
SCHEDULED_EMAILS_DB = os.path.join(DATA_DIR, "scheduled_emails.db")
EMAIL_CACHE_DB = os.path.join(DATA_DIR, "email_cache.db")

# Data subdirectories
PERSONAL_UPLOADS_DIR = os.path.join(DATA_DIR, "personal_uploads")
EMOJI_CACHE_DIR = os.path.join(DATA_DIR, "emoji_cache")
RAG_DIR = os.path.join(DATA_DIR, "rag")
CHROMA_DIR = os.path.join(DATA_DIR, "chroma")
BG_JOBS_DIR = os.path.join(DATA_DIR, "bg_jobs")
DEEP_RESEARCH_DIR = os.path.join(DATA_DIR, "deep_research")
MCP_OAUTH_DIR = os.path.join(DATA_DIR, "mcp_oauth")
GENERATED_IMAGES_DIR = os.path.join(DATA_DIR, "generated_images")
TTS_CACHE_DIR = os.path.join(DATA_DIR, "tts_cache")
EMAIL_URGENCY_CACHE_DIR = os.path.join(DATA_DIR, "email_urgency_cache")
SKILLS_DIR = os.path.join(DATA_DIR, "skills")
GALLERY_DIR = os.path.join(DATA_DIR, "gallery")
GALLERY_UPLOADS_DIR = os.path.join(DATA_DIR, "gallery_uploads")
MEMORY_VECTORS_DIR = os.path.join(DATA_DIR, "memory_vectors")

# Paths with an intentional dedicated env override, defaulting under DATA_DIR.
MAIL_ATTACHMENTS_DIR = os.getenv("ODYSSEUS_MAIL_ATTACHMENTS_DIR", os.path.join(DATA_DIR, "mail-attachments"))
FASTEMBED_CACHE_DIR = os.getenv("FASTEMBED_CACHE_PATH", os.path.join(DATA_DIR, "fastembed_cache"))

# Agent tool output limits (single source of truth — imported by tool_execution.py,
# tool_implementations.py, agent_tools.py, and any other module that needs them)
MAX_OUTPUT_CHARS = 10_000       # cap for bash/python/web_search/web_fetch output
MAX_READ_CHARS = 20_000         # cap for read_file / document preview
MAX_DIFF_LINES = 400            # cap for edit_file unified-diff display

# API Configuration
MAX_CONTEXT_MESSAGES = 90
REQUEST_TIMEOUT = 20
OPENAI_COMPAT_PATH = "/v1/chat/completions"

# Environment variables with defaults
DEFAULT_HOST = os.getenv("LLM_HOST", "localhost")
LLM_HOSTS = [h.strip() for h in os.getenv("LLM_HOSTS", "").split(",") if h.strip()]
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SEARXNG_INSTANCE = os.getenv("SEARXNG_INSTANCE", "http://localhost:8080")


# Cleanup configuration
CLEANUP_ENABLED = os.getenv("CLEANUP_ENABLED", "True").lower() == "true"
CLEANUP_INTERVAL_HOURS = int(os.getenv("CLEANUP_INTERVAL_HOURS", "24"))

# Default parameters
DEFAULT_TEMPERATURE = 1.0
DEFAULT_MAX_TOKENS = 0


def internal_api_base() -> str:
    """Base URL for in-process loopback calls to Odysseus's own API.

    Agent tools and background jobs reach admin-gated routes by calling the
    running server over HTTP. Resolution order:
      1. ODYSSEUS_INTERNAL_BASE  - explicit override (e.g. behind a TLS proxy).
      2. APP_PORT                - http://127.0.0.1:$APP_PORT (docker-compose).
      3. Fallback http://127.0.0.1:7000 - legacy default.

    127.0.0.1 (not "localhost") avoids IPv6/DNS ambiguity for a strictly-local
    call. Without this, loopback tools fail with "All connection attempts
    failed" whenever the server is not on port 7000.
    """
    override = os.environ.get("ODYSSEUS_INTERNAL_BASE")
    if override:
        return override.rstrip("/")
    return f"http://127.0.0.1:{os.environ.get('APP_PORT', '7000')}"
