import json
import os
import uuid
import logging
import re
from typing import Dict, List, Optional, Any

import httpx

from core.atomic_io import atomic_write_json
from core.platform_compat import safe_chmod
from src.secret_storage import decrypt, encrypt, is_encrypted

log = logging.getLogger(__name__)

DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "integrations.json")

# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

INTEGRATION_PRESETS: Dict[str, Dict[str, Any]] = {
    "miniflux": {
        "name": "Miniflux",
        "auth_type": "header",
        "auth_header": "X-Auth-Token",
        "description": (
            "Miniflux RSS reader (v1 API). Key endpoints:\n"
            "  GET /v1/feeds — list all feeds\n"
            "  GET /v1/feeds/{id} — get feed details\n"
            "  POST /v1/feeds — create feed {\"feed_url\": \"...\", \"category_id\": N}\n"
            "  PUT /v1/feeds/{id} — update feed\n"
            "  DELETE /v1/feeds/{id} — delete feed\n"
            "  GET /v1/feeds/{id}/entries — list entries for feed\n"
            "  GET /v1/entries — list all entries (params: status, limit, order, direction, category_id)\n"
            "  GET /v1/entries/{id} — get single entry\n"
            "  PUT /v1/entries — update entries {\"entry_ids\": [...], \"status\": \"read|unread\"}\n"
            "  GET /v1/categories — list categories\n"
            "  POST /v1/categories — create category {\"title\": \"...\"}\n"
            "  GET /v1/feeds/{id}/icon — get feed icon\n"
            "  PUT /v1/entries/{id}/bookmark — toggle bookmark"
        ),
    },
    "gitea": {
        "name": "Gitea",
        "auth_type": "header",
        "auth_header": "Authorization",
        "description": (
            "Gitea git forge API (v1). Auth header value format: 'token YOUR_TOKEN'. Key endpoints:\n"
            "  GET /api/v1/repos/search — search repositories\n"
            "  GET /api/v1/repos/{owner}/{repo} — get repo details\n"
            "  GET /api/v1/repos/{owner}/{repo}/issues — list issues\n"
            "  POST /api/v1/repos/{owner}/{repo}/issues — create issue {\"title\": \"...\"}\n"
            "  GET /api/v1/repos/{owner}/{repo}/pulls — list pull requests\n"
            "  GET /api/v1/repos/{owner}/{repo}/commits — list commits\n"
            "  GET /api/v1/user/repos — list your repos\n"
            "  GET /api/v1/orgs — list organizations\n"
            "  GET /api/v1/repos/{owner}/{repo}/contents/{filepath} — get file content"
        ),
    },
    "linkding": {
        "name": "Linkding",
        "auth_type": "header",
        "auth_header": "Authorization",
        "description": (
            "Linkding bookmark manager API. Auth header value format: 'Token YOUR_TOKEN'. Key endpoints:\n"
            "  GET /api/bookmarks/ — list bookmarks (params: q, limit, offset)\n"
            "  GET /api/bookmarks/{id}/ — get bookmark\n"
            "  POST /api/bookmarks/ — create bookmark {\"url\": \"...\", \"title\": \"...\", \"tag_names\": [...]}\n"
            "  PUT /api/bookmarks/{id}/ — update bookmark\n"
            "  DELETE /api/bookmarks/{id}/ — delete bookmark\n"
            "  GET /api/bookmarks/archived/ — list archived bookmarks\n"
            "  GET /api/tags/ — list tags"
        ),
    },
    "homeassistant": {
        "name": "Home Assistant",
        "auth_type": "bearer",
        "description": (
            "Home Assistant smart home API. Key endpoints:\n"
            "  GET /api/ — API status check\n"
            "  GET /api/states — list all entity states\n"
            "  GET /api/states/{entity_id} — get state of entity\n"
            "  POST /api/states/{entity_id} — update entity state\n"
            "  POST /api/services/{domain}/{service} — call service (e.g. light/turn_on)\n"
            "  GET /api/history/period/{timestamp} — get state history\n"
            "  GET /api/logbook/{timestamp} — get logbook entries\n"
            "  POST /api/events/{event_type} — fire event\n"
            "  GET /api/config — get configuration"
        ),
    },
    "ntfy": {
        "name": "ntfy",
        "auth_type": "none",
        "description": (
            "ntfy push notification service. Key endpoints:\n"
            "  POST /{topic} — send notification. Body is the message text.\n"
            "    Headers: Title (notification title), Priority (1-5), Tags (comma-separated emoji tags)\n"
            "  POST / — send JSON notification {\"topic\": \"...\", \"message\": \"...\", \"title\": \"...\", \"priority\": N}\n"
            "  GET /{topic}/json?poll=1 — poll for messages"
        ),
    },
    "discord_webhook": {
        "name": "Discord Webhook",
        "auth_type": "none",
        "description": (
            "Discord Incoming Webhook. Paste the full webhook URL (including the token) as the Base URL.\n"
            "To get a URL: Discord server -> Server Settings -> Integrations -> Webhooks -> New Webhook -> Copy Webhook URL.\n"
            "The secret is embedded in the URL — leave auth type as None.\n\n"
            "Use this integration as the target in Settings -> Reminders -> Webhook channel.\n"
            "Payload template examples:\n"
            "  Simple:  {\"content\": \"{{title}}: {{message}}\"}\n"
            "  Embed:   {\"embeds\": [{\"title\": \"{{title}}\", \"description\": \"{{message}}\", \"color\": 5793266}]}"
        ),
    },
    "vaultwarden": {
        "name": "Vaultwarden",
        "auth_type": "header",
        "auth_header": "Authorization",
        "description": (
            "Vaultwarden (Bitwarden-compatible) password manager API. Auth header value format: 'Bearer ACCESS_TOKEN'.\n"
            "To get an access token: POST /identity/connect/token with grant_type=client_credentials&client_id=...&client_secret=...\n"
            "Key endpoints:\n"
            "  GET /api/ciphers — list all vault items (logins, notes, cards, identities)\n"
            "  GET /api/ciphers/{id} — get a single vault item\n"
            "  POST /api/ciphers — create vault item {\"type\": 1, \"name\": \"...\", \"login\": {\"uri\": \"...\", \"username\": \"...\", \"password\": \"...\"}}\n"
            "  PUT /api/ciphers/{id} — update vault item\n"
            "  DELETE /api/ciphers/{id} — delete vault item\n"
            "  GET /api/folders — list folders\n"
            "  POST /api/folders — create folder {\"name\": \"...\"}\n"
            "  GET /api/collections — list collections (org vaults)\n"
            "  POST /api/ciphers/{id}/password-history — get password history\n"
            "  GET /api/sends — list Bitwarden Send items\n"
            "  POST /api/sends — create a Send (secure sharing)\n"
            "  Note: Vault data is end-to-end encrypted. The API returns encrypted fields\n"
            "  that must be decrypted client-side with the user's master key."
        ),
    },
    "freshrss": {
        "name": "FreshRSS",
        "auth_type": "header",
        "auth_header": "Authorization",
        "description": (
            "FreshRSS RSS reader (GReader API). Auth header value format: 'GoogleLogin auth=YOUR_TOKEN'. Key endpoints:\n"
            "  GET /api/greader.php/reader/api/0/subscription/list?output=json — list feeds\n"
            "  GET /api/greader.php/reader/api/0/stream/contents/feed/{feed_id}?output=json&n=20 — get entries\n"
            "  GET /api/greader.php/reader/api/0/tag/list?output=json — list tags/categories\n"
            "  POST /api/greader.php/reader/api/0/edit-tag — mark read/starred\n"
            "  GET /api/greader.php/reader/api/0/unread-count?output=json — unread counts"
        ),
    },
}

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def _ensure_data_dir() -> None:
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)


def _encrypt_integration_secrets(integrations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return storage-safe copies with API keys encrypted at rest."""
    safe: List[Dict[str, Any]] = []
    for item in integrations:
        copy = dict(item)
        api_key = copy.get("api_key", "")
        if api_key:
            copy["api_key"] = encrypt(str(api_key))
        safe.append(copy)
    return safe


def _decrypt_integration_secrets(integrations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return runtime copies with API keys decrypted for callers."""
    decoded: List[Dict[str, Any]] = []
    for item in integrations:
        copy = dict(item)
        api_key = copy.get("api_key", "")
        if api_key:
            copy["api_key"] = decrypt(str(api_key))
        decoded.append(copy)
    return decoded


def _has_plaintext_api_key(integrations: List[Dict[str, Any]]) -> bool:
    return any(
        bool(item.get("api_key")) and not is_encrypted(str(item.get("api_key")))
        for item in integrations
    )


def mask_integration_secret(integration: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy safe for API responses."""
    safe = dict(integration)
    api_key = safe.get("api_key", "")
    if api_key:
        safe["api_key"] = f"{str(api_key)[:4]}****"
    return safe


def load_integrations() -> List[Dict[str, Any]]:
    """Load all integrations from disk with secrets decrypted for runtime use."""
    if not os.path.exists(DATA_FILE):
        return []
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            integrations = json.load(f)
        if not isinstance(integrations, list):
            log.error("Invalid integrations file shape: expected a list")
            return []
        valid_integrations = [item for item in integrations if isinstance(item, dict)]
        if len(valid_integrations) != len(integrations):
            log.error("Invalid integrations file rows: ignored non-object entries")
        integrations = valid_integrations
        if _has_plaintext_api_key(integrations):
            save_integrations(_decrypt_integration_secrets(integrations))
        return _decrypt_integration_secrets(integrations)
    except (json.JSONDecodeError, IOError) as exc:
        log.error("Failed to load integrations: %s", exc)
        return []


def save_integrations(integrations: List[Dict[str, Any]]) -> None:
    """Persist integrations list to disk with API keys encrypted at rest."""
    _ensure_data_dir()
    atomic_write_json(DATA_FILE, _encrypt_integration_secrets(integrations), indent=2)
    safe_chmod(DATA_FILE, 0o600)


def get_integration(integration_id: str) -> Optional[Dict[str, Any]]:
    """Get a single integration by id."""
    for item in load_integrations():
        if item.get("id") == integration_id:
            return item
    return None


def add_integration(data: Dict[str, Any]) -> Dict[str, Any]:
    """Add a new integration. If 'preset' is given, merge preset defaults first."""
    integration: Dict[str, Any] = {}

    preset_key = data.get("preset")
    if preset_key and preset_key in INTEGRATION_PRESETS:
        integration.update(INTEGRATION_PRESETS[preset_key])
        integration["preset"] = preset_key

    integration.update(data)
    integration.setdefault("id", uuid.uuid4().hex[:12])
    integration.setdefault("enabled", True)
    integration.setdefault("auth_type", "none")
    integration.setdefault("auth_header", "")
    integration.setdefault("auth_param", "")
    integration.setdefault("description", "")
    integration.setdefault("api_key", "")
    integration.setdefault("name", "")
    integration.setdefault("base_url", "")

    integrations = load_integrations()
    integrations.append(integration)
    save_integrations(integrations)
    return integration


def update_integration(integration_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Update fields on an existing integration. Returns updated integration or None."""
    integrations = load_integrations()
    for item in integrations:
        if item.get("id") == integration_id:
            data.pop("id", None)  # prevent id change
            item.update(data)
            save_integrations(integrations)
            return item
    return None


def delete_integration(integration_id: str) -> bool:
    """Delete an integration by id. Returns True if found and deleted."""
    integrations = load_integrations()
    original_len = len(integrations)
    integrations = [i for i in integrations if i.get("id") != integration_id]
    if len(integrations) < original_len:
        save_integrations(integrations)
        return True
    return False


# ---------------------------------------------------------------------------
# API execution
# ---------------------------------------------------------------------------

def _strip_html_tags(html: str) -> str:
    """Rough HTML tag stripping."""
    text = re.sub(r"<[^>]+>", "", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _find_integration(identifier: str) -> Optional[Dict[str, Any]]:
    """Find integration by id or name (case-insensitive)."""
    integrations = load_integrations()
    # try id first
    for item in integrations:
        if item.get("id") == identifier:
            return item
    # try name
    lower = identifier.lower()
    for item in integrations:
        if item.get("name", "").lower() == lower:
            return item
    return None


async def execute_api_call(
    integration_id: str,
    method: str,
    path: str,
    params: Optional[Dict[str, Any]] = None,
    body: Optional[Any] = None,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Execute an HTTP request against a registered integration."""

    integration = _find_integration(integration_id)
    if not integration:
        return {"error": f"Integration not found: {integration_id}", "exit_code": 1}

    if not integration.get("enabled", True):
        return {"error": f"Integration '{integration.get('name')}' is disabled", "exit_code": 1}

    base_url = integration.get("base_url", "").rstrip("/")
    if not base_url:
        return {"error": "Integration has no base_url configured", "exit_code": 1}

    # Strip common API path suffixes users might accidentally include
    # (e.g. "http://host/v1/" → "http://host"). The integration's preset
    # endpoints include the full path, so the base should be bare.
    preset = (integration.get("preset") or integration.get("name", "")).lower()
    strip_suffixes = {
        "miniflux": ["/v1"],
        "gitea": ["/api/v1", "/api"],
        "linkding": ["/api"],
        "homeassistant": ["/api"],
    }
    for suf in strip_suffixes.get(preset, []):
        if base_url.endswith(suf):
            base_url = base_url[: -len(suf)]
            break

    # Validate path
    if not path.startswith("/"):
        return {"error": "Path must start with /", "exit_code": 1}
    if re.search(r"^https?://", path) or "://" in path:
        return {"error": "Path must not contain a protocol scheme", "exit_code": 1}

    url = base_url + path
    method = method.upper()

    # Build headers
    headers: Dict[str, str] = {}
    if extra_headers:
        headers.update(extra_headers)

    api_key = integration.get("api_key", "")
    auth_type = integration.get("auth_type", "none")

    if auth_type == "header" and api_key:
        # Fall back based on preset/name when auth_header is unset or empty
        header_name = integration.get("auth_header") or ""
        if not header_name:
            preset = (integration.get("preset") or integration.get("name", "")).lower()
            header_defaults = {
                "miniflux": "X-Auth-Token",
                "linkding": "Authorization",
                "gitea": "Authorization",
            }
            header_name = header_defaults.get(preset, "Authorization")
        headers[header_name] = api_key
    elif auth_type == "bearer" and api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    elif auth_type == "query" and api_key:
        if params is None:
            params = {}
        param_name = integration.get("auth_param", "api_key")
        params[param_name] = api_key

    # auth_type == "basic" — expects api_key as "user:password"
    auth = None
    if auth_type == "basic" and api_key:
        parts = api_key.split(":", 1)
        if len(parts) == 2:
            auth = httpx.BasicAuth(parts[0], parts[1])

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method,
                url,
                params=params,
                json=body if body is not None else None,
                headers=headers,
                auth=auth,
            )

        content_type = response.headers.get("content-type", "")
        status = response.status_code

        # Format response body
        if "application/json" in content_type:
            try:
                data = response.json()
                formatted = json.dumps(data, indent=2, ensure_ascii=False)
            except (json.JSONDecodeError, ValueError):
                formatted = response.text
        elif "text/html" in content_type:
            formatted = _strip_html_tags(response.text)
        else:
            formatted = response.text

        # Truncate
        if len(formatted) > 12000:
            formatted = formatted[:12000] + "\n... (truncated)"

        output = f"HTTP {status}\n{formatted}"

        if status >= 400:
            return {"error": output, "exit_code": 1}

        return {"output": output, "exit_code": 0}

    except httpx.TimeoutException:
        return {"error": f"Request to {integration.get('name')} timed out", "exit_code": 1}
    except httpx.RequestError as exc:
        return {"error": f"Request failed: {exc}", "exit_code": 1}
    except Exception as exc:
        log.exception("Unexpected error in execute_api_call")
        return {"error": f"Unexpected error: {exc}", "exit_code": 1}


# ---------------------------------------------------------------------------
# System prompt helper
# ---------------------------------------------------------------------------

def get_integrations_prompt() -> str:
    """Return a string describing all enabled integrations for system prompt injection.

    Returns empty string if no integrations are enabled.
    """
    integrations = load_integrations()
    enabled = [i for i in integrations if i.get("enabled", True)]
    if not enabled:
        return ""

    lines = ["You have access to the following API integrations via the api_call tool:\n"]
    for integ in enabled:
        name = integ.get("name", integ.get("id", "unknown"))
        lines.append(f"## {name} (id: {integ['id']})")
        desc = integ.get("description", "")
        if desc:
            lines.append(desc)
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def migrate_from_settings() -> None:
    """If data/settings.json has miniflux_url and miniflux_api_key, create a
    Miniflux integration and clear those keys from settings."""
    settings_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "settings.json")
    if not os.path.exists(settings_path):
        return

    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            settings = json.load(f)
    except (json.JSONDecodeError, IOError):
        return

    miniflux_url = settings.get("miniflux_url", "")
    miniflux_key = settings.get("miniflux_api_key", "")

    if not miniflux_url or not miniflux_key:
        return

    # Check if a miniflux integration already exists
    existing = load_integrations()
    for item in existing:
        if item.get("preset") == "miniflux":
            log.info("Miniflux integration already exists, skipping migration")
            return

    add_integration({
        "preset": "miniflux",
        "base_url": miniflux_url.rstrip("/"),
        "api_key": miniflux_key,
    })

    # Clear migrated keys
    settings.pop("miniflux_url", None)
    settings.pop("miniflux_api_key", None)
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)

    log.info("Migrated Miniflux integration from settings.json")
