"""Optional guard-core perimeter for Odysseus.

Wires the ``fastapi-guard`` / ``guard-core`` engine in as an opt-in outer
perimeter: per-IP rate-limit ceilings, WAF/recon detection, honeypot auto-ban,
and a log-only signal for credentials or prompt-injection markers written into
the searchable corpus. Disabled by default.

Contract:
- When ``ODYSSEUS_GUARD_ENABLED`` is not ``true`` the guard packages are never
  imported and ``security_config`` / ``guard_deco`` stay ``None``. Odysseus then
  behaves exactly as if this module did not exist, and ``fastapi-guard`` need not
  be installed.
- Odysseus already owns security headers, CORS, auth, outbound SSRF validation,
  secret redaction, owner-scope, and upload size caps. The perimeter disables
  its overlapping subsystems (headers/CORS) and augments the rest.
- It ships in ``passive_mode`` (log-only) until an operator reviews the guard
  WARNING lines in the app log and sets ``ODYSSEUS_GUARD_PASSIVE=false``. This mirrors Odysseus's stance
  of wrapping untrusted content rather than rejecting it (src/prompt_security.py).

Content inspection is done by a single global ``custom_request_check`` so it
covers every route, including path-parameter ones (e.g. ``/api/memory/{id}``),
without a decorator on each. It only ever logs. The per-route decorators in
core/guard_deco.py add tighter, per-surface controls on top of it.
"""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger("odysseus.guard")

GUARD_ENABLED = os.getenv("ODYSSEUS_GUARD_ENABLED", "false").lower() == "true"


def _passive_mode() -> bool:
    return os.getenv("ODYSSEUS_GUARD_PASSIVE", "true").lower() != "false"


def _emergency_mode() -> bool:
    return os.getenv("ODYSSEUS_GUARD_EMERGENCY", "false").lower() == "true"


def _block_clouds() -> bool:
    return os.getenv("ODYSSEUS_GUARD_BLOCK_CLOUDS", "false").lower() == "true"


def _env_list(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


class _MaxMindGeoIPHandler:
    """GeoIPHandler (guard-core protocol) backed by a MaxMind country database.

    Opt-in and only constructed when ODYSSEUS_GUARD_GEOIP_DB points at a readable
    GeoLite2/GeoIP2 country .mmdb. ``get_country`` returns ``None`` rather than
    raising when an IP can't be resolved, as the protocol requires.
    """

    def __init__(self, db_path: str) -> None:
        import maxminddb

        self._reader = maxminddb.open_database(db_path)

    @property
    def is_initialized(self) -> bool:
        return self._reader is not None

    async def initialize(self) -> None:
        return None

    async def initialize_redis(self, redis_handler: object) -> None:
        return None

    async def initialize_agent(self, agent_handler: object) -> None:
        return None

    def get_country(self, ip: str) -> str | None:
        try:
            record = self._reader.get(ip)
        except Exception:
            return None
        if isinstance(record, dict):
            country = record.get("country") or record.get("registered_country")
            if isinstance(country, dict):
                code = country.get("iso_code")
                if isinstance(code, str):
                    return code
        return None

    async def refresh(self) -> None:
        return None

    def close(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None


def _geoip_handler(blocked_countries: list[str]):
    db_path = os.getenv("ODYSSEUS_GUARD_GEOIP_DB", "").strip()
    if not blocked_countries:
        return None
    if not db_path:
        logger.warning(
            "guard: ODYSSEUS_GUARD_BLOCK_COUNTRIES needs ODYSSEUS_GUARD_GEOIP_DB; ignored"
        )
        return None
    try:
        return _MaxMindGeoIPHandler(db_path)
    except Exception:
        logger.warning("guard: could not open GeoIP DB %r; country blocking disabled", db_path)
        return None


_CREDENTIAL_PATTERNS = [
    re.compile(r"sk-ant-[a-zA-Z0-9\-_]{15,}"),
    re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"postgres(?:ql)?://[^:\s]+:[^@\s]{4,}@"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]

_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|above|prior)\s+(?:instructions?|prompts?)", re.I),
    re.compile(r"you\s+are\s+now\s+(?:a|an|the|DAN|developer\s+mode)", re.I),
    re.compile(r"disregard\s+(?:your|the)\s+(?:system\s+prompt|rules|guidelines)", re.I),
    re.compile(r"<\|(?:im_start|im_end|system|user|assistant)\|>", re.I),
    re.compile(r"\[INST\]|\[/INST\]|\[\[SYSTEM\]\]", re.I),
]

# Paths whose bodies land in the searchable/RAG corpus: scan for secrets being
# persisted where a later agent read could surface them. Deliberately excludes
# the key-config routes (session, model-endpoints, embeddings, auth/integrations,
# v1/chat) that legitimately carry API keys.
_CORPUS_WRITE_RE = re.compile(
    r"^/api/(?:memory|notes|documents?|skills|personal|import|codex/(?:memory|documents))(?=/|$)"
)

# Paths that set stored instructions later replayed to an LLM/agent.
_INSTRUCTION_WRITE_RE = re.compile(
    r"^/api/(?:assistant/settings|skills/builtin|tasks|chat|chat_stream|v1/chat)(?=/|$)"
)

_SCAN_BYTES = 65536


def _has_credential(raw: str) -> bool:
    return any(pattern.search(raw) for pattern in _CREDENTIAL_PATTERNS)


def _has_injection(raw: str) -> bool:
    return any(pattern.search(raw) for pattern in _INJECTION_PATTERNS)


async def _global_content_scan(request):
    """Global, log-only content scanner (fires on every request, incl. param routes).

    Emits a warning when a credential format is being written into the corpus, or
    when a role-override marker appears in a stored-instruction body. Never blocks;
    returns None so the request always proceeds.
    """
    try:
        if request.method not in ("POST", "PUT", "PATCH"):
            return None
        path = request.url_path
        scan_credentials = bool(_CORPUS_WRITE_RE.match(path))
        scan_injection = bool(_INSTRUCTION_WRITE_RE.match(path))
        if not (scan_credentials or scan_injection):
            return None
        if "multipart" in request.headers.get("content-type", "").lower():
            return None
        raw = (await request.body())[:_SCAN_BYTES].decode("utf-8", "ignore")
        if scan_credentials and _has_credential(raw):
            logger.warning("guard: credential-format content written to corpus at %s %s", request.method, path)
        if scan_injection and _has_injection(raw):
            logger.warning("guard: prompt-injection marker in stored instruction at %s %s", request.method, path)
    except Exception:
        return None
    return None


security_config = None
guard_deco = None


if GUARD_ENABLED:
    from guard import SecurityConfig, SecurityDecorator, SecurityMiddleware
    from guard_core.models import ThreatBanConfig

    class GuardMiddleware(SecurityMiddleware):
        """SecurityMiddleware that logs through Odysseus's handlers only.

        guard-core attaches its own console handler to the ``guard_core`` logger
        and still propagates to the root logger, so every event would otherwise
        print twice, and only the propagated copy reaches Odysseus's log file.
        """

        def __init__(self, app, *, config) -> None:
            super().__init__(app, config=config)
            for handler in self.logger.handlers[:]:
                self.logger.removeHandler(handler)

    _passive = _passive_mode()
    _proxies = _env_list("ODYSSEUS_GUARD_TRUSTED_PROXIES")
    _blocked_countries = _env_list("ODYSSEUS_GUARD_BLOCK_COUNTRIES")
    _geo = _geoip_handler(_blocked_countries)
    if _geo is None:
        _blocked_countries = []

    security_config = SecurityConfig(
        trusted_proxies=_proxies,
        trusted_proxy_depth=1,
        trust_x_forwarded_proto=bool(_proxies),
        enable_redis=False,
        passive_mode=_passive,
        # Fail open while calibrating in passive mode so a WAF edge case on
        # legitimate AI content can never turn into a 500; fail secure once
        # an operator flips to active enforcement.
        fail_secure=not _passive,
        enforce_https=False,
        # Opt-in geo/cloud perimeter (default off). Effective only when guard
        # sees the real client IP: direct exposure, or a proxy declared via
        # ODYSSEUS_GUARD_TRUSTED_PROXIES. block_clouds sheds cloud-hosted
        # crawlers/scrapers; blocked_countries needs a MaxMind DB.
        block_cloud_providers=({"AWS", "GCP", "Azure"} if _block_clouds() else None),
        geo_ip_handler=_geo,
        blocked_countries=frozenset(_blocked_countries),
        exclude_paths=[
            "/static",
            "/api/health",
            "/api/version",
            "/login",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/favicon.ico",
        ],
        security_headers={"enabled": False},
        enable_cors=False,
        enable_rate_limiting=True,
        rate_limit=300,
        rate_limit_window=60,
        endpoint_rate_limits={
            "/api/shell/exec": (10, 60),
            "/api/shell/stream": (10, 60),
            "/api/mcp/servers": (10, 60),
            "/api/vault/unlock": (5, 60),
            "/api/vault/login": (5, 60),
            "/api/tokens": (10, 60),
            "/api/v1/chat": (30, 60),
            "/api/export": (5, 300),
            "/api/import": (5, 300),
        },
        enable_penetration_detection=True,
        # Fields whose values are legitimately code / prose / paths / URLs /
        # commands in this AI workspace and would otherwise trip the WAF on
        # normal use. One central allowlist: guard-core matches these names in
        # JSON bodies at any depth, in x-www-form-urlencoded fields, and in
        # multipart text parts, so it covers every route. Since guard-core 3.13
        # multipart file parts are scanned too (filename and decodable payload),
        # which is why the upload routes carry @no_waf.
        # Credentials are excluded outright: a strong password is random bytes
        # and must never be pattern-matched (";rm" inside one blocked login).
        excluded_detection_body_fields={
            "password", "new_password", "current_password", "master_password",
            "bw_password", "api_key", "secret", "totp_code",
            "message", "content", "text", "prompt", "personality", "procedure",
            "pitfalls", "solution", "when_to_use", "description", "query",
            "payload", "thumbnail", "items", "instruction", "original_text",
            "body", "code", "diff", "command", "cmd", "args",
            "title", "name", "subject", "label", "tags", "topic", "summary",
            "notes", "steps", "verification", "system_prompt",
            "messages", "metadata", "value", "markdown", "task", "problem",
            "body_extra", "original_body", "user_hint", "style", "location",
            "address", "directory", "local_dir", "include", "env_prefix",
            "url", "endpoint_url", "base_url", "server_url", "carddav_url",
            "_endpoint", "vcf", "csv", "pip", "cron_expression", "avatar",
            "webhook_payload_template", "llm_persona",
            "memories", "presets", "skills", "settings", "preferences",
            "context", "workspace", "approved_plan", "search_context",
            "target_language", "email_translate_language",
        },
        excluded_detection_headers={
            "authorization", "x-api-key", "x-auth-token",
            "x-odysseus-internal-token", "x-odysseus-owner", "x-tz-offset",
        },
        # Free-text search and mailbox-folder query parameters. A user searching
        # their own history for "DROP TABLE" is not an attack. Path-like params
        # (path, filepath) stay scanned so traversal detection still applies.
        excluded_detection_params={"q", "search", "filter", "folder", "dest", "sort"},
        detection_max_body_inspect_bytes=65536,
        detection_max_content_length=10000,
        detection_threat_score_threshold=1.2,
        detection_semantic_threshold=0.75,
        enable_ip_banning=True,
        # Rate-limit violators feed the same ban engine as WAF hits (guard-core
        # 3.13+); a client that keeps hammering a ceiling is banned, not just 429'd.
        enable_rate_limit_auto_ban=True,
        # Regexes against the User-Agent header. Only tools that never carry
        # legitimate traffic; browsers, curl, and SDK clients are untouched.
        blocked_user_agents=[
            r"(?i)\b(?:sqlmap|nikto|nmap|masscan|zgrab|nuclei|wpscan|dirbuster|gobuster|acunetix|nessus)\b",
        ],
        auto_ban_threshold=20,
        auto_ban_duration=3600,
        threat_ban_config={
            "recon": ThreatBanConfig(threshold=5, duration=86400),
            "sensitive_file": ThreatBanConfig(threshold=3, duration=86400),
            "cms_probing": ThreatBanConfig(threshold=3, duration=86400),
        },
        custom_request_check=_global_content_scan,
        log_suspicious_level="WARNING",
        log_request_level=None,
        emergency_mode=_emergency_mode(),
        emergency_whitelist=["127.0.0.1", "::1"],
        enable_agent=False,
    )

    guard_deco = SecurityDecorator(security_config)
