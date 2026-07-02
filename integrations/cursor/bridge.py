from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from cursor_sdk import Agent, Cursor
from cursor_sdk.errors import CursorAgentError
from cursor_sdk.types import AgentOptions, LocalAgentOptions


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()


def _json_content(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=True)
    except Exception:
        return str(value)


def _extract_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    parts.append(text)
                continue
            if not isinstance(item, Mapping):
                rendered = str(item).strip()
                if rendered:
                    parts.append(rendered)
                continue
            item_type = str(item.get("type") or "").strip().lower()
            if item_type in {"text", "input_text", "output_text"}:
                text = str(item.get("text") or "").strip()
                if text:
                    parts.append(text)
                continue
            if item_type == "image_url":
                image_url = item.get("image_url")
                if isinstance(image_url, Mapping):
                    url = str(image_url.get("url") or "").strip()
                    if url:
                        parts.append(f"[image: {url}]")
                continue
            rendered = _json_content(item).strip()
            if rendered:
                parts.append(rendered)
        return "\n".join(part for part in parts if part)
    if isinstance(content, Mapping):
        return _json_content(content).strip()
    return str(content).strip()


def _render_message(role: str, content: Any, *, name: str = "") -> str:
    text = _extract_text(content)
    if not text:
        return ""
    label = role.upper() if role else "MESSAGE"
    if name:
        label = f"{label} ({name})"
    return f"{label}:\n{text}"


def build_full_prompt(messages: Iterable[Mapping[str, Any]]) -> str:
    blocks: List[str] = []
    for raw in messages:
        if not isinstance(raw, Mapping):
            continue
        role = str(raw.get("role") or "").strip().lower()
        name = str(raw.get("name") or "").strip()
        rendered = _render_message(role or "message", raw.get("content"), name=name)
        if rendered:
            blocks.append(rendered)
    if not blocks:
        return "USER:\nHello."
    blocks.append("Reply as the assistant to the latest user request.")
    return "\n\n".join(blocks)


def build_incremental_prompt(messages: Iterable[Mapping[str, Any]]) -> str:
    latest_user = ""
    system_parts: List[str] = []
    for raw in messages:
        if not isinstance(raw, Mapping):
            continue
        role = str(raw.get("role") or "").strip().lower()
        text = _extract_text(raw.get("content"))
        if not text:
            continue
        if role == "system":
            system_parts.append(text)
        elif role == "user":
            latest_user = text
    blocks: List[str] = []
    if system_parts:
        blocks.append("SYSTEM:\n" + "\n\n".join(system_parts))
    if latest_user:
        blocks.append("USER:\n" + latest_user)
    if not blocks:
        return build_full_prompt(messages)
    blocks.append("Reply as the assistant.")
    return "\n\n".join(blocks)


def parse_model_map(raw: str, default_model: str) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for piece in raw.split(","):
        item = piece.strip()
        if not item:
            continue
        if "=" in item:
            alias, actual = item.split("=", 1)
            alias = alias.strip()
            actual = actual.strip()
            if alias and actual:
                mapping[alias] = actual
        else:
            mapping[item] = item
    if not mapping and default_model:
        mapping[default_model] = default_model
    return mapping


@dataclass
class BridgeSettings:
    cursor_api_key: str = field(default_factory=lambda: _env("CURSOR_API_KEY"))
    bridge_api_key: str = field(default_factory=lambda: _env("CURSOR_BRIDGE_API_KEY"))
    default_model: str = field(default_factory=lambda: _env("CURSOR_BRIDGE_DEFAULT_MODEL", "composer-2.5"))
    working_dir: str = field(default_factory=lambda: os.path.abspath(_env("CURSOR_BRIDGE_CWD", os.getcwd())))
    agent_name: str = field(default_factory=lambda: _env("CURSOR_BRIDGE_AGENT_NAME", "odysseus-cursor-bridge"))
    request_timeout_s: int = field(default_factory=lambda: int(_env("CURSOR_BRIDGE_TIMEOUT_SECONDS", "180")))
    model_map_raw: str = field(default_factory=lambda: _env("CURSOR_BRIDGE_MODEL_MAP"))
    model_cache_ttl_s: int = field(default_factory=lambda: int(_env("CURSOR_BRIDGE_MODEL_CACHE_TTL_SECONDS", "300")))

    def local_options(self) -> LocalAgentOptions:
        return LocalAgentOptions(cwd=self.working_dir, setting_sources=[])

    def configured_model_map(self) -> Dict[str, str]:
        return parse_model_map(self.model_map_raw, self.default_model)


@dataclass
class SessionRecord:
    agent_id: str
    model: str
    lock: threading.Lock = field(default_factory=threading.Lock)


class SessionStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: Dict[str, SessionRecord] = {}

    def get(self, key: str) -> Optional[SessionRecord]:
        with self._lock:
            return self._sessions.get(key)

    def set(self, key: str, record: SessionRecord) -> None:
        with self._lock:
            self._sessions[key] = record

    def reset_if_model_changed(self, key: str, model: str) -> None:
        with self._lock:
            existing = self._sessions.get(key)
            if existing and existing.model != model:
                self._sessions.pop(key, None)


class ModelCatalog:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cached_at = 0.0
        self._cached: Dict[str, str] = {}

    def get(self, settings: BridgeSettings) -> Dict[str, str]:
        now = time.time()
        with self._lock:
            if self._cached and now - self._cached_at < settings.model_cache_ttl_s:
                return dict(self._cached)
            model_map = settings.configured_model_map()
            if settings.cursor_api_key:
                try:
                    discovered = Cursor.models.list(api_key=settings.cursor_api_key)
                    ids = [model.id for model in discovered if getattr(model, "id", "")]
                    if ids:
                        model_map = {model_id: model_id for model_id in ids}
                except Exception:
                    # Keep the configured fallback map when discovery is unavailable.
                    pass
            self._cached = dict(model_map)
            self._cached_at = now
            return dict(self._cached)


SETTINGS = BridgeSettings()
SESSIONS = SessionStore()
MODELS = ModelCatalog()

app = FastAPI(title="Cursor OpenAI Bridge", version="0.1.0")


def require_bridge_auth(authorization: Optional[str], settings: BridgeSettings) -> None:
    expected = settings.bridge_api_key
    if not expected:
        return
    supplied = (authorization or "").strip()
    if supplied.lower().startswith("bearer "):
        supplied = supplied[7:].strip()
    if supplied != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def cursor_api_key_missing(settings: BridgeSettings) -> bool:
    return not settings.cursor_api_key


def current_model_map(settings: BridgeSettings) -> Dict[str, str]:
    return MODELS.get(settings)


def resolve_requested_model(requested_model: str, settings: BridgeSettings) -> tuple[str, str]:
    model_map = current_model_map(settings)
    requested = (requested_model or "").strip()
    if requested and requested in model_map:
        return requested, model_map[requested]
    if requested and requested not in model_map and requested in model_map.values():
        return requested, requested
    first_exposed, first_actual = next(iter(model_map.items()))
    return first_exposed, first_actual


def build_openai_error(message: str, error_type: str = "bridge_error", code: int = 500) -> JSONResponse:
    return JSONResponse(
        status_code=code,
        content={"error": {"message": message, "type": error_type}},
    )


def _chat_response_id() -> str:
    return "chatcmpl-" + uuid.uuid4().hex


def _chat_chunk_id() -> str:
    return "chatcmpl-" + uuid.uuid4().hex


def _created_ts() -> int:
    return int(time.time())


def _sse_frame(payload: Mapping[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=True)}\n\n".encode("utf-8")


def _session_key_from_request(payload: Mapping[str, Any], header_value: Optional[str]) -> str:
    header_key = (header_value or "").strip()
    if header_key:
        return header_key
    user_value = payload.get("user")
    if isinstance(user_value, str):
        return user_value.strip()
    return ""


def _create_or_resume_agent(session_key: str, actual_model: str, settings: BridgeSettings) -> tuple[Any, Optional[SessionRecord], bool]:
    options = AgentOptions(
        model=actual_model,
        api_key=settings.cursor_api_key,
        name=settings.agent_name,
        local=settings.local_options(),
    )
    if not session_key:
        agent = Agent.create(options)
        return agent, None, True

    SESSIONS.reset_if_model_changed(session_key, actual_model)
    record = SESSIONS.get(session_key)
    if record:
        agent = Agent.resume(record.agent_id, options)
        return agent, record, False

    agent = Agent.create(options)
    record = SessionRecord(agent_id=agent.agent_id, model=actual_model)
    SESSIONS.set(session_key, record)
    return agent, record, True


def _run_prompt(messages: List[Mapping[str, Any]], *, is_new_agent: bool) -> str:
    if is_new_agent:
        return build_full_prompt(messages)
    return build_incremental_prompt(messages)


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "cursor_api_key_configured": bool(SETTINGS.cursor_api_key),
        "working_dir": SETTINGS.working_dir,
    }


@app.get("/v1/models")
def list_models(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    require_bridge_auth(authorization, SETTINGS)
    model_map = current_model_map(SETTINGS)
    data = [
        {
            "id": exposed,
            "object": "model",
            "owned_by": "cursor",
            "permission": [],
            "root": actual,
        }
        for exposed, actual in model_map.items()
    ]
    return {"object": "list", "data": data}


@app.post("/v1/chat/completions")
def chat_completions(
    payload: Dict[str, Any],
    authorization: Optional[str] = Header(default=None),
    x_session_id: Optional[str] = Header(default=None),
):
    require_bridge_auth(authorization, SETTINGS)
    if cursor_api_key_missing(SETTINGS):
        return build_openai_error(
            "CURSOR_API_KEY is not set for the Cursor bridge.",
            "configuration_error",
            500,
        )

    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return build_openai_error("`messages` must be a non-empty list.", "invalid_request_error", 400)

    requested_model = str(payload.get("model") or "").strip()
    exposed_model, actual_model = resolve_requested_model(requested_model, SETTINGS)
    session_key = _session_key_from_request(payload, x_session_id)

    try:
        agent, session_record, is_new_agent = _create_or_resume_agent(session_key, actual_model, SETTINGS)
    except CursorAgentError as exc:
        return build_openai_error(f"Cursor agent startup failed: {exc}", "cursor_startup_error", 502)

    lock = session_record.lock if session_record is not None else None
    prompt = _run_prompt(messages, is_new_agent=is_new_agent)
    chat_id = _chat_response_id()
    created = _created_ts()
    use_stream = bool(payload.get("stream"))

    def _close_agent() -> None:
        try:
            agent.close()
        except Exception:
            pass

    def _send_and_wait_text() -> JSONResponse:
        try:
            run = agent.send(prompt)
            text_parts = list(run.iter_text())
            result = run.wait()
            if str(result.status) == "error":
                return build_openai_error("Cursor run failed.", "cursor_run_error", 502)
            text = "".join(text_parts) or result.result or ""
            return JSONResponse(
                content={
                    "id": chat_id,
                    "object": "chat.completion",
                    "created": created,
                    "model": exposed_model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": text},
                            "finish_reason": "stop",
                        }
                    ],
                }
            )
        except CursorAgentError as exc:
            return build_openai_error(f"Cursor run failed to start: {exc}", "cursor_startup_error", 502)
        finally:
            _close_agent()

    def _stream_events():
        chunk_id = _chat_chunk_id()
        run = None
        try:
            run = agent.send(prompt)
            yield _sse_frame(
                {
                    "id": chunk_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": exposed_model,
                    "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
                }
            )
            for piece in run.iter_text():
                if not piece:
                    continue
                yield _sse_frame(
                    {
                        "id": chunk_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": exposed_model,
                        "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
                    }
                )
            result = run.wait()
            finish_reason = "stop"
            if str(result.status) == "error":
                finish_reason = "error"
            yield _sse_frame(
                {
                    "id": chunk_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": exposed_model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
                }
            )
        except CursorAgentError as exc:
            yield _sse_frame({"error": {"message": f"Cursor run failed to start: {exc}", "type": "cursor_startup_error"}})
        except Exception as exc:
            yield _sse_frame({"error": {"message": f"Bridge streaming failed: {exc}", "type": "bridge_stream_error"}})
        finally:
            _close_agent()
            yield b"data: [DONE]\n\n"

    if lock is not None:
        with lock:
            if use_stream:
                return StreamingResponse(_stream_events(), media_type="text/event-stream")
            return _send_and_wait_text()

    if use_stream:
        return StreamingResponse(_stream_events(), media_type="text/event-stream")
    return _send_and_wait_text()


@app.get("/")
def root() -> PlainTextResponse:
    return PlainTextResponse("Cursor OpenAI bridge is running.\n")

