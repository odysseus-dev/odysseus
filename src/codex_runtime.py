"""Codex app-server adapter for Odysseus' shared Codex Runtime."""

import asyncio
import contextlib
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import threading
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)

CODEX_RUNTIME_BASE_URL = "codex://runtime"
CODEX_RUNTIME_ENDPOINT_NAME = "Codex Runtime"
DEFAULT_CODEX_RUNTIME_MODELS = ["gpt-5.5", "gpt-5.4-mini", "gpt-5.3-codex-spark"]
# Backwards-compatible constant for older imports. Runtime code should call
# codex_runtime_models() so env changes are respected.
CODEX_RUNTIME_MODELS = list(DEFAULT_CODEX_RUNTIME_MODELS)
CODEX_RUNTIME_CLIENT_VERSION = "1.0.0"
CODEX_RUNTIME_SETUP_COMMAND = "docker compose exec odysseus codex login --device-auth"

CODEX_RUNTIME_DEVELOPER_INSTRUCTIONS = (
    "You are running as the Codex model runtime inside Odysseus. Odysseus is "
    "responsible for the chat UI, persistence, and its own agent tools. Answer "
    "the user directly for normal chat. In Odysseus agent mode, if the system "
    "prompt asks for fenced Odysseus tool blocks, emit those exact fenced blocks "
    "instead of trying to use Codex-side shell, file editing, browser, MCP, or "
    "other local tools. Do not modify files, run commands, or request approvals "
    "from Codex; Odysseus will execute any tools it supports."
)

_RUNTIME_ACTIVE_LOCK = threading.Lock()
_RUNTIME_ACTIVE_REQUESTS = 0


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, min_value: int = 1, max_value: int = 86_400) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using %s", name, raw, default)
        return default
    return max(min_value, min(max_value, value))


def _env_models() -> List[str]:
    raw = (os.getenv("CODEX_RUNTIME_MODELS") or "").strip()
    if not raw:
        return list(DEFAULT_CODEX_RUNTIME_MODELS)
    values: List[str] = []
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                values = [str(item).strip() for item in parsed]
        except json.JSONDecodeError:
            logger.warning("Invalid JSON in CODEX_RUNTIME_MODELS; using defaults")
    else:
        values = [part.strip() for part in re.split(r"[,;\n]", raw)]
    deduped = [m for i, m in enumerate(values) if m and m not in values[:i]]
    return deduped or list(DEFAULT_CODEX_RUNTIME_MODELS)


def codex_runtime_models() -> List[str]:
    models = _env_models()
    configured_default = (os.getenv("CODEX_RUNTIME_DEFAULT_MODEL") or "").strip()
    if configured_default and configured_default not in models:
        models.insert(0, configured_default)
    return models


def is_codex_runtime_enabled() -> bool:
    return _truthy(os.getenv("CODEX_RUNTIME_ENABLED"))


def default_codex_model() -> str:
    models = codex_runtime_models()
    configured = (os.getenv("CODEX_RUNTIME_DEFAULT_MODEL") or "").strip()
    return configured or models[0]


def max_concurrent_requests() -> int:
    return _env_int("CODEX_RUNTIME_MAX_CONCURRENT_REQUESTS", 2, min_value=1, max_value=64)


def startup_timeout_seconds() -> int:
    return _env_int("CODEX_RUNTIME_STARTUP_TIMEOUT_SECONDS", 15, min_value=1, max_value=300)


def read_timeout_seconds() -> int:
    return _env_int("CODEX_RUNTIME_READ_TIMEOUT_SECONDS", 60, min_value=1, max_value=900)


def shutdown_timeout_seconds() -> int:
    return _env_int("CODEX_RUNTIME_SHUTDOWN_TIMEOUT_SECONDS", 3, min_value=1, max_value=60)


def default_request_timeout_seconds() -> int:
    return _env_int("CODEX_RUNTIME_REQUEST_TIMEOUT_SECONDS", 300, min_value=1, max_value=3_600)


def probe_timeout_seconds() -> int:
    return _env_int("CODEX_RUNTIME_PROBE_TIMEOUT_SECONDS", 10, min_value=1, max_value=120)


def message_char_limit() -> int:
    return _env_int("CODEX_RUNTIME_MESSAGE_CHAR_LIMIT", 40_000, min_value=1_000, max_value=2_000_000)


def prompt_char_limit() -> int:
    return _env_int("CODEX_RUNTIME_PROMPT_CHAR_LIMIT", 160_000, min_value=8_000, max_value=4_000_000)


def codex_runtime_limits() -> Dict[str, int]:
    return {
        "max_concurrent_requests": max_concurrent_requests(),
        "active_requests": active_runtime_requests(),
        "startup_timeout_seconds": startup_timeout_seconds(),
        "read_timeout_seconds": read_timeout_seconds(),
        "shutdown_timeout_seconds": shutdown_timeout_seconds(),
        "request_timeout_seconds": default_request_timeout_seconds(),
        "probe_timeout_seconds": probe_timeout_seconds(),
        "message_char_limit": message_char_limit(),
        "prompt_char_limit": prompt_char_limit(),
    }


def is_codex_runtime_url(url: Optional[str]) -> bool:
    return (url or "").strip().lower().startswith("codex://")


def codex_home() -> str:
    configured = os.getenv("CODEX_HOME")
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    if os.path.isdir("/app/data"):
        return "/app/data/codex"
    return os.path.abspath(os.path.expanduser("~/.codex"))


def codex_runtime_cwd() -> str:
    configured = (os.getenv("CODEX_RUNTIME_CWD") or "").strip()
    if configured:
        expanded = os.path.abspath(os.path.expanduser(configured))
        if os.path.isdir(expanded):
            return expanded
        logger.warning("CODEX_RUNTIME_CWD is not a directory; using default runtime cwd")
    if os.path.isdir("/app"):
        return "/app"
    return os.getcwd()


def codex_cli() -> str:
    return (os.getenv("CODEX_RUNTIME_CLI") or "codex").strip() or "codex"


def resolve_codex_cli() -> Optional[str]:
    configured = codex_cli()
    if os.path.isabs(configured):
        return configured if os.access(configured, os.X_OK) else None
    return shutil.which(configured)


def codex_auth_file() -> str:
    return os.path.join(codex_home(), "auth.json")


def is_codex_auth_ready() -> bool:
    if os.getenv("CODEX_ACCESS_TOKEN"):
        return True
    try:
        return os.path.getsize(codex_auth_file()) > 0
    except OSError:
        return False


def active_runtime_requests() -> int:
    with _RUNTIME_ACTIVE_LOCK:
        return _RUNTIME_ACTIVE_REQUESTS


class _RuntimeSlot:
    def __init__(self) -> None:
        self._released = False

    def release(self) -> None:
        global _RUNTIME_ACTIVE_REQUESTS
        if self._released:
            return
        self._released = True
        with _RUNTIME_ACTIVE_LOCK:
            _RUNTIME_ACTIVE_REQUESTS = max(0, _RUNTIME_ACTIVE_REQUESTS - 1)


def _try_acquire_runtime_slot() -> Optional[_RuntimeSlot]:
    global _RUNTIME_ACTIVE_REQUESTS
    with _RUNTIME_ACTIVE_LOCK:
        if _RUNTIME_ACTIVE_REQUESTS >= max_concurrent_requests():
            return None
        _RUNTIME_ACTIVE_REQUESTS += 1
    return _RuntimeSlot()


def _sanitize_diagnostic_text(text: Any, *, limit: int = 2_000) -> str:
    s = str(text or "")
    if not s:
        return ""
    home = re.escape(codex_home())
    replacements = [
        (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/\-]+=*", re.I), "Bearer [redacted]"),
        (re.compile(r"\b(sk-[A-Za-z0-9_\-]{16,}|xox[baprs]-[A-Za-z0-9_\-]{16,}|AIza[0-9A-Za-z_\-]{16,})\b"), "[redacted-token]"),
        (re.compile(r"\b(CODEX_ACCESS_TOKEN|OPENAI_API_KEY|ANTHROPIC_API_KEY|API_KEY|TOKEN|PASSWORD)\s*=\s*['\"]?[^'\"\s]+", re.I), r"\1=[redacted]"),
        (re.compile(home + r"[^\s'\"\)]*"), "[codex-home]"),
        (re.compile(r"/app/data/codex[^\s'\"\)]*"), "[codex-home]"),
    ]
    for pattern, repl in replacements:
        s = pattern.sub(repl, s)
    s = s.replace("\x00", "")
    if len(s) > limit:
        s = s[:limit] + "...[truncated]"
    return s


def _safe_diag(code: str, message: str, severity: str = "info") -> Dict[str, str]:
    return {
        "code": code,
        "message": _sanitize_diagnostic_text(message, limit=500),
        "severity": severity,
    }


def _version_for_cli(resolved_cli: Optional[str]) -> Optional[str]:
    if not resolved_cli:
        return None
    try:
        result = subprocess.run(
            [resolved_cli, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        output = (result.stdout or result.stderr or "").strip()
        return _sanitize_diagnostic_text(output, limit=200) or None
    except Exception as exc:
        logger.debug("Codex CLI version probe failed: %s", _sanitize_diagnostic_text(exc))
        return None


def _codex_cli_env() -> Dict[str, str]:
    home = codex_home()
    env = os.environ.copy()
    env["CODEX_HOME"] = home
    env.setdefault("CODEX_SQLITE_HOME", home)
    return env


def _run_codex_cli(args: List[str], *, timeout: Optional[int] = None) -> Dict[str, Any]:
    resolved_cli = resolve_codex_cli()
    if not resolved_cli:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": "Codex CLI not found",
            "error": "cli_missing",
        }
    try:
        result = subprocess.run(
            [resolved_cli, *args],
            capture_output=True,
            text=True,
            timeout=timeout or probe_timeout_seconds(),
            check=False,
            env=_codex_cli_env(),
        )
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": _sanitize_diagnostic_text(result.stdout, limit=2_000),
            "stderr": _sanitize_diagnostic_text(result.stderr, limit=2_000),
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": f"Codex CLI command timed out after {timeout or probe_timeout_seconds()}s",
            "error": "timeout",
        }
    except Exception as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": _sanitize_diagnostic_text(exc, limit=2_000),
            "error": "probe_failed",
        }


def codex_auth_probe() -> Dict[str, Any]:
    if os.getenv("CODEX_ACCESS_TOKEN"):
        return {
            "auth_ready": True,
            "method": "access_token",
            "ok": True,
            "message": "CODEX_ACCESS_TOKEN is configured.",
        }
    result = _run_codex_cli(["login", "status"], timeout=probe_timeout_seconds())
    output = "\n".join(part for part in (result.get("stdout"), result.get("stderr")) if part).strip()
    if result.get("ok"):
        return {
            "auth_ready": True,
            "method": "cli_login_status",
            "ok": True,
            "message": output or "Codex login status succeeded.",
            "returncode": result.get("returncode"),
        }
    fallback_ready = is_codex_auth_ready()
    return {
        "auth_ready": fallback_ready,
        "method": "auth_file" if fallback_ready else "cli_login_status",
        "ok": fallback_ready,
        "message": output or "Codex login status did not confirm authentication.",
        "returncode": result.get("returncode"),
        "error": result.get("error") or "auth_required",
    }


def codex_runtime_probe() -> Dict[str, Any]:
    resolved_cli = resolve_codex_cli()
    version = _version_for_cli(resolved_cli)
    auth = codex_auth_probe() if resolved_cli else {
        "auth_ready": is_codex_auth_ready(),
        "method": "auth_file",
        "ok": False,
        "message": "Codex CLI is not available.",
        "error": "cli_missing",
    }
    state = "ready" if is_codex_runtime_enabled() and resolved_cli and auth.get("auth_ready") else "not_ready"
    return {
        "state": state,
        "enabled": is_codex_runtime_enabled(),
        "cli_available": bool(resolved_cli),
        "cli": codex_cli(),
        "cli_version": version,
        "auth_ready": bool(auth.get("auth_ready")),
        "auth": auth,
        "setup_command": CODEX_RUNTIME_SETUP_COMMAND,
        "limits": codex_runtime_limits(),
    }


def codex_runtime_endpoint_registration_status() -> Dict[str, Any]:
    if not is_codex_runtime_enabled():
        return {"registered": False, "endpoint_id": None}
    try:
        from core.database import ModelEndpoint, SessionLocal
        db = SessionLocal()
        try:
            ep = (
                db.query(ModelEndpoint)
                .filter(ModelEndpoint.base_url == CODEX_RUNTIME_BASE_URL)
                .filter(ModelEndpoint.owner.is_(None))
                .first()
            )
            if not ep:
                return {"registered": False, "endpoint_id": None}
            return {
                "registered": True,
                "endpoint_id": ep.id,
                "endpoint_name": ep.name,
                "is_enabled": bool(ep.is_enabled),
            }
        finally:
            db.close()
    except Exception as exc:
        logger.debug("Codex Runtime registration status check failed: %s", _sanitize_diagnostic_text(exc))
        return {
            "registered": False,
            "endpoint_id": None,
            "error": "registration_status_failed",
        }


def codex_runtime_status() -> Dict[str, Any]:
    resolved_cli = resolve_codex_cli()
    registration = codex_runtime_endpoint_registration_status()
    enabled = is_codex_runtime_enabled()
    auth_ready = is_codex_auth_ready()
    registered = bool(registration.get("registered"))
    diagnostics: List[Dict[str, str]] = []

    if not enabled:
        state = "disabled"
        diagnostics.append(_safe_diag("runtime_disabled", "Set CODEX_RUNTIME_ENABLED=true and restart Odysseus.", "info"))
    elif not resolved_cli:
        state = "cli_missing"
        diagnostics.append(_safe_diag("cli_missing", "Install the Codex CLI or set CODEX_RUNTIME_CLI.", "error"))
    elif not auth_ready:
        state = "auth_required"
        diagnostics.append(_safe_diag("auth_required", "Run the Codex device login command from the Odysseus container.", "warning"))
    elif not registered:
        state = "unregistered"
        diagnostics.append(_safe_diag("endpoint_unregistered", "Run reconcile to register the shared Codex model endpoint.", "warning"))
    else:
        state = "ready"

    if enabled and resolved_cli and not diagnostics:
        diagnostics.append(_safe_diag("ready", "Codex Runtime is enabled and ready.", "info"))

    return {
        "state": state,
        "enabled": enabled,
        "registered": registered,
        "endpoint_registration": registration,
        "base_url": CODEX_RUNTIME_BASE_URL,
        "endpoint_name": CODEX_RUNTIME_ENDPOINT_NAME,
        "models": codex_runtime_models(),
        "default_model": default_codex_model(),
        "auth_ready": auth_ready,
        "cli": codex_cli(),
        "cli_available": bool(resolved_cli),
        "cli_version": _version_for_cli(resolved_cli),
        "setup_command": CODEX_RUNTIME_SETUP_COMMAND,
        "limits": codex_runtime_limits(),
        "diagnostics": diagnostics,
    }


def ensure_codex_runtime_endpoint_registered() -> Dict[str, Any]:
    """Create/update the shared Codex model endpoint when the feature flag is on."""
    if not is_codex_runtime_enabled():
        return {"enabled": False, "registered": False, "changed": False}

    from core.database import ModelEndpoint, SessionLocal

    models = codex_runtime_models()
    db = SessionLocal()
    try:
        ep = (
            db.query(ModelEndpoint)
            .filter(ModelEndpoint.base_url == CODEX_RUNTIME_BASE_URL)
            .filter(ModelEndpoint.owner.is_(None))
            .first()
        )
        changed = False
        if not ep:
            ep = ModelEndpoint(
                id=str(uuid.uuid4())[:8],
                name=CODEX_RUNTIME_ENDPOINT_NAME,
                base_url=CODEX_RUNTIME_BASE_URL,
                api_key=None,
                is_enabled=True,
                cached_models=json.dumps(models),
                hidden_models=None,
                pinned_models=None,
                model_type="llm",
                endpoint_kind="codex",
                model_refresh_mode="manual",
                supports_tools=False,
                owner=None,
            )
            db.add(ep)
            changed = True
        else:
            desired = {
                "name": CODEX_RUNTIME_ENDPOINT_NAME,
                "api_key": None,
                "is_enabled": True,
                "cached_models": json.dumps(models),
                "model_type": "llm",
                "endpoint_kind": "codex",
                "model_refresh_mode": "manual",
                "supports_tools": False,
                "owner": None,
            }
            for key, value in desired.items():
                if getattr(ep, key) != value:
                    setattr(ep, key, value)
                    changed = True
        if changed:
            db.commit()
            logger.info("Codex Runtime endpoint registered: %s", ep.id)
        try:
            from src.settings import load_settings, save_settings
            settings = load_settings()
            if not settings.get("default_endpoint_id"):
                settings["default_endpoint_id"] = ep.id
                settings["default_model"] = default_codex_model()
                save_settings(settings)
                changed = True
        except Exception as exc:
            logger.debug("Codex Runtime default model seeding skipped: %s", _sanitize_diagnostic_text(exc))
        if changed:
            try:
                from routes.model_routes import invalidate_models_cache
                invalidate_models_cache()
            except Exception as exc:
                logger.debug("Codex Runtime cache invalidation skipped: %s", _sanitize_diagnostic_text(exc))
        return {"enabled": True, "registered": True, "endpoint_id": ep.id, "changed": changed}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _limit_text(text: str, limit: int, marker: str) -> str:
    if len(text) <= limit:
        return text
    if limit <= len(marker) + 200:
        return text[:limit]
    head_len = min(2_000, max(200, limit // 8))
    tail_len = max(0, limit - head_len - len(marker))
    return text[:head_len] + marker + text[-tail_len:]


def _message_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return _limit_text(content, message_char_limit(), "\n[... message truncated ...]\n")
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                item_type = str(item.get("type") or "").strip()
                if item_type in {"text", "input_text"}:
                    parts.append(str(item.get("text") or ""))
                elif item_type in {"image_url", "input_image", "localImage"}:
                    parts.append("[unsupported image attachment omitted]")
                elif item_type in {"file", "input_file", "localFile"}:
                    parts.append("[unsupported file attachment omitted]")
                elif "text" in item and isinstance(item.get("text"), str):
                    parts.append(str(item.get("text") or ""))
                else:
                    label = item_type or "structured content"
                    parts.append(f"[unsupported {label} omitted]")
            elif item is not None:
                parts.append(str(item))
        joined = "\n".join(p for p in parts if p)
        return _limit_text(joined, message_char_limit(), "\n[... message truncated ...]\n")
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return _limit_text(content["text"], message_char_limit(), "\n[... message truncated ...]\n")
        return "[unsupported structured content omitted]"
    if content is None:
        return ""
    return _limit_text(str(content), message_char_limit(), "\n[... message truncated ...]\n")


def _role_tag(role: str) -> str:
    cleaned = re.sub(r"[^A-Z0-9 _-]", "", role.upper()).strip()
    return cleaned[:40] or "USER"


def _tool_result_label(msg: Dict[str, Any]) -> str:
    tool_id = re.sub(r"[^A-Za-z0-9_.:-]", "_", str(msg.get("tool_call_id") or ""))[:80]
    return f"TOOL RESULT {tool_id}" if tool_id else "TOOL RESULT"


def messages_to_codex_prompt(messages: List[Dict[str, Any]]) -> str:
    """Flatten Odysseus chat messages into one bounded Codex turn input."""
    rendered: List[str] = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = _role_tag(str(msg.get("role") or "user"))
        content = _message_content_to_text(msg.get("content"))
        if not content and msg.get("tool_calls"):
            content = "[assistant requested Odysseus tool calls]"
        if not content:
            continue
        if role == "TOOL":
            label = _tool_result_label(msg)
            rendered.append(f"<{label}>\n{content}\n</{label}>")
        else:
            rendered.append(f"<{role}>\n{content}\n</{role}>")
    if not rendered:
        return "Continue."
    prompt = (
        "Continue this Odysseus conversation. Preserve the user's requested "
        "behavior and answer as the assistant. Some Odysseus attachments may "
        "be represented as explicit unsupported-content markers.\n\n"
        + "\n\n".join(rendered)
    )
    return _limit_text(prompt, prompt_char_limit(), "\n\n[... earlier Codex Runtime prompt content truncated ...]\n\n")


def _sse_delta(text: str) -> str:
    return f"data: {json.dumps({'delta': text})}\n\n"


def _sse_error(
    message: str,
    status: int = 502,
    *,
    code: str = "codex_runtime_error",
    retryable: bool = False,
) -> str:
    payload = {
        "error": _sanitize_diagnostic_text(message, limit=1_000),
        "code": code,
        "status": status,
        "retryable": retryable,
    }
    return f"event: error\ndata: {json.dumps(payload)}\n\n"


def _extract_error_text(payload: Dict[str, Any]) -> str:
    err = payload.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err)
    if err:
        return str(err)
    params = payload.get("params")
    if isinstance(params, dict):
        if params.get("message"):
            return str(params["message"])
        if params.get("error"):
            return str(params["error"])
    return "Codex app-server returned an error"


def _turn_final_text(turn: Dict[str, Any]) -> str:
    parts: List[str] = []
    for item in turn.get("items") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "agentMessage" and item.get("text"):
            parts.append(str(item["text"]))
    return "\n\n".join(parts)


async def _drain_stderr(proc: asyncio.subprocess.Process, stderr_tail: List[str]) -> None:
    assert proc.stderr is not None
    try:
        while True:
            line = await proc.stderr.readline()
            if not line:
                return
            text = _sanitize_diagnostic_text(line.decode(errors="replace").rstrip(), limit=1_000)
            if text:
                stderr_tail.append(text)
                del stderr_tail[:-20]
                logger.debug("[codex-runtime stderr] %s", text)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.debug("Codex stderr drain stopped: %s", _sanitize_diagnostic_text(exc))


class CodexAppServerSession:
    def __init__(self, *, model: str, timeout: int):
        self.model = model or default_codex_model()
        self.timeout = max(int(timeout or default_request_timeout_seconds()), 1)
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._next_id = 1
        self._stderr_tail: List[str] = []
        self._stderr_task: Optional[asyncio.Task] = None

    async def __aenter__(self) -> "CodexAppServerSession":
        resolved_cli = resolve_codex_cli()
        if not resolved_cli:
            raise RuntimeError(
                f"Codex CLI not found. Install @openai/codex or set CODEX_RUNTIME_CLI. "
                f"Configured CLI: {codex_cli()!r}"
            )

        os.makedirs(codex_home(), exist_ok=True)
        self.proc = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                resolved_cli,
                "app-server",
                "--listen",
                "stdio://",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_codex_cli_env(),
            ),
            timeout=startup_timeout_seconds(),
        )
        self._stderr_task = asyncio.create_task(_drain_stderr(self.proc, self._stderr_tail))
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._stderr_task:
            self._stderr_task.cancel()
        proc = self.proc
        if proc and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=shutdown_timeout_seconds())
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
        if self._stderr_task:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._stderr_task

    def _new_id(self) -> int:
        value = self._next_id
        self._next_id += 1
        return value

    async def send(self, payload: Dict[str, Any]) -> None:
        if not self.proc or not self.proc.stdin:
            raise RuntimeError("Codex app-server is not running")
        self.proc.stdin.write((json.dumps(payload) + "\n").encode())
        await asyncio.wait_for(self.proc.stdin.drain(), timeout=read_timeout_seconds())

    async def request(self, method: str, params: Optional[Dict[str, Any]] = None) -> int:
        request_id = self._new_id()
        payload: Dict[str, Any] = {"method": method, "id": request_id}
        if params is not None:
            payload["params"] = params
        await self.send(payload)
        return request_id

    async def read_message(self) -> Dict[str, Any]:
        if not self.proc or not self.proc.stdout:
            raise RuntimeError("Codex app-server is not running")
        line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=min(self.timeout, read_timeout_seconds()))
        if not line:
            stderr = "\n".join(self._stderr_tail[-5:])
            detail = f" Stderr: {stderr}" if stderr else ""
            raise RuntimeError(f"Codex app-server exited before completing the turn.{detail}")
        try:
            return json.loads(line.decode())
        except json.JSONDecodeError as exc:
            safe_line = _sanitize_diagnostic_text(line[:200].decode(errors="replace"), limit=250)
            raise RuntimeError(f"Invalid JSON from Codex app-server: {safe_line!r}") from exc

    async def respond_to_server_request(self, msg: Dict[str, Any]) -> None:
        request_id = msg.get("id")
        if request_id is None:
            return
        await self.send({
            "id": request_id,
            "error": {
                "code": -32601,
                "message": "Odysseus Codex Runtime does not expose Codex-side tools or approvals.",
            },
        })

    async def wait_response(self, request_id: int) -> Dict[str, Any]:
        while True:
            msg = await self.read_message()
            if msg.get("id") == request_id:
                if msg.get("error"):
                    raise RuntimeError(_extract_error_text(msg))
                return msg.get("result") or {}
            if msg.get("id") is not None and msg.get("method"):
                await self.respond_to_server_request(msg)

    async def initialize(self) -> None:
        init_id = await self.request("initialize", {
            "clientInfo": {
                "name": "odysseus_codex_runtime",
                "title": "Odysseus Codex Runtime",
                "version": CODEX_RUNTIME_CLIENT_VERSION,
            },
            "capabilities": {"experimentalApi": True},
        })
        await self.wait_response(init_id)
        await self.send({"method": "initialized", "params": {}})

    async def start_thread(self) -> str:
        thread_id = await self.request("thread/start", {
            "model": self.model,
            "cwd": codex_runtime_cwd(),
            "ephemeral": True,
            "sandbox": "read-only",
            "approvalPolicy": "never",
            "approvalsReviewer": "user",
            "developerInstructions": CODEX_RUNTIME_DEVELOPER_INSTRUCTIONS,
            "threadSource": "user",
        })
        result = await self.wait_response(thread_id)
        thread = result.get("thread") if isinstance(result, dict) else None
        if not isinstance(thread, dict) or not thread.get("id"):
            raise RuntimeError("Codex app-server did not return a thread id")
        return str(thread["id"])

    async def start_turn(self, thread_id: str, prompt: str) -> int:
        return await self.request("turn/start", {
            "threadId": thread_id,
            "model": self.model,
            "cwd": codex_runtime_cwd(),
            "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
            "approvalPolicy": "never",
            "approvalsReviewer": "user",
            "input": [{"type": "text", "text": prompt}],
        })


def _effective_request_timeout(timeout: Optional[int]) -> int:
    if timeout is None:
        return default_request_timeout_seconds()
    return max(int(timeout or default_request_timeout_seconds()), 1)


async def stream_codex(
    model: str,
    messages: List[Dict[str, Any]],
    *,
    timeout: int = 300,
) -> AsyncGenerator[str, None]:
    if not is_codex_runtime_enabled():
        yield _sse_error(
            "Codex Runtime is disabled. Set CODEX_RUNTIME_ENABLED=true and restart Odysseus.",
            503,
            code="codex_runtime_disabled",
            retryable=False,
        )
        return
    if not is_codex_auth_ready():
        yield _sse_error(
            f"Codex Runtime is not authenticated. Run `{CODEX_RUNTIME_SETUP_COMMAND}` and complete device login.",
            401,
            code="codex_runtime_auth_required",
            retryable=False,
        )
        return

    slot = _try_acquire_runtime_slot()
    if not slot:
        yield _sse_error(
            "Codex Runtime is busy. Try again after the current request finishes.",
            429,
            code="runtime_busy",
            retryable=True,
        )
        return

    effective_timeout = _effective_request_timeout(timeout)
    prompt = messages_to_codex_prompt(messages)
    emitted_text = ""
    try:
        async with asyncio.timeout(effective_timeout):
            async with CodexAppServerSession(model=model, timeout=effective_timeout) as session:
                await session.initialize()
                thread_id = await session.start_thread()
                turn_request_id = await session.start_turn(thread_id, prompt)
                while True:
                    msg = await session.read_message()

                    if msg.get("id") == turn_request_id:
                        if msg.get("error"):
                            yield _sse_error(
                                _extract_error_text(msg),
                                502,
                                code="codex_turn_start_failed",
                                retryable=True,
                            )
                            return
                        continue

                    if msg.get("id") is not None and msg.get("method"):
                        await session.respond_to_server_request(msg)
                        continue

                    method = msg.get("method")
                    params = msg.get("params") or {}
                    if method == "item/agentMessage/delta":
                        delta = params.get("delta") or ""
                        if delta:
                            emitted_text += delta
                            yield _sse_delta(delta)
                    elif method == "turn/completed":
                        turn = params.get("turn") or {}
                        status = turn.get("status")
                        if not emitted_text:
                            final_text = _turn_final_text(turn)
                            if final_text:
                                emitted_text = final_text
                                yield _sse_delta(final_text)
                        if status == "failed":
                            err = turn.get("error") or {}
                            yield _sse_error(
                                str(err.get("message") or "Codex turn failed"),
                                502,
                                code="codex_turn_failed",
                                retryable=True,
                            )
                            return
                        yield "data: [DONE]\n\n"
                        return
                    elif method == "error":
                        yield _sse_error(
                            _extract_error_text(msg),
                            502,
                            code="codex_app_server_error",
                            retryable=True,
                        )
                        return
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        logger.warning("Codex Runtime request timed out after %ss", effective_timeout)
        yield _sse_error(
            f"Codex Runtime request timed out after {effective_timeout}s.",
            504,
            code="codex_runtime_timeout",
            retryable=True,
        )
    except Exception as exc:
        safe_exc = _sanitize_diagnostic_text(exc)
        logger.warning("Codex Runtime stream failed: %s", safe_exc)
        code = "codex_runtime_cli_missing" if "Codex CLI not found" in safe_exc else "codex_runtime_error"
        status = 503 if code == "codex_runtime_cli_missing" else 502
        yield _sse_error(safe_exc, status, code=code, retryable=(status >= 500))
    finally:
        slot.release()


async def call_codex(
    model: str,
    messages: List[Dict[str, Any]],
    *,
    timeout: int = 300,
) -> str:
    parts: List[str] = []
    async for chunk in stream_codex(model, messages, timeout=timeout):
        if chunk.startswith("data: [DONE]"):
            break
        if chunk.startswith("event: error"):
            try:
                data_line = next(line for line in chunk.splitlines() if line.startswith("data:"))
                payload = json.loads(data_line.split(":", 1)[1].strip())
                raise HTTPException(payload.get("status", 502), payload.get("error") or payload.get("text") or "Codex error")
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(502, f"Codex error: {exc}") from exc
        if chunk.startswith("data: "):
            try:
                payload = json.loads(chunk[6:])
                delta = payload.get("delta")
                if delta:
                    parts.append(str(delta))
            except json.JSONDecodeError:
                continue
    return "".join(parts)


def shell_setup_command() -> str:
    return " ".join(shlex.quote(part) for part in CODEX_RUNTIME_SETUP_COMMAND.split())
