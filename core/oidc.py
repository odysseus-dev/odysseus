"""Minimal OpenID Connect helpers for optional Authentik login."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import secrets
from functools import lru_cache
from typing import Any, Dict, Optional

import httpx
from authlib.jose import JsonWebKey, jwt

log = logging.getLogger(__name__)

AUTHENTIK_ISSUER = os.getenv("AUTHENTIK_ISSUER", "").strip().rstrip("/")
AUTHENTIK_CLIENT_ID = os.getenv("AUTHENTIK_ID", "").strip()
AUTHENTIK_CLIENT_SECRET = os.getenv("AUTHENTIK_SECRET", "").strip()
AUTHENTIK_SCOPE = os.getenv("AUTHENTIK_SCOPE", "openid email profile").strip() or "openid email profile"
AUTHENTIK_REQUIRE_EMAIL_VERIFIED = os.getenv("AUTHENTIK_REQUIRE_EMAIL_VERIFIED", "true").lower() != "false"
AUTHENTIK_REDIRECT_URI = os.getenv("AUTHENTIK_REDIRECT_URI", "").strip()


def authentik_env_configured() -> bool:
    return bool(AUTHENTIK_ISSUER and AUTHENTIK_CLIENT_ID and AUTHENTIK_CLIENT_SECRET)


def get_authentik_config(settings: Optional[dict] = None) -> Dict[str, Any]:
    cfg = settings or {}
    enabled_default = os.getenv("AUTHENTIK_ENABLE", "false").lower() == "true"
    auto_create_default = os.getenv("AUTHENTIK_AUTO_CREATE_USERS", "true").lower() != "false"
    return {
        "enabled": bool(cfg.get("authentik_enabled", enabled_default)),
        "auto_create_users": bool(cfg.get("authentik_auto_create_users", auto_create_default)),
        "configured": authentik_env_configured(),
        "issuer": AUTHENTIK_ISSUER,
        "client_id": AUTHENTIK_CLIENT_ID,
        "scope": AUTHENTIK_SCOPE,
    }


def sanitize_username(value: str) -> str:
    clean = re.sub(r"[^a-z0-9._-]+", "-", (value or "").strip().lower())
    clean = re.sub(r"[-_.]{2,}", "-", clean).strip("-._")
    return clean or f"authentik-{secrets.token_hex(4)}"


def derive_username(claims: Dict[str, Any]) -> str:
    preferred = (claims.get("preferred_username") or "").strip()
    email = (claims.get("email") or "").strip()
    sub = (claims.get("sub") or "").strip()
    if preferred:
        return sanitize_username(preferred)
    if email:
        return sanitize_username(email.split("@", 1)[0])
    if sub:
        return sanitize_username(f"authentik-{sub[:12]}")
    return sanitize_username("")


def get_login_redirect_path(request) -> str:
    path = (request.query_params.get("next") or "/").strip()
    if not path.startswith("/") or path.startswith("//"):
        return "/"
    return path


def build_redirect_uri(request) -> str:
    if AUTHENTIK_REDIRECT_URI:
        return AUTHENTIK_REDIRECT_URI
    try:
        return str(request.url_for("authentik_callback"))
    except Exception:
        return f"{str(request.base_url).rstrip('/')}/api/auth/oidc/callback"


@lru_cache(maxsize=1)
def _issuer_metadata_cache_key() -> str:
    return AUTHENTIK_ISSUER


async def discover_provider() -> Dict[str, Any]:
    if not AUTHENTIK_ISSUER:
        raise RuntimeError("AUTHENTIK_ISSUER is not configured")
    url = f"{AUTHENTIK_ISSUER}/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


def _state_payload(state: str, nonce: str, next_path: str) -> str:
    raw = json.dumps({"state": state, "nonce": nonce, "next": next_path}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_state_payload(payload: str) -> Dict[str, str]:
    padded = payload + "=" * (-len(payload) % 4)
    raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    data = json.loads(raw)
    return {
        "state": str(data.get("state") or ""),
        "nonce": str(data.get("nonce") or ""),
        "next": str(data.get("next") or "/"),
    }


def create_state_payload(next_path: str) -> Dict[str, str]:
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    return {
        "state": state,
        "nonce": nonce,
        "payload": _state_payload(state, nonce, next_path),
    }


async def build_authorization_url(request, next_path: str) -> Dict[str, Any]:
    discovery = await discover_provider()
    state_data = create_state_payload(next_path)
    params = {
        "response_type": "code",
        "client_id": AUTHENTIK_CLIENT_ID,
        "redirect_uri": build_redirect_uri(request),
        "scope": AUTHENTIK_SCOPE,
        "state": state_data["state"],
        "nonce": state_data["nonce"],
    }
    auth_url = httpx.URL(discovery["authorization_endpoint"]).copy_merge_params(params)
    return {"authorization_url": str(auth_url), **state_data}


async def exchange_code(code: str, request) -> Dict[str, Any]:
    discovery = await discover_provider()
    redirect_uri = build_redirect_uri(request)
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": AUTHENTIK_CLIENT_ID,
        "client_secret": AUTHENTIK_CLIENT_SECRET,
    }
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        resp = await client.post(discovery["token_endpoint"], data=payload, headers={"Accept": "application/json"})
        resp.raise_for_status()
        return resp.json()


async def fetch_userinfo(access_token: str) -> Dict[str, Any]:
    discovery = await discover_provider()
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        resp = await client.get(discovery["userinfo_endpoint"], headers={"Authorization": f"Bearer {access_token}"})
        resp.raise_for_status()
        return resp.json()


async def validate_id_token(id_token: str, nonce: str) -> Dict[str, Any]:
    discovery = await discover_provider()
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        jwks_resp = await client.get(discovery["jwks_uri"])
        jwks_resp.raise_for_status()
    key_set = JsonWebKey.import_key_set(jwks_resp.json())
    claims = jwt.decode(id_token, key_set)
    claims.validate()
    if claims.get("iss") != AUTHENTIK_ISSUER:
        raise ValueError("Invalid token issuer")
    aud = claims.get("aud")
    if isinstance(aud, list):
        ok_aud = AUTHENTIK_CLIENT_ID in aud
    else:
        ok_aud = aud == AUTHENTIK_CLIENT_ID
    if not ok_aud:
        raise ValueError("Invalid token audience")
    if claims.get("nonce") != nonce:
        raise ValueError("Invalid token nonce")
    return dict(claims)


def merge_claims(token_claims: Dict[str, Any], userinfo: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    merged = dict(token_claims)
    if userinfo:
        merged.update({k: v for k, v in userinfo.items() if v not in (None, "")})
    return merged
