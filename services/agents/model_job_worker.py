"""Isolated bounded-job worker. Holds the 9router virtual key. Not an agent runtime.

TEMPORARY BRIDGE: ``mint_jobs_virtual_key`` writes 9router sqlite ``apiKeys``.
Preferred path is worker → supported 9router control/API → scoped virtual key.
Deletion condition: mint exclusively via POST /api/keys (or another official
control API) and drop the DATA_DIR mount. Custody is not settled while the
mount remains.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from urllib import error as urlerror
from urllib import request as urlrequest

from .model_jobs import (
    InvalidArchetype,
    ModelJobExecutor,
    ModelJobFailed,
    ModelJobResult,
)

log = logging.getLogger(__name__)

KEY_NAME = "odysseus-jobs"
MACHINE_ID = "odysseusjobs1"
DEFAULT_V1 = "http://9router:20128/v1"
DEFAULT_DB = "/opt/odysseus/9router-data/db/data.sqlite"
API_KEY_SECRET = os.environ.get("API_KEY_SECRET", "endpoint-proxy-api-key-secret")
_JOBS_KEY_ENV = "_JOBS_VIRTUAL_KEY"
# Catalog id 9router accepts on /v1/chat/completions. Overlay UI routes
# (automatic/fast/…) and LiteLLM ``openai/`` prefixes 404 if sent raw.
DEFAULT_CATALOG_MODEL = "cx/gpt-5.5"
_LITELLM_OPENAI_PREFIX = "openai/"
# Odysseus composer routes + legacy ``auto`` default from session_routes.
_OVERLAY_ROUTE_MODELS = frozenset({"auto", "automatic", "fast", "balanced", "best"})


def resolve_nine_router_model(raw: str | None) -> str:
    """Map Odysseus/LiteLLM model strings to a 9router catalog id.

    Agents: model-jobs calls 9router directly (not via LiteLLM). Catalog ids
    look like ``cx/gpt-5.5``. Sending ``auto``/``automatic`` or
    ``openai/cx/gpt-5.5`` yields model_not_found (no OpenAI credentials).

    Parameters
    ----------
    raw
        Payload model, overlay route, LiteLLM id, or empty.

    Returns
    -------
    str
        Catalog id safe for ``POST /v1/chat/completions``.

    Examples
    --------
    >>> resolve_nine_router_model("automatic")
    'cx/gpt-5.5'
    >>> resolve_nine_router_model("openai/cx/gpt-5.5")
    'cx/gpt-5.5'
    """

    model = str(raw or "").strip()
    if model.startswith(_LITELLM_OPENAI_PREFIX):
        model = model[len(_LITELLM_OPENAI_PREFIX) :].strip()
    if not model or model in _OVERLAY_ROUTE_MODELS:
        env_default = str(os.environ.get("NINE_ROUTER_DEFAULT_MODEL") or "").strip()
        if env_default.startswith(_LITELLM_OPENAI_PREFIX):
            env_default = env_default[len(_LITELLM_OPENAI_PREFIX) :].strip()
        return env_default or DEFAULT_CATALOG_MODEL
    return model


def mint_jobs_virtual_key(db_path: str | Path | None = None) -> str:
    """Reuse or insert the named 9router virtual key from DATA_DIR sqlite.

    Parameters
    ----------
    db_path
        9router sqlite file. Defaults to the mounted DATA_DIR path.

    Returns
    -------
    str
        Active virtual key. Stays in this process only.

    Example
    -------
    ``mint_jobs_virtual_key("/tmp/data.sqlite")``
    """

    path = Path(db_path or os.environ.get("NINE_ROUTER_SQLITE_PATH", DEFAULT_DB))
    if not path.is_file():
        raise ModelJobFailed(f"9router sqlite missing: {path}")
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT key FROM apiKeys WHERE name = ? AND isActive = 1 LIMIT 1",
            (KEY_NAME,),
        ).fetchone()
        if row and row["key"]:
            return str(row["key"])
        key_id = uuid.uuid4().hex[:6]
        crc = hmac.new(
            API_KEY_SECRET.encode(),
            (MACHINE_ID + key_id).encode(),
            hashlib.sha256,
        ).hexdigest()[:8]
        key = f"sk-{MACHINE_ID}-{key_id}-{crc}"
        conn.execute(
            "INSERT INTO apiKeys (id, key, name, machineId, isActive, createdAt) "
            "VALUES (?, ?, ?, ?, 1, ?)",
            (
                key_id,
                key,
                KEY_NAME,
                MACHINE_ID,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        return key
    finally:
        conn.close()


def _messages_from_payload(payload: dict[str, Any], archetype: Any) -> list[dict[str, str]]:
    messages = payload.get("messages")
    if isinstance(messages, list) and messages:
        return list(messages)
    text = str(payload.get("text") or payload.get("original_text") or "")
    instruction = str(payload.get("instruction") or "")
    job_id = str(getattr(archetype, "id", "") or "")
    if job_id == "session-title":
        return [
            {
                "role": "system",
                "content": (
                    "Generate a short title (3-6 words, no quotes). "
                    "Reply with ONLY the title."
                ),
            },
            {"role": "user", "content": text},
        ]
    if instruction:
        return [
            {
                "role": "system",
                "content": "Follow the instruction. Output only the rewritten text.",
            },
            {"role": "user", "content": f"{text}\n\nInstruction: {instruction}"},
        ]
    schema = str(getattr(archetype, "result_schema", "") or "the request")
    return [
        {"role": "system", "content": f"Return a JSON object for {schema}."},
        {"role": "user", "content": text},
    ]


def _content_from_completion(parsed: dict[str, Any]) -> str:
    choices = parsed.get("choices") or []
    if not choices:
        raise ModelJobFailed("9router returned no choices")
    message = (choices[0] or {}).get("message") or {}
    content = message.get("content") or ""
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content).strip()


def _output_from_text(text: str, archetype: Any) -> dict[str, Any]:
    if text.startswith("{") and text.endswith("}"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    job_id = str(getattr(archetype, "id", "") or "")
    if job_id == "session-title":
        return {"title": text}
    return {"text": text}


def invoke_nine_router(
    payload: dict[str, Any],
    *,
    archetype: Any = None,
    owner: str | None = None,
    base_url: str | None = None,
    virtual_key: str | None = None,
) -> dict[str, Any]:
    """Call 9router /v1/chat/completions and return a typed object.

    Parameters
    ----------
    payload
        Job input. May include ``messages``, ``text``, or ``model``.
    archetype
        Bounded job archetype.
    owner
        Odysseus owner id. Used only for audit attribution.
    base_url
        9router /v1 root. Empty string fails closed.
    virtual_key
        Worker-held virtual key. Empty string fails closed.

    Returns
    -------
    dict[str, Any]
        Typed output plus ``_provenance`` (no secrets).

    Example
    -------
    ``invoke_nine_router({"text": "hi"}, base_url="", virtual_key="")`` raises.
    """

    del owner  # attribution happens on the executor result
    base = DEFAULT_V1 if base_url is None else base_url
    if base_url is None:
        base = os.environ.get("NINE_ROUTER_V1", DEFAULT_V1)
    key = os.environ.get(_JOBS_KEY_ENV, "") if virtual_key is None else virtual_key
    base = str(base or "").rstrip("/")
    if not base or not key:
        raise ModelJobFailed("9router access missing")
    # Resolve before the request: overlay routes and openai/ LiteLLM ids 404.
    model = resolve_nine_router_model(payload.get("model"))
    body = {
        "model": model,
        "messages": _messages_from_payload(payload, archetype),
        "temperature": getattr(archetype, "temperature", 0) or 0,
    }
    token_limit = getattr(archetype, "token_limit", None)
    if token_limit:
        body["max_tokens"] = int(token_limit)
    req = urlrequest.Request(
        f"{base}/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    timeout = getattr(archetype, "timeout_seconds", 60) or 60
    from services.observability.otel import (
        apply_span_attributes,
        chat_completion_span,
        record_span_error,
    )

    with chat_completion_span(model) as span:
        try:
            with urlrequest.urlopen(req, timeout=timeout) as resp:
                status = int(getattr(resp, "status", 200) or 200)
                parsed = json.loads(resp.read().decode())
            apply_span_attributes(span, {"http.status_code": status})
            if status == 401 or status >= 500:
                from services.observability.metrics import record_client_http_error

                record_client_http_error("9router", status)
        except (urlerror.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            http_status = 500
            if isinstance(exc, urlerror.HTTPError):
                http_status = int(exc.code or 500)
            apply_span_attributes(span, {"http.status_code": http_status})
            record_span_error(span, exc)
            from services.observability.metrics import record_client_http_error

            record_client_http_error("9router", http_status)
            raise ModelJobFailed(f"9router completions failed: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ModelJobFailed("9router returned a non-object")
    text = _content_from_completion(parsed)
    output = _output_from_text(text, archetype)
    output["_provenance"] = {
        "resolved_model": parsed.get("model") or body["model"],
        "resolved_route": "9router",
    }
    return output


def execute_worker_job(
    archetype: Any,
    payload: dict[str, Any],
    owner: str,
    *,
    invoke: Callable[..., Any] | None = None,
) -> ModelJobResult:
    """Run a typed job inside the worker process.

    Parameters
    ----------
    archetype
        Bounded job archetype. Agent kinds still raise InvalidArchetype.
    payload
        Owner-scoped job input.
    owner
        Odysseus owner id.
    invoke
        Optional 9router invoker. Defaults to ``invoke_nine_router``.

    Returns
    -------
    ModelJobResult
        Typed output, empty conversation id, public audit.

    Example
    -------
    ``execute_worker_job(arch, {"text": "x"}, "u1", invoke=fake)``
    """

    return ModelJobExecutor(invoke=invoke or invoke_nine_router).execute(
        archetype, payload, owner
    )


def handle_job_request(body: dict[str, Any]) -> dict[str, Any]:
    """Execute one HTTP job payload and return the public result.

    Parameters
    ----------
    body
        ``owner``, ``archetype``, and ``payload``.

    Returns
    -------
    dict[str, Any]
        JSON-safe result without secrets.

    Example
    -------
    ``handle_job_request({"owner": "u1", "archetype": {...}, "payload": {}})``
    """

    raw_arch = body.get("archetype") or {}
    if not isinstance(raw_arch, dict):
        raise InvalidArchetype("archetype must be an object")
    result = execute_worker_job(
        SimpleNamespace(**raw_arch),
        dict(body.get("payload") or {}),
        str(body.get("owner") or ""),
    )
    return {
        "output": result.output,
        "owner": result.owner,
        "conversation_id": None,
        "audit": result.audit,
    }


class _JobHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        log.info("%s", fmt % args if args else fmt)

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        blob = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] == "/health":
            self._send(200, {"ok": True})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != "/jobs":
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode() or "{}")
            if not isinstance(body, dict):
                raise InvalidArchetype("job body must be an object")
            self._send(200, handle_job_request(body))
        except ModelJobFailed as exc:
            self._send(503, {"error": str(exc)})
        except InvalidArchetype as exc:
            self._send(400, {"error": str(exc)})


def main() -> None:
    """Mint the named jobs key, then serve typed jobs.

    Agents: ``configure_tracer`` uses the same Collector-only contract as the
    Odysseus API process. Completions emit ``chat {model}`` GenAI spans.
    """

    _unshadow = __import__(
        "services.observability.stdlib_calendar", fromlist=["prefer_stdlib_calendar"]
    ).prefer_stdlib_calendar
    _unshadow()
    from services.observability.otel import configure_tracer

    configure_tracer(os.environ.get("OTEL_SERVICE_NAME") or "odysseus-model-jobs")
    os.environ[_JOBS_KEY_ENV] = mint_jobs_virtual_key()
    host = os.environ.get("MODEL_JOB_WORKER_HOST", "0.0.0.0")
    port = int(os.environ.get("MODEL_JOB_WORKER_PORT", "8091"))
    ThreadingHTTPServer((host, port), _JobHandler).serve_forever()


if __name__ == "__main__":
    main()
