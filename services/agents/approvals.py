"""Exact-action ApprovalGrant issuance and one-time consumption."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, replace
from typing import Any


class ApprovalDenied(Exception):
    """Grant verification failed. Message is log-safe."""


def normalize_action(arguments: dict[str, Any]) -> str:
    """Canonical UTF-8 JSON: sorted keys, compact separators."""
    return json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def action_digest(arguments: dict[str, Any]) -> str:
    return hashlib.sha256(normalize_action(arguments).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ConfirmedAction:
    event_id: str
    execution_id: str
    conversation_id: str
    tool: str
    arguments: dict[str, Any]
    owner: str
    profile_id: str
    profile_revision: int
    resources: frozenset[str]

    def with_args(self, **overrides: Any) -> ConfirmedAction:
        merged = dict(self.arguments)
        merged.update(overrides)
        return replace(self, arguments=merged)

    def with_tool(self, tool: str) -> ConfirmedAction:
        return replace(self, tool=tool)


@dataclass(frozen=True)
class ApprovalGrant:
    event_id: str
    execution_id: str
    conversation_id: str
    tool: str
    args_digest: str
    owner: str
    profile_id: str
    profile_revision: int
    resources: frozenset[str]
    nonce: str
    issued_at: float
    expires_at: float
    signature: str


class ApprovalAuthority:
    def __init__(self, hmac_key: bytes | None = None) -> None:
        if hmac_key is None:
            from src.secret_storage import hmac_secret

            hmac_key = hmac_secret("approval-grant")
        self._key = hmac_key
        self._consumed: set[str] = set()
        self._results: dict[str, dict[str, Any]] = {}

    def _payload(self, grant: ApprovalGrant) -> bytes:
        body = {
            "event_id": grant.event_id,
            "execution_id": grant.execution_id,
            "conversation_id": grant.conversation_id,
            "tool": grant.tool,
            "args_digest": grant.args_digest,
            "owner": grant.owner,
            "profile_id": grant.profile_id,
            "profile_revision": grant.profile_revision,
            "resources": sorted(grant.resources),
            "nonce": grant.nonce,
            "issued_at": grant.issued_at,
            "expires_at": grant.expires_at,
        }
        return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")

    def _sign(self, grant: ApprovalGrant) -> ApprovalGrant:
        signature = hmac.new(self._key, self._payload(grant), hashlib.sha256).hexdigest()
        return replace(grant, signature=signature)

    def issue_from_confirmation(
        self,
        action: ConfirmedAction,
        *,
        accepted: bool = True,
        ttl_seconds: float = 60,
        now: float | None = None,
    ) -> ApprovalGrant:
        if not accepted:
            raise ApprovalDenied("confirmation rejected")
        issued_at = time.time() if now is None else now
        unsigned = ApprovalGrant(
            event_id=action.event_id,
            execution_id=action.execution_id,
            conversation_id=action.conversation_id,
            tool=action.tool,
            args_digest=action_digest(action.arguments),
            owner=action.owner,
            profile_id=action.profile_id,
            profile_revision=action.profile_revision,
            resources=frozenset(action.resources),
            nonce=secrets.token_hex(8),
            issued_at=issued_at,
            expires_at=issued_at + float(ttl_seconds),
            signature="",
        )
        return self._sign(unsigned)

    def verify_and_consume(
        self,
        grant: ApprovalGrant,
        action: ConfirmedAction,
        *,
        idempotency_key: str | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        if idempotency_key and idempotency_key in self._results:
            return self._results[idempotency_key]
        expected = hmac.new(self._key, self._payload(grant), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, grant.signature):
            raise ApprovalDenied("invalid signature")
        if grant.nonce in self._consumed:
            raise ApprovalDenied("replayed grant")
        moment = time.time() if now is None else now
        if grant.expires_at < moment:
            raise ApprovalDenied("grant expired")
        if grant.execution_id != action.execution_id:
            raise ApprovalDenied("execution mismatch")
        if grant.profile_revision != action.profile_revision or grant.profile_id != action.profile_id:
            raise ApprovalDenied("profile mismatch")
        if grant.tool != action.tool:
            raise ApprovalDenied("tool mismatch")
        if frozenset(grant.resources) != frozenset(action.resources):
            raise ApprovalDenied("resource mismatch")
        if grant.args_digest != action_digest(action.arguments) or grant.event_id != action.event_id:
            raise ApprovalDenied("action mismatch")
        self._consumed.add(grant.nonce)
        result = {
            "ok": True,
            "event_id": grant.event_id,
            "tool": grant.tool,
            "execution_id": grant.execution_id,
        }
        if idempotency_key:
            self._results[idempotency_key] = result
        return result

    def consumed_count(self) -> int:
        return len(self._consumed)
