"""ChatGPT subscription / Codex backend OAuth helpers.

This provider is intentionally separate from OpenAI API-key endpoints. It uses
OpenAI account OAuth device authorization, stores refresh tokens server-side,
and resolves a fresh bearer token at request time.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from typing import Any, Dict, Optional

import httpx
from fastapi import HTTPException

DEFAULT_CHATGPT_SUBSCRIPTION_BASE_URL = (
    os.getenv("CHATGPT_SUBSCRIPTION_BASE_URL", "").strip().rstrip("/")
    or "https://chatgpt.com/backend-api/codex"
)
CHATGPT_SUBSCRIPTION_PROVIDER = "chatgpt-subscription"
CHATGPT_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CHATGPT_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
CHATGPT_OAUTH_ISSUER = "https://auth.openai.com"
CHATGPT_OAUTH_REDIRECT_URI = f"{CHATGPT_OAUTH_ISSUER}/deviceauth/callback"
CHATGPT_ACCESS_TOKEN_REFRESH_SKEW_SECONDS = 120
_AUTH_REFRESH_LOCKS: dict[str, threading.Lock] = {}
_AUTH_REFRESH_LOCKS_GUARD = threading.Lock()


def _database_handles():
    from core.database import ProviderAuthSession, SessionLocal, utcnow_naive
    return ProviderAuthSession, SessionLocal, utcnow_naive


def _refresh_lock_for(auth_id: str) -> threading.Lock:
    with _AUTH_REFRESH_LOCKS_GUARD:
        lock = _AUTH_REFRESH_LOCKS.get(auth_id)
        if lock is None:
            lock = threading.Lock()
            _AUTH_REFRESH_LOCKS[auth_id] = lock
        return lock


class ChatGPTSubscriptionError(RuntimeError):
    """Base error for ChatGPT subscription provider failures."""


class ChatGPTSubscriptionReauthRequired(ChatGPTSubscriptionError):
    """Stored OAuth credentials are invalid or expired beyond refresh."""


class ChatGPTSubscriptionRateLimited(ChatGPTSubscriptionError):
    """Upstream quota/rate limit; reconnecting will not fix it."""


class ChatGPTSubscriptionAuthNotFound(ChatGPTSubscriptionError):
    """No matching owner-scoped auth session exists."""


def is_chatgpt_subscription_base(url: str) -> bool:
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url or "")
        host = (parsed.hostname or "").lower().rstrip(".")
        path = (parsed.path or "").rstrip("/")
    except Exception:
        return False
    return host == "chatgpt.com" and (
        path == "/backend-api/codex" or path.startswith("/backend-api/codex/")
    )


def chatgpt_headers(access_token: Optional[str]) -> Dict[str, str]:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Origin": "https://chatgpt.com",
        "Referer": "https://chatgpt.com/codex",
        "User-Agent": "Odysseus ChatGPT Subscription",
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    return headers


def fetch_available_models(access_token: str, timeout: float = 10.0) -> list[str]:
    if not access_token:
        return []
    try:
        response = httpx.get(
            "https://chatgpt.com/backend-api/codex/models?client_version=1.0.0",
            headers=chatgpt_headers(access_token),
            timeout=timeout,
        )
        if response.status_code != 200:
            return []
        data = response.json()
    except Exception:
        return []
    entries = data.get("models", []) if isinstance(data, dict) else []
    sortable: list[tuple[int, str]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug")
        if not isinstance(slug, str) or not slug.strip():
            continue
        visibility = item.get("visibility", "")
        if isinstance(visibility, str) and visibility.strip().lower() in {"hide", "hidden"}:
            continue
        priority = item.get("priority")
        rank = int(priority) if isinstance(priority, (int, float)) else 10_000
        sortable.append((rank, slug.strip()))
    sortable.sort(key=lambda item: (item[0], item[1]))
    ordered: list[str] = []
    seen: set[str] = set()
    for _, slug in sortable:
        if slug not in seen:
            ordered.append(slug)
            seen.add(slug)
    return ordered


def _raise_for_oauth_response(response: httpx.Response, action: str) -> None:
    if response.status_code < 400:
        return
    code = ""
    message = f"ChatGPT Subscription {action} failed with HTTP {response.status_code}."
    try:
        payload = response.json()
        err = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(err, dict):
            code = str(err.get("code") or err.get("type") or "").strip()
            msg = err.get("message")
            if msg:
                message = f"ChatGPT Subscription {action} failed: {msg}"
        elif isinstance(err, str):
            code = err.strip()
            desc = payload.get("error_description") or payload.get("message")
            if desc:
                message = f"ChatGPT Subscription {action} failed: {desc}"
    except Exception:
        pass
    if response.status_code == 429:
        raise ChatGPTSubscriptionRateLimited(
            "ChatGPT Subscription quota or rate limit was reached. Credentials are still valid."
        )
    if response.status_code in (401, 403) or code in {"invalid_grant", "invalid_token", "invalid_request", "refresh_token_reused"}:
        raise ChatGPTSubscriptionReauthRequired(message)
    raise ChatGPTSubscriptionError(message)


def _json_or_error(response: httpx.Response, action: str) -> Dict[str, Any]:
    _raise_for_oauth_response(response, action)
    try:
        data = response.json()
    except Exception as exc:
        raise ChatGPTSubscriptionError(f"ChatGPT Subscription {action} returned invalid JSON.") from exc
    if not isinstance(data, dict):
        raise ChatGPTSubscriptionError(f"ChatGPT Subscription {action} returned an unexpected response.")
    return data


def request_device_code(timeout: float = 15.0) -> Dict[str, Any]:
    response = httpx.post(
        f"{CHATGPT_OAUTH_ISSUER}/api/accounts/deviceauth/usercode",
        json={"client_id": CHATGPT_OAUTH_CLIENT_ID},
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    data = _json_or_error(response, "device-code request")
    if not data.get("device_auth_id") or not data.get("user_code"):
        raise ChatGPTSubscriptionError("ChatGPT device-code response was missing required fields.")
    data.setdefault("verification_uri", f"{CHATGPT_OAUTH_ISSUER}/codex/device")
    data.setdefault("interval", 5)
    data.setdefault("expires_in", 900)
    return data


def poll_device_auth(device_auth_id: str, user_code: str, timeout: float = 15.0) -> Dict[str, Any]:
    response = httpx.post(
        f"{CHATGPT_OAUTH_ISSUER}/api/accounts/deviceauth/token",
        json={"device_auth_id": device_auth_id, "user_code": user_code},
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    if response.status_code in (403, 404):
        return {"status": "pending", "error": "authorization_pending"}
    return _json_or_error(response, "device-code poll")


def exchange_authorization_code(authorization_code: str, code_verifier: str, timeout: float = 15.0) -> Dict[str, Any]:
    response = httpx.post(
        CHATGPT_OAUTH_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "authorization_code",
            "code": authorization_code,
            "redirect_uri": CHATGPT_OAUTH_REDIRECT_URI,
            "client_id": CHATGPT_OAUTH_CLIENT_ID,
            "code_verifier": code_verifier,
        },
        timeout=timeout,
    )
    data = _json_or_error(response, "token exchange")
    if not data.get("access_token"):
        raise ChatGPTSubscriptionReauthRequired("ChatGPT token exchange did not return an access token.")
    return data


def refresh_oauth_tokens(access_token: str, refresh_token: str, timeout: float = 20.0) -> Dict[str, Any]:
    del access_token
    if not refresh_token:
        raise ChatGPTSubscriptionReauthRequired("ChatGPT Subscription is missing a refresh token. Reconnect the provider.")
    response = httpx.post(
        CHATGPT_OAUTH_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CHATGPT_OAUTH_CLIENT_ID,
        },
        timeout=timeout,
    )
    data = _json_or_error(response, "token refresh")
    if not data.get("access_token"):
        raise ChatGPTSubscriptionReauthRequired("ChatGPT token refresh did not return an access token.")
    return data


def _decode_jwt_payload(token: str) -> Dict[str, Any]:
    parts = (token or "").split(".")
    if len(parts) < 2:
        raise ValueError("not a JWT")
    segment = parts[1]
    segment += "=" * (-len(segment) % 4)
    raw = base64.urlsafe_b64decode(segment.encode("ascii"))
    payload = json.loads(raw.decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def access_token_is_expiring(access_token: str, skew_seconds: int = CHATGPT_ACCESS_TOKEN_REFRESH_SKEW_SECONDS) -> bool:
    try:
        exp = int(_decode_jwt_payload(access_token).get("exp") or 0)
    except Exception:
        return True
    return exp <= int(time.time()) + int(skew_seconds)


def resolve_runtime_credentials(auth_id: str, owner: Optional[str] = None, *, force_refresh: bool = False) -> Dict[str, Any]:
    ProviderAuthSession, SessionLocal, utcnow_naive = _database_handles()
    db = SessionLocal()
    try:
        q = db.query(ProviderAuthSession).filter(
            ProviderAuthSession.id == auth_id,
            ProviderAuthSession.provider == CHATGPT_SUBSCRIPTION_PROVIDER,
        )
        if owner:
            q = q.filter(ProviderAuthSession.owner == owner)
        row = q.first()
        if row is None:
            raise ChatGPTSubscriptionAuthNotFound("ChatGPT Subscription credentials were not found for this user.")

        access_token = row.access_token or ""
        if force_refresh or access_token_is_expiring(access_token):
            with _refresh_lock_for(auth_id):
                db.refresh(row)
                access_token = row.access_token or ""
                refresh_token = row.refresh_token or ""
                if force_refresh or access_token_is_expiring(access_token):
                    refreshed = refresh_oauth_tokens(access_token, refresh_token)
                    row.access_token = refreshed["access_token"]
                    if refreshed.get("refresh_token"):
                        row.refresh_token = refreshed["refresh_token"]
                    row.last_refresh = utcnow_naive()
                    db.commit()
                    db.refresh(row)
            access_token = row.access_token or ""

        return {
            "provider": CHATGPT_SUBSCRIPTION_PROVIDER,
            "base_url": (row.base_url or DEFAULT_CHATGPT_SUBSCRIPTION_BASE_URL).rstrip("/"),
            "api_key": access_token,
            "auth_mode": row.auth_mode or "chatgpt",
        }
    finally:
        db.close()


def to_http_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, ChatGPTSubscriptionRateLimited):
        return HTTPException(429, str(exc))
    if isinstance(exc, (ChatGPTSubscriptionReauthRequired, ChatGPTSubscriptionAuthNotFound)):
        return HTTPException(401, f"{exc} Reconnect the provider.")
    return HTTPException(502, str(exc))


def _content_as_text(content: Any) -> str:
    # Flatten Odysseus message content to text accepted by Responses.
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                if part is not None:
                    parts.append(str(part))
                continue
            value = part.get("text")
            if value is None:
                value = part.get("content")
            if value is not None:
                parts.append(str(value))
        return "\n".join(parts)
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(content)


def _arguments_as_json(arguments: Any) -> str:
    # Return the JSON-string form required by Responses function calls.
    if isinstance(arguments, str):
        return arguments if arguments.strip() else "{}"
    if arguments is None:
        return "{}"
    try:
        return json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return "{}"


def _tool_output_as_text(content: Any) -> str:
    # Responses function_call_output.output must be a string.
    return _content_as_text(content)


def build_responses_tools(tools: Optional[list[dict]]) -> list[dict]:
    # Convert Chat Completions function schemas to Responses schemas.
    converted: list[dict] = []
    for tool in tools or []:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            continue
        function = tool.get("function")
        if not isinstance(function, dict):
            # Accept an already-flattened Responses function schema too.
            function = tool
        name = function.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        parameters = function.get("parameters")
        item: dict[str, Any] = {
            "type": "function",
            "name": name.strip(),
            "parameters": parameters
            if isinstance(parameters, dict)
            else {"type": "object", "properties": {}},
        }
        description = function.get("description")
        if isinstance(description, str) and description:
            item["description"] = description
        strict = function.get("strict", tool.get("strict"))
        if isinstance(strict, bool):
            item["strict"] = strict
        converted.append(item)
    return converted


def _same_responses_model(source_model: Any, requested_model: Any) -> bool:
    if not source_model or not requested_model:
        return True
    source = str(source_model).strip().lower()
    requested = str(requested_model).strip().lower()
    return (
        source == requested
        or source.startswith(requested + "-")
        or requested.startswith(source + "-")
    )


def _sanitize_reasoning_item(item: Any) -> Optional[dict]:
    # Stateless Responses replay needs encrypted_content. Never replay arbitrary
    # response fields or plaintext chain-of-thought content.
    if not isinstance(item, dict) or item.get("type") != "reasoning":
        return None
    encrypted = item.get("encrypted_content")
    if not isinstance(encrypted, str) or not encrypted:
        return None
    cleaned: dict[str, Any] = {
        "type": "reasoning",
        "encrypted_content": encrypted,
    }
    item_id = item.get("id")
    if isinstance(item_id, str) and item_id:
        cleaned["id"] = item_id
    summary = item.get("summary")
    if isinstance(summary, list):
        safe_summary: list[dict] = []
        for part in summary:
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            text = part.get("text")
            if isinstance(part_type, str) and isinstance(text, str):
                safe_summary.append({"type": part_type, "text": text})
        cleaned["summary"] = safe_summary
    return cleaned


def _reasoning_item_key(item: dict) -> str:
    item_id = item.get("id")
    if isinstance(item_id, str) and item_id:
        return "id:" + item_id
    return "enc:" + str(item.get("encrypted_content") or "")


def _replay_reasoning_items(
    tool_calls: Any,
    requested_model: Optional[str],
) -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    for tool_call in tool_calls or []:
        if not isinstance(tool_call, dict):
            continue
        extra = tool_call.get("extra_content")
        if not isinstance(extra, dict):
            continue
        if not _same_responses_model(extra.get("responses_model"), requested_model):
            continue
        for raw in extra.get("responses_reasoning_items") or []:
            item = _sanitize_reasoning_item(raw)
            if item is None:
                continue
            key = _reasoning_item_key(item)
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
    return items


def build_responses_input(
    messages: list[dict],
    model: Optional[str] = None,
) -> list[dict]:
    # Convert canonical Odysseus history to Responses input items. Assistant
    # calls and tool results remain structural and keep the exact call_id.
    input_items: list[dict] = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role") or "user"

        if role == "tool":
            call_id = msg.get("tool_call_id")
            if isinstance(call_id, str) and call_id:
                input_items.append({
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": _tool_output_as_text(msg.get("content")),
                })
            continue

        tool_calls = msg.get("tool_calls") or []
        if role == "assistant" and tool_calls:
            # GPT reasoning models may require the opaque reasoning item from
            # the call-producing response to be replayed before call outputs.
            input_items.extend(_replay_reasoning_items(tool_calls, model))

        content = _content_as_text(msg.get("content"))
        if content or role != "assistant" or not tool_calls:
            input_type = "output_text" if role == "assistant" else "input_text"
            input_items.append({
                "role": role,
                "content": [{"type": input_type, "text": content}],
            })

        if role == "assistant":
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function") or {}
                if not isinstance(function, dict):
                    continue
                name = function.get("name")
                call_id = tool_call.get("id")
                if not isinstance(name, str) or not name.strip():
                    continue
                if not isinstance(call_id, str) or not call_id:
                    continue
                input_items.append({
                    "type": "function_call",
                    "call_id": call_id,
                    "name": name.strip(),
                    "arguments": _arguments_as_json(function.get("arguments")),
                })
    return input_items


class ResponsesToolCallAccumulator:
    # Aggregate streamed Responses function calls and replayable reasoning.

    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []
        self._reasoning_items: list[dict] = []
        self._reasoning_keys: set[str] = set()
        self._response_model: Optional[str] = None

    def _capture_response_model(self, event: dict) -> None:
        response = event.get("response")
        if isinstance(response, dict):
            model = response.get("model")
            if isinstance(model, str) and model:
                self._response_model = model
        model = event.get("model")
        if isinstance(model, str) and model:
            self._response_model = model

    def _capture_reasoning(self, item: Any) -> None:
        cleaned = _sanitize_reasoning_item(item)
        if cleaned is None:
            return
        key = _reasoning_item_key(cleaned)
        if key in self._reasoning_keys:
            return
        self._reasoning_keys.add(key)
        self._reasoning_items.append(cleaned)

    def _find(
        self,
        *,
        output_index: Any = None,
        call_id: Any = None,
        item_id: Any = None,
    ) -> Optional[dict[str, Any]]:
        for record in self._records:
            if output_index is not None and record.get("output_index") == output_index:
                return record
            if call_id and record.get("call_id") == call_id:
                return record
            if item_id and record.get("item_id") == item_id:
                return record
        return None

    def _record(self, event: dict, item: Optional[dict] = None) -> dict[str, Any]:
        item = item if isinstance(item, dict) else {}
        output_index = event.get("output_index", item.get("output_index"))
        call_id = item.get("call_id") or event.get("call_id")
        item_id = item.get("id") or event.get("item_id")
        record = self._find(
            output_index=output_index,
            call_id=call_id,
            item_id=item_id,
        )
        if record is None:
            record = {
                "output_index": output_index,
                "call_id": call_id,
                "item_id": item_id,
                "name": "",
                "arguments": "",
                "argument_deltas": [],
                "sequence": len(self._records),
            }
            self._records.append(record)
        else:
            if record.get("output_index") is None and output_index is not None:
                record["output_index"] = output_index
            if not record.get("call_id") and call_id:
                record["call_id"] = call_id
            if not record.get("item_id") and item_id:
                record["item_id"] = item_id
        return record

    def feed(self, event: dict, event_type: str = "") -> None:
        if not isinstance(event, dict):
            return
        self._capture_response_model(event)
        kind = event_type or str(event.get("type") or "")

        if kind in {"response.output_item.added", "response.output_item.done"}:
            item = event.get("item") or {}
            if not isinstance(item, dict):
                return
            if item.get("type") == "reasoning":
                self._capture_reasoning(item)
                return
            if item.get("type") != "function_call":
                return
            record = self._record(event, item)
            if isinstance(item.get("name"), str):
                record["name"] = item["name"]
            if isinstance(item.get("arguments"), str):
                if kind == "response.output_item.done" or item["arguments"]:
                    record["arguments"] = item["arguments"]
            return

        if kind in {
            "response.function_call_arguments.delta",
            "response.function_call_arguments.done",
        }:
            item = event.get("item")
            record = self._record(event, item if isinstance(item, dict) else None)
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                record["name"] = item["name"]
            if kind.endswith(".delta"):
                delta = event.get("delta")
                if isinstance(delta, str) and delta:
                    record["argument_deltas"].append(delta)
            else:
                arguments = event.get("arguments")
                if not isinstance(arguments, str) and isinstance(item, dict):
                    arguments = item.get("arguments")
                if isinstance(arguments, str):
                    record["arguments"] = arguments
            return

        if kind == "response.completed":
            response = event.get("response") or {}
            if not isinstance(response, dict):
                return
            for index, item in enumerate(response.get("output") or []):
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "reasoning":
                    self._capture_reasoning(item)
                    continue
                if item.get("type") != "function_call":
                    continue
                synthetic_event = dict(event)
                synthetic_event["output_index"] = item.get("output_index", index)
                record = self._record(synthetic_event, item)
                if isinstance(item.get("name"), str):
                    record["name"] = item["name"]
                if isinstance(item.get("arguments"), str):
                    record["arguments"] = item["arguments"]

    def calls(self) -> list[dict]:
        calls: list[dict] = []
        seen: set[str] = set()
        records = sorted(
            self._records,
            key=lambda record: (
                record.get("output_index")
                if isinstance(record.get("output_index"), int)
                else 10**9,
                record.get("sequence", 0),
            ),
        )
        for index, record in enumerate(records):
            name = record.get("name")
            if not isinstance(name, str) or not name:
                continue
            call_id = record.get("call_id") or record.get("item_id") or f"call_{index}"
            call_id = str(call_id)
            if call_id in seen:
                continue
            seen.add(call_id)
            arguments = record.get("arguments")
            if not isinstance(arguments, str) or not arguments:
                arguments = "".join(record.get("argument_deltas") or []) or "{}"
            calls.append({
                "id": call_id,
                "name": name,
                "arguments": arguments,
            })

        if calls and self._reasoning_items:
            extra: dict[str, Any] = {
                "responses_reasoning_items": list(self._reasoning_items),
            }
            if self._response_model:
                extra["responses_model"] = self._response_model
            # Agent history preserves extra_content on each canonical tool call.
            # Attach the shared reasoning envelope once to avoid replay duplicates.
            calls[0]["extra_content"] = extra
        return calls
