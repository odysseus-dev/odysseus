"""OpenAI Codex ChatGPT-subscription provider support."""

from __future__ import annotations

import base64
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import httpx
from fastapi import HTTPException

from core.database import (
    ModelEndpoint,
    ProviderOAuthCredential,
    ProviderOAuthDeviceLogin,
    SessionLocal,
)

logger = logging.getLogger(__name__)

PROVIDER = "openai_codex"
DISPLAY_NAME = "OpenAI Codex"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTH_BASE = "https://auth.openai.com"
DEVICE_CODE_URL = f"{AUTH_BASE}/oauth/device/code"
TOKEN_URL = f"{AUTH_BASE}/oauth/token"
DEVICE_USERCODE_URL = f"{AUTH_BASE}/api/accounts/deviceauth/usercode"
DEVICE_TOKEN_POLL_URL = f"{AUTH_BASE}/api/accounts/deviceauth/token"
DEVICE_VERIFICATION_URI = f"{AUTH_BASE}/codex/device"
DEVICE_REDIRECT_URI = f"{AUTH_BASE}/deviceauth/callback"
CODEX_ORIGINATOR = "codex_cli_rs"
CODEX_USER_AGENT = "codex_cli_rs/0.40.0 (Linux; x86_64)"
SCOPE = "openid profile email offline_access"
CHATGPT_BASE_URL = "https://chatgpt.com/backend-api"
CODEX_CHAT_URL = f"{CHATGPT_BASE_URL}/codex/responses"
OPENAI_BETA_SSE = "responses=experimental"
OPENAI_BETA_WEBSOCKET = "responses_websockets=2026-02-06"
JWT_CLAIM_PATH = "https://api.openai.com/auth"
TOKEN_REFRESH_SKEW_SECONDS = 120

# Reasoning effort levels accepted by the Codex Responses API for the gpt-5.x
# models. "off" is special: like the codex CLI / pi (thinkingLevelMap off→null),
# it means "send no reasoning block" so the model uses its built-in default —
# NOT zero reasoning. Users who want the least thinking pick "minimal".
CODEX_REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")
CODEX_DEFAULT_REASONING_EFFORT = "medium"


def normalize_reasoning_effort(value: object) -> Optional[str]:
    """Map a user-supplied effort string to a valid level, "off", or None.

    Returns one of CODEX_REASONING_EFFORTS, the literal "off" (omit reasoning),
    or None when the value is empty/unrecognised (caller falls back to default).
    """
    if value is None:
        return None
    v = str(value).strip().lower()
    if not v:
        return None
    if v in ("off", "none", "default"):
        return "off"
    if v in CODEX_REASONING_EFFORTS:
        return v
    return None


CODEX_MODELS: List[str] = [
    "gpt-5.1",
    "gpt-5.1-codex-max",
    "gpt-5.1-codex-mini",
    "gpt-5.2",
    "gpt-5.2-codex",
    "gpt-5.3-codex",
    "gpt-5.3-codex-spark",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.5",
]


def owner_key(owner: Optional[str]) -> str:
    return owner or ""


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_expiry(expires_in: object) -> datetime:
    try:
        seconds = int(expires_in)
    except Exception:
        seconds = 3600
    return _now() + timedelta(seconds=max(60, seconds))


def _json_list(values: List[str]) -> str:
    return json.dumps(values, separators=(",", ":"))


def _auth_headers(content_type: str) -> Dict[str, str]:
    return {
        "Content-Type": content_type,
        "User-Agent": CODEX_USER_AGENT,
        "originator": CODEX_ORIGINATOR,
    }


def _jwt_payload(token: str) -> dict:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
    except Exception:
        return {}


def extract_account_id(token: str) -> Optional[str]:
    auth_claim = _jwt_payload(token).get(JWT_CLAIM_PATH)
    if isinstance(auth_claim, dict):
        account_id = auth_claim.get("chatgpt_account_id")
        if isinstance(account_id, str) and account_id:
            return account_id
    return None


def _to_responses_tool(tool: dict) -> dict:
    """Convert a Chat-Completions function tool to the Responses API shape.

    Chat Completions: {"type":"function","function":{"name","description","parameters"}}
    Responses API:    {"type":"function","name","description","parameters", ...}

    Non-function tools (or already-flat tools) are returned unchanged.
    """
    if not isinstance(tool, dict):
        return tool
    fn = tool.get("function")
    if not isinstance(fn, dict):
        return tool  # already flat, or a non-function tool type
    out = {"type": tool.get("type", "function")}
    if fn.get("name") is not None:
        out["name"] = fn.get("name")
    if fn.get("description") is not None:
        out["description"] = fn.get("description")
    if fn.get("parameters") is not None:
        out["parameters"] = fn.get("parameters")
    if fn.get("strict") is not None:
        out["strict"] = fn.get("strict")
    return out


def _coerce_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value)


def _function_call_item(tc: dict) -> Optional[dict]:
    """Convert a Chat-Completions tool_call into a Responses `function_call` item.

    Chat Completions: {"id","type":"function","function":{"name","arguments"}}
    Responses API:    {"type":"function_call","call_id","name","arguments"}

    `arguments` must be a JSON string. The item `id` (fc_...) is intentionally
    omitted: we never replay reasoning items, so omitting it avoids the
    rs_/fc_ pairing validation the Responses API would otherwise enforce.
    """
    if not isinstance(tc, dict):
        return None
    fn = tc.get("function")
    if isinstance(fn, dict):
        name = fn.get("name") or ""
        arguments = fn.get("arguments")
    else:
        name = tc.get("name") or ""
        arguments = tc.get("arguments")
    call_id = tc.get("id") or tc.get("call_id") or ""
    if arguments is None:
        arguments = "{}"
    elif not isinstance(arguments, str):
        arguments = json.dumps(arguments)
    return {
        "type": "function_call",
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
    }


def build_codex_payload(
    model: str,
    messages: List[dict],
    temperature: float,
    max_tokens: int,
    tools: Optional[List[dict]] = None,
    session_id: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    replay_items: Optional[Dict[str, list]] = None,
) -> dict:
    # The Codex Responses API `input` array accepts either EasyInputMessages
    # ({"role","content"}) or typed items (function_call / function_call_output).
    # Chat-Completions assistant tool-call turns and `role:"tool"` results have
    # no valid EasyInputMessage representation, so they must be converted to the
    # typed item shapes — otherwise the API 400s with
    # "Missing required parameter: 'input[N].content'".
    system_parts: List[str] = []
    input_items: List[dict] = []
    for msg in messages or []:
        role = msg.get("role")
        if role == "system":
            content = msg.get("content")
            if content:
                system_parts.append(str(content))
            continue
        if role == "tool":
            input_items.append({
                "type": "function_call_output",
                "call_id": msg.get("tool_call_id") or msg.get("call_id") or "",
                "output": _coerce_text(msg.get("content", "")),
            })
            continue
        if role == "assistant":
            tool_calls = msg.get("tool_calls") or []
            surviving_ids = {
                str(tc.get("id")) for tc in tool_calls
                if isinstance(tc, dict) and tc.get("id")
            }
            first_id = next(
                (str(tc.get("id")) for tc in tool_calls if isinstance(tc, dict) and tc.get("id")),
                None,
            )
            replay = replay_items.get(first_id) if (replay_items and first_id) else None
            # Reasoning replay is turn-level all-or-nothing. A reasoning item's
            # encrypted_content is generated as the chain leading to ALL of the
            # turn's (possibly parallel) tool calls, so it only stays valid if
            # every one of those calls is still present with its tool result. If
            # any call was trimmed (compaction/sanitize), replaying a partial set
            # leaves the reasoning item half-matched — which can 400 just like an
            # orphaned function_call. So: replay verbatim only when every
            # function_call survives; otherwise fall through to the verified
            # id-less reconstruction below. Each turn is thus either fully intact
            # or safely reconstructed — never a half-state.
            if replay:
                replay_call_ids = {
                    str(it.get("call_id")) for it in replay
                    if isinstance(it, dict) and it.get("type") == "function_call" and it.get("call_id") is not None
                }
                if replay_call_ids and replay_call_ids <= surviving_ids:
                    for it in replay:
                        if isinstance(it, dict):
                            input_items.append(it)
                    continue
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                input_items.append({"role": "assistant", "content": content})
            elif isinstance(content, list) and content:
                input_items.append({"role": "assistant", "content": content})
            for tc in tool_calls:
                fc = _function_call_item(tc)
                if fc is not None:
                    input_items.append(fc)
            legacy_fc = msg.get("function_call")
            if isinstance(legacy_fc, dict):
                fc = _function_call_item({"function": legacy_fc})
                if fc is not None:
                    input_items.append(fc)
            continue
        # user (and any other role with plain content) → EasyInputMessage.
        content = msg.get("content")
        if role and content is not None:
            input_items.append({"role": role, "content": content})

    payload = {
        "model": model,
        "store": False,
        "stream": True,
        "instructions": "\n\n".join(system_parts),
        "input": input_items,
        "include": ["reasoning.encrypted_content"],
        "tool_choice": "auto",
        "parallel_tool_calls": True,
        "text": {"verbosity": "medium"},
    }
    # Codex Responses API (gpt-5.x reasoning models) rejects temperature.
    # Codex Responses API rejects max_output_tokens (model decides length).
    # Reasoning effort: emit a {effort, summary} block for an explicit level;
    # "off"/None omits it so the model uses its built-in default. `summary:auto`
    # keeps the reasoning-summary deltas the SSE parser surfaces as thinking.
    effort = normalize_reasoning_effort(reasoning_effort)
    if effort and effort != "off":
        payload["reasoning"] = {"effort": effort, "summary": "auto"}
    if tools:
        payload["tools"] = [_to_responses_tool(t) for t in tools]
    if session_id:
        payload["prompt_cache_key"] = session_id
    return payload


def ensure_codex_endpoint(db, owner: Optional[str] = None) -> ModelEndpoint:
    owner = owner_key(owner)
    existing = (
        db.query(ModelEndpoint)
        .filter(ModelEndpoint.base_url == CHATGPT_BASE_URL)
        .filter(ModelEndpoint.owner == owner)
        .first()
    )
    if existing:
        existing.name = DISPLAY_NAME
        existing.cached_models = _json_list(CODEX_MODELS)
        existing.pinned_models = _json_list(CODEX_MODELS)
        existing.model_type = "llm"
        existing.supports_tools = True
        existing.is_enabled = True
        return existing

    ep = ModelEndpoint(
        id=str(uuid.uuid4())[:8],
        name=DISPLAY_NAME,
        base_url=CHATGPT_BASE_URL,
        api_key=None,
        is_enabled=True,
        cached_models=_json_list(CODEX_MODELS),
        pinned_models=_json_list(CODEX_MODELS),
        model_type="llm",
        supports_tools=True,
        owner=owner,
    )
    db.add(ep)
    return ep


def get_credential(db, owner: Optional[str]) -> Optional[ProviderOAuthCredential]:
    return (
        db.query(ProviderOAuthCredential)
        .filter(ProviderOAuthCredential.provider == PROVIDER)
        .filter(ProviderOAuthCredential.owner == owner_key(owner))
        .first()
    )


def credential_status(owner: Optional[str]) -> dict:
    db = SessionLocal()
    try:
        cred = get_credential(db, owner)
        endpoint = (
            db.query(ModelEndpoint)
            .filter(ModelEndpoint.base_url == CHATGPT_BASE_URL)
            .filter(ModelEndpoint.owner == owner_key(owner))
            .first()
        )
        return {
            "provider": PROVIDER,
            "connected": bool(cred and cred.refresh_token and cred.status != "error"),
            "status": cred.status if cred else "disconnected",
            "account_id": cred.account_id if cred else None,
            "expires_at": cred.expires_at.isoformat() if cred and cred.expires_at else None,
            "error": cred.error if cred else None,
            "endpoint_id": endpoint.id if endpoint else None,
            "models": list(CODEX_MODELS),
        }
    finally:
        db.close()


async def start_device_login(owner: Optional[str]) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            DEVICE_USERCODE_URL,
            json={"client_id": CLIENT_ID},
            headers=_auth_headers("application/json"),
        )
    if not response.is_success:
        raise HTTPException(response.status_code, f"OpenAI device login failed: {response.text[:300]}")
    data = response.json()
    device_auth_id = data.get("device_auth_id")
    user_code = data.get("user_code")
    if not device_auth_id or not user_code:
        raise HTTPException(502, "OpenAI device login response was missing device_auth_id or user_code")
    expires_at = _parse_expiry(900)
    interval = int(data.get("interval") or 5)
    login_id = str(uuid.uuid4())

    db = SessionLocal()
    try:
        row = ProviderOAuthDeviceLogin(
            id=login_id,
            provider=PROVIDER,
            owner=owner_key(owner),
            device_auth_id=device_auth_id,
            user_code=user_code,
            verification_uri=DEVICE_VERIFICATION_URI,
            verification_uri_complete=DEVICE_VERIFICATION_URI,
            expires_at=expires_at,
            interval_seconds=interval,
            status="pending",
        )
        db.add(row)
        db.commit()
    finally:
        db.close()

    return {
        "login_id": login_id,
        "user_code": user_code,
        "verification_uri": DEVICE_VERIFICATION_URI,
        "verification_uri_complete": DEVICE_VERIFICATION_URI,
        "expires_at": expires_at.isoformat(),
        "interval_seconds": interval,
        "status": "pending",
    }


async def poll_device_login(owner: Optional[str], login_id: str) -> dict:
    db = SessionLocal()
    try:
        row = (
            db.query(ProviderOAuthDeviceLogin)
            .filter(ProviderOAuthDeviceLogin.id == login_id)
            .filter(ProviderOAuthDeviceLogin.provider == PROVIDER)
            .filter(ProviderOAuthDeviceLogin.owner == owner_key(owner))
            .first()
        )
        if not row:
            raise HTTPException(404, "Login not found")
        if row.expires_at <= _now():
            row.status = "expired"
            row.error = "Device login expired"
            db.commit()
            return {"status": "expired", "error": row.error}
        device_auth_id = row.device_auth_id
        user_code = row.user_code
        interval = row.interval_seconds or 5
    finally:
        db.close()

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            DEVICE_TOKEN_POLL_URL,
            json={"device_auth_id": device_auth_id, "user_code": user_code},
            headers=_auth_headers("application/json"),
        )

    # 403/404 means the user has not approved yet -> still pending.
    if response.status_code in (403, 404):
        return {"status": "pending", "interval_seconds": interval}

    if not response.is_success:
        db = SessionLocal()
        try:
            row = db.query(ProviderOAuthDeviceLogin).filter(ProviderOAuthDeviceLogin.id == login_id).first()
            if row:
                row.status = "error"
                row.error = response.text[:300]
                db.commit()
        finally:
            db.close()
        return {"status": "error", "error": response.text[:300]}

    poll_data = response.json()
    authorization_code = poll_data.get("authorization_code")
    code_verifier = poll_data.get("code_verifier")
    if not authorization_code or not code_verifier:
        return {"status": "error", "error": "Device token response was missing authorization_code or code_verifier"}

    # Step 3: exchange the PKCE authorization code for tokens.
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": authorization_code,
                "redirect_uri": DEVICE_REDIRECT_URI,
                "client_id": CLIENT_ID,
                "code_verifier": code_verifier,
            },
            headers=_auth_headers("application/x-www-form-urlencoded"),
        )

    if not response.is_success:
        raise HTTPException(response.status_code, f"OpenAI token exchange failed: {response.text[:300]}")

    data = response.json()
    access = data.get("access_token")
    refresh = data.get("refresh_token")
    if not access or not refresh:
        raise HTTPException(502, "OpenAI token response was missing access_token or refresh_token")

    expires_at = _parse_expiry(data.get("expires_in", 3600))
    account_id = extract_account_id(access)
    db = SessionLocal()
    try:
        cred = get_credential(db, owner)
        if not cred:
            cred = ProviderOAuthCredential(id=str(uuid.uuid4()), provider=PROVIDER, owner=owner_key(owner))
            db.add(cred)
        cred.access_token = access
        cred.refresh_token = refresh
        cred.expires_at = expires_at
        cred.account_id = account_id
        cred.status = "connected"
        cred.error = None
        row = db.query(ProviderOAuthDeviceLogin).filter(ProviderOAuthDeviceLogin.id == login_id).first()
        if row:
            row.status = "complete"
        ep = ensure_codex_endpoint(db, owner)
        db.commit()
        return {
            "status": "connected",
            "account_id": account_id,
            "expires_at": expires_at.isoformat(),
            "endpoint_id": ep.id,
            "models": list(CODEX_MODELS),
        }
    finally:
        db.close()


async def refresh_credential(db, cred: ProviderOAuthCredential) -> ProviderOAuthCredential:
    if not cred.refresh_token:
        cred.status = "error"
        cred.error = "Missing refresh token"
        db.commit()
        raise HTTPException(401, "OpenAI Codex is not connected")

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": cred.refresh_token,
                "client_id": CLIENT_ID,
            },
            headers=_auth_headers("application/x-www-form-urlencoded"),
        )

    if not response.is_success:
        cred.status = "error"
        cred.error = response.text[:500]
        db.commit()
        raise HTTPException(401, "OpenAI Codex token refresh failed")

    data = response.json()
    access = data.get("access_token")
    refresh = data.get("refresh_token")
    if not access or not refresh:
        cred.status = "error"
        cred.error = "Refresh response missing token fields"
        db.commit()
        raise HTTPException(502, "OpenAI Codex refresh response was invalid")

    cred.access_token = access
    cred.refresh_token = refresh
    cred.expires_at = _parse_expiry(data.get("expires_in", 3600))
    cred.account_id = extract_account_id(access) or cred.account_id
    cred.status = "connected"
    cred.error = None
    db.commit()
    db.refresh(cred)
    return cred


async def resolve_codex_headers(owner: Optional[str], session_id: Optional[str] = None, *, websocket: bool = False) -> Dict[str, str]:
    db = SessionLocal()
    try:
        cred = get_credential(db, owner)
        if not cred or not cred.access_token:
            raise HTTPException(401, "OpenAI Codex is not connected")
        if not cred.expires_at or cred.expires_at <= _now() + timedelta(seconds=TOKEN_REFRESH_SKEW_SECONDS):
            cred = await refresh_credential(db, cred)
        account_id = cred.account_id or extract_account_id(cred.access_token)
        if not account_id:
            raise HTTPException(401, "OpenAI Codex token is missing account id")
        headers = {
            "Authorization": f"Bearer {cred.access_token}",
            "chatgpt-account-id": account_id,
            "originator": CODEX_ORIGINATOR,
            "User-Agent": CODEX_USER_AGENT,
        }
        if websocket:
            headers["OpenAI-Beta"] = OPENAI_BETA_WEBSOCKET
            headers["x-client-request-id"] = session_id or str(uuid.uuid4())
            headers["session_id"] = session_id or headers["x-client-request-id"]
        else:
            headers["OpenAI-Beta"] = OPENAI_BETA_SSE
            headers["accept"] = "text/event-stream"
            headers["content-type"] = "application/json"
            if session_id:
                headers["session_id"] = session_id
        return headers
    finally:
        db.close()


def logout(owner: Optional[str]) -> dict:
    db = SessionLocal()
    try:
        cred = get_credential(db, owner)
        if cred:
            db.delete(cred)
        db.query(ProviderOAuthDeviceLogin).filter(
            ProviderOAuthDeviceLogin.provider == PROVIDER,
            ProviderOAuthDeviceLogin.owner == owner_key(owner),
        ).delete(synchronize_session=False)
        db.commit()
        return {"status": "disconnected"}
    finally:
        db.close()
