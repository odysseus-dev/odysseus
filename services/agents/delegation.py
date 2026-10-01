"""Execution-scoped delegated authority. HMAC only; no JWT dependency."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import asdict, dataclass
from typing import Any, Protocol


class DelegationDenied(Exception):
    """Token failed policy or signature checks. Message is log-safe."""


class WorkloadAuthenticationRequired(Exception):
    """Reissue requires an authenticated workload, not an agent token."""


class Workload(Protocol):
    def authenticate(self) -> str:
        ...


@dataclass(frozen=True)
class DelegationClaims:
    token_id: str
    issuer: str
    audience: str
    owner: str
    execution_id: str
    conversation_id: str
    parent_token_id: str | None
    scopes: frozenset[str]
    resources: frozenset[str]
    profile_id: str
    profile_revision: int
    archetype_id: str
    archetype_version: int
    budget: int
    depth: int
    max_depth: int
    allowed_child_archetypes: frozenset[str]
    issued_at: float
    expires_at: float


@dataclass(frozen=True)
class DelegationToken:
    claims: DelegationClaims
    signature: str


def _canonical_claims(claims: DelegationClaims) -> bytes:
    payload = asdict(claims)
    payload["scopes"] = sorted(payload["scopes"])
    payload["resources"] = sorted(payload["resources"])
    payload["allowed_child_archetypes"] = sorted(payload["allowed_child_archetypes"])
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _as_frozen(values: Any) -> frozenset[str]:
    return frozenset(values or ())


class DelegationAuthority:
    def __init__(self, hmac_key: bytes | None = None) -> None:
        if hmac_key is None:
            from src.secret_storage import hmac_secret

            hmac_key = hmac_secret("delegation")
        self._key = hmac_key
        self._revoked: set[str] = set()
        self._cancelled_executions: set[str] = set()

    def _sign(self, claims: DelegationClaims) -> DelegationToken:
        signature = hmac.new(self._key, _canonical_claims(claims), hashlib.sha256).hexdigest()
        return DelegationToken(claims=claims, signature=signature)

    def issue(
        self,
        *,
        audience: str,
        execution_id: str,
        conversation_id: str,
        owner: str,
        scopes: set[str] | frozenset[str],
        resources: set[str] | frozenset[str],
        profile_id: str,
        profile_revision: int,
        archetype_id: str,
        archetype_version: int,
        budget: int,
        max_depth: int,
        ttl_seconds: float,
        allowed_child_archetypes: set[str] | frozenset[str] = frozenset(),
        issuer: str = "odysseus",
        parent_token_id: str | None = None,
        depth: int = 0,
        now: float | None = None,
    ) -> DelegationToken:
        issued_at = time.time() if now is None else now
        claims = DelegationClaims(
            token_id=secrets.token_hex(16),
            issuer=issuer,
            audience=audience,
            owner=owner,
            execution_id=execution_id,
            conversation_id=conversation_id,
            parent_token_id=parent_token_id,
            scopes=_as_frozen(scopes),
            resources=_as_frozen(resources),
            profile_id=profile_id,
            profile_revision=int(profile_revision),
            archetype_id=archetype_id,
            archetype_version=int(archetype_version),
            budget=int(budget),
            depth=int(depth),
            max_depth=int(max_depth),
            allowed_child_archetypes=_as_frozen(allowed_child_archetypes),
            issued_at=issued_at,
            expires_at=issued_at + float(ttl_seconds),
        )
        return self._sign(claims)

    def verify(
        self,
        token: DelegationToken,
        *,
        audience: str,
        execution_id: str,
        profile_revision: int | None = None,
        now: float | None = None,
    ) -> DelegationClaims:
        expected = hmac.new(self._key, _canonical_claims(token.claims), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, token.signature):
            raise DelegationDenied("invalid signature")
        claims = token.claims
        if claims.audience != audience:
            raise DelegationDenied("audience mismatch")
        if claims.execution_id != execution_id:
            raise DelegationDenied("execution mismatch")
        if profile_revision is not None and claims.profile_revision != profile_revision:
            raise DelegationDenied("profile revision mismatch")
        moment = time.time() if now is None else now
        if claims.expires_at < moment:
            raise DelegationDenied("token expired")
        if claims.token_id in self._revoked:
            raise DelegationDenied("token revoked")
        if claims.execution_id in self._cancelled_executions:
            raise DelegationDenied("execution cancelled")
        return claims

    def issue_child(
        self,
        parent: DelegationToken,
        *,
        scopes: set[str] | frozenset[str],
        resources: set[str] | frozenset[str] | None = None,
        budget: int | None = None,
        archetype_id: str | None = None,
        archetype_version: int | None = None,
        profile_id: str | None = None,
        profile_revision: int | None = None,
        ttl_seconds: float | None = None,
    ) -> DelegationToken:
        parent_claims = self.verify(
            parent,
            audience=parent.claims.audience,
            execution_id=parent.claims.execution_id,
        )
        child_scopes = _as_frozen(scopes)
        if not child_scopes <= parent_claims.scopes:
            raise DelegationDenied("child scopes exceed parent")
        child_resources = parent_claims.resources if resources is None else _as_frozen(resources)
        if not child_resources <= parent_claims.resources:
            raise DelegationDenied("child resources exceed parent")
        child_budget = parent_claims.budget if budget is None else int(budget)
        if child_budget > parent_claims.budget:
            raise DelegationDenied("child budget exceeds parent")
        child_depth = parent_claims.depth + 1
        if child_depth > parent_claims.max_depth:
            raise DelegationDenied("delegation depth exceeded")
        child_archetype = archetype_id or parent_claims.archetype_id
        if child_archetype not in parent_claims.allowed_child_archetypes:
            raise DelegationDenied("child archetype not allowed")
        remaining = parent_claims.expires_at - time.time()
        child_ttl = remaining if ttl_seconds is None else min(float(ttl_seconds), remaining)
        return self.issue(
            audience=parent_claims.audience,
            execution_id=parent_claims.execution_id,
            conversation_id=parent_claims.conversation_id,
            owner=parent_claims.owner,
            scopes=child_scopes,
            resources=child_resources,
            profile_id=profile_id or parent_claims.profile_id,
            profile_revision=parent_claims.profile_revision if profile_revision is None else profile_revision,
            archetype_id=child_archetype,
            archetype_version=parent_claims.archetype_version if archetype_version is None else archetype_version,
            budget=child_budget,
            max_depth=parent_claims.max_depth,
            ttl_seconds=child_ttl,
            allowed_child_archetypes=parent_claims.allowed_child_archetypes,
            parent_token_id=parent_claims.token_id,
            depth=child_depth,
        )

    def reissue(self, token: DelegationToken, *, workload: Workload | None) -> DelegationToken:
        if workload is None:
            raise WorkloadAuthenticationRequired("workload authentication required")
        workload.authenticate()
        claims = self.verify(
            token,
            audience=token.claims.audience,
            execution_id=token.claims.execution_id,
        )
        self.revoke(claims.token_id)
        remaining = max(claims.expires_at - time.time(), 0.0)
        return self.issue(
            audience=claims.audience,
            execution_id=claims.execution_id,
            conversation_id=claims.conversation_id,
            owner=claims.owner,
            scopes=claims.scopes,
            resources=claims.resources,
            profile_id=claims.profile_id,
            profile_revision=claims.profile_revision,
            archetype_id=claims.archetype_id,
            archetype_version=claims.archetype_version,
            budget=claims.budget,
            max_depth=claims.max_depth,
            ttl_seconds=remaining,
            allowed_child_archetypes=claims.allowed_child_archetypes,
            parent_token_id=claims.parent_token_id,
            depth=claims.depth,
        )

    def revoke(self, token_id: str) -> None:
        self._revoked.add(token_id)

    def cancel_execution(self, execution_id: str) -> None:
        self._cancelled_executions.add(execution_id)
