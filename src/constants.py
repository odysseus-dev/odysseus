# src/constants.py
"""Application-wide constants and configuration values."""
import os
import subprocess

from src.runtime_paths import get_app_root, get_default_data_dir

APP_VERSION = "1.0.3"
BUILTIN_SKILLS_DIR = os.path.join(get_app_root(), "resources", "skills")
# Identifies the private maintainer-preview build without changing the public
# application semver used by release and readiness checks. Keep the API/UI
# value tied to HARNESS_VERSION so a version bump cannot leave the running
# service claiming an older harness build.
def _load_build_version() -> str:
    override = os.getenv("ODYSSEUS_BUILD_VERSION", "").strip()
    if override:
        return override
    try:
        with open(os.path.join(get_app_root(), "HARNESS_VERSION"), encoding="utf-8") as fh:
            value = fh.read().strip()
            if value:
                return value
    except OSError:
        pass
    return "unknown"


APP_BUILD_VERSION = _load_build_version()


def _load_source_commit() -> str:
    """Identify the source tree loaded by this process for runtime provenance."""
    override = os.getenv("ODYSSEUS_SOURCE_COMMIT", "").strip()
    if override:
        return override
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=get_app_root(),
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    commit = result.stdout.strip()
    return commit if result.returncode == 0 and commit else "unknown"


APP_SOURCE_COMMIT = _load_source_commit()

# Base paths
BASE_DIR = os.path.join(get_app_root(), "")
STATIC_DIR = os.path.join(BASE_DIR, "static")
DATA_DIR = os.getenv("ODYSSEUS_DATA_DIR", get_default_data_dir())

# Data file paths
# Single source of truth: every persisted file/dir lives under DATA_DIR, which
# is the ONLY place ODYSSEUS_DATA_DIR is read. Import these constants instead of
# re-deriving paths from __file__ or a relative "data" literal.
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions.json")
MEMORY_FILE = os.path.join(DATA_DIR, "memory.json")
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
CONTAINMENT_STATE_FILE = os.path.join(DATA_DIR, "containment_grants.json")
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
PROCESS_RESOURCES_DIR = os.path.join(DATA_DIR, "process_resources")
BROWSER_RESOURCES_DIR = os.path.join(DATA_DIR, "browser_resources")
DEEP_RESEARCH_DIR = os.path.join(DATA_DIR, "deep_research")
MCP_OAUTH_DIR = os.path.join(DATA_DIR, "mcp_oauth")
GENERATED_IMAGES_DIR = os.path.join(DATA_DIR, "generated_images")
TTS_CACHE_DIR = os.path.join(DATA_DIR, "tts_cache")
EMAIL_URGENCY_CACHE_DIR = os.path.join(DATA_DIR, "email_urgency_cache")
SKILLS_DIR = os.path.join(DATA_DIR, "skills")
GALLERY_DIR = os.path.join(DATA_DIR, "gallery")
GALLERY_UPLOADS_DIR = os.path.join(DATA_DIR, "gallery_uploads")
MEMORY_VECTORS_DIR = os.path.join(DATA_DIR, "memory_vectors")

# The only part of DATA_DIR the agent's file tools and subprocesses may touch.
# Everything else under DATA_DIR is application state (session store, auth
# database, encryption key, settings), and the agent has no business reading it.
AGENT_WORKSPACE_DIR = os.path.join(DATA_DIR, "agent_workspace")

# Paths with an intentional dedicated env override, defaulting under DATA_DIR.
MAIL_ATTACHMENTS_DIR = os.getenv("ODYSSEUS_MAIL_ATTACHMENTS_DIR", os.path.join(DATA_DIR, "mail-attachments"))
# `or` (not os.getenv's default arg) so a PRESENT-but-EMPTY value falls back to
# the default. docker-compose.yml injects `FASTEMBED_CACHE_PATH=${FASTEMBED_CACHE_PATH:-}`,
# which sets the var to "" when the host hasn't defined it. os.getenv(name, default)
# only returns the default when the var is ABSENT, so the empty string would win →
# os.makedirs("") raises [Errno 2] No such file or directory: '' → FastEmbed fails to
# init and all vector features (RAG, semantic memory, tool index) silently degrade.
FASTEMBED_CACHE_DIR = os.getenv("FASTEMBED_CACHE_PATH") or os.path.join(DATA_DIR, "fastembed_cache")

# Agent tool output limits (single source of truth — imported by tool_execution.py,
# tool_implementations.py, agent_tools.py, and any other module that needs them)
MAX_OUTPUT_CHARS = 10_000       # cap for bash/python/web_search/web_fetch output
MAX_READ_CHARS = 20_000         # cap for read_file / document preview
MAX_DIFF_LINES = 400            # cap for edit_file unified-diff display

# web_fetch response-size policy (#3812). MAX_OUTPUT_CHARS above only trims
# what the agent SEES; these caps bound what the server downloads, parses,
# and writes to the content cache. The soft cap is the default download
# budget; the agent can raise it per call (full/max_bytes) but never past
# the hard cap, so a model can't decide to pull a multi-GB file.
WEB_FETCH_SOFT_MAX_BYTES = 2_000_000    # default download budget (2 MB)
WEB_FETCH_HARD_MAX_BYTES = 20_000_000   # absolute ceiling, even with override (20 MB)

# API Configuration
MAX_CONTEXT_MESSAGES = 90
REQUEST_TIMEOUT = 20
OPENAI_COMPAT_PATH = "/v1/chat/completions"

# Outbound UA for web_fetch / web_search scraping; common desktop UA so pages serve normal HTML.
WEB_FETCH_USER_AGENT = os.environ.get(
    "WEB_FETCH_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
)

# Environment variables with defaults
DEFAULT_HOST = os.getenv("LLM_HOST", "localhost")
LLM_HOSTS = [h.strip() for h in os.getenv("LLM_HOSTS", "").split(",") if h.strip()]
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SEARXNG_INSTANCE = os.getenv("SEARXNG_INSTANCE", "http://localhost:8080")

# Scholarly title resolution. These are the only third-party metadata APIs the
# search path calls directly, so they get named constants rather than literals
# repeated at each call site. The budget bounds the whole SearXNG -> OpenAlex ->
# arXiv chain: each hop used to get its own full timeout, so one scholarly query
# could stall a user-facing search for the sum of all three.
ARXIV_API_URL = "https://export.arxiv.org/api/query"
OPENALEX_API_URL = "https://api.openalex.org/works"
SCHOLARLY_LOOKUP_TIMEOUT = 12.0
SCHOLARLY_LOOKUP_TOTAL_BUDGET = 20.0

# Cleanup configuration
CLEANUP_ENABLED = os.getenv("CLEANUP_ENABLED", "True").lower() == "true"
CLEANUP_INTERVAL_HOURS = int(os.getenv("CLEANUP_INTERVAL_HOURS", "24"))

# Agent workspace
# The stable virtual root the tool contract promises an agent, independent of
# where the workspace physically lives. Both the mount namespace and the
# path resolvers map it to the active workspace, so it is the one absolute path
# a contained command may assume.
WORKSPACE_MOUNT = "/workspace"
# Scratch directory inside the workspace that agent shell commands get in place
# of the host /tmp. A dirname rather than a path: the workspace is dynamic, so
# the full path is only knowable per turn.
AGENT_ISOLATED_TMP_DIRNAME = ".tmp"

# Auth policy
PASSWORD_MIN_LENGTH = 8

# Default parameters
DEFAULT_TEMPERATURE = 1.0
DEFAULT_MAX_TOKENS = 32768
# Vision/OCR calls need more headroom than a plain caption would suggest:
# thinking-capable models (Gemma 4, Qwen3-VL Thinking) spend the budget in
# `reasoning` and return empty `content` if capped too low.
VISION_MAX_TOKENS = 2000


def internal_api_base() -> str:
    """Base URL for in-process loopback calls to Odysseus's own API.

    Agent tools and background jobs reach admin-gated routes by calling the
    running server over HTTP. Resolution order:
      1. ODYSSEUS_INTERNAL_BASE  - explicit override (e.g. behind a TLS proxy).
      2. APP_PORT                - http://127.0.0.1:$APP_PORT (docker-compose).
      3. Fallback http://127.0.0.1:7011 - matches app.py's bind default.

    127.0.0.1 (not "localhost") avoids IPv6/DNS ambiguity for a strictly-local
    call. Without this, loopback tools fail with "All connection attempts
    failed" whenever the server is not on port 7000.
    """
    override = os.environ.get("ODYSSEUS_INTERNAL_BASE")
    if override:
        return override.rstrip("/")
    return f"http://127.0.0.1:{os.environ.get('APP_PORT', '7011')}"

# Shared prompt for all vision/OCR description calls (chat attachments and
# gallery captions) so both paths produce the same two-part shape: a short
# summary the UI can show collapsed, then the full description.
VISION_DESCRIBE_PROMPT = (
    "Describe this image in plain text: a one or two sentence overview, "
    "a blank line, then everything else you see. Transcribe any readable "
    "text exactly; don't guess at anything unclear."
)
