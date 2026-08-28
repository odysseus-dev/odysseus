"""Opaque exact-action approvals with explicit task and chat scopes.

The server still seals and claims the first displayed action exactly once. The
selected scope then bypasses only the automatic post-external-context approval
gate for the rest of the resumed task or chat session. Browser-visible fields
are display copies, never authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from src.tool_approval_scopes import (
    CHAT_SESSION_APPROVAL_DECISION,
    DENY_APPROVAL_DECISION,
    TASK_APPROVAL_DECISION,
    ToolApprovalScope,
    scope_for_decision,
)
from src.tool_capabilities import ToolCapabilities, capabilities_for_action


DEFAULT_APPROVAL_TTL_SECONDS = 10 * 60
DEFAULT_MAX_PENDING_APPROVALS = 2048


def _normalized_owner(owner: Any) -> str:
    return str(owner or "").strip().casefold()


def _normalized_workspace(workspace: Any) -> str:
    if not isinstance(workspace, str) or not workspace.strip():
        return ""
    return os.path.realpath(os.path.expanduser(workspace))


_MAX_APPROVAL_SELECTED_TOOLS = 512
_MAX_APPROVAL_TOOL_NAME_CHARS = 512
_MAX_APPROVAL_CONTINUATION_QUERY_CHARS = 4000


def _normalized_selected_tools(
    selected_tools: Any,
    *,
    required_tool: Any = None,
) -> tuple[str, ...]:
    if isinstance(selected_tools, str):
        selected_tools = (selected_tools,)
    try:
        values = selected_tools or ()
        names = {
            name.strip()
            for name in values
            if (
                isinstance(name, str)
                and name.strip()
                and len(name.strip()) <= _MAX_APPROVAL_TOOL_NAME_CHARS
            )
        }
        required_name = str(required_tool or "").strip()
        if required_name and len(required_name) <= _MAX_APPROVAL_TOOL_NAME_CHARS:
            names.add(required_name)
        ordered = sorted(names)
        if len(ordered) <= _MAX_APPROVAL_SELECTED_TOOLS:
            return tuple(ordered)
        kept = ordered[:_MAX_APPROVAL_SELECTED_TOOLS]
        if required_name and required_name in names and required_name not in kept:
            kept[-1] = required_name
            kept.sort()
        return tuple(kept)
    except TypeError:
        return ()


def _normalized_continuation_query(value: Any) -> str:
    # The query is server-derived from the interrupted run and already lives in
    # session history. Keep the pending copy bounded because approvals are held
    # in memory until consumed or expired.
    return str(value or "").strip()[:_MAX_APPROVAL_CONTINUATION_QUERY_CHARS]


def _canonical_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def document_content_digest(content: Any) -> str:
    """Return the stable server-side fingerprint used to seal a document."""
    return hashlib.sha256(str(content or "").encode("utf-8")).hexdigest()


def _binding_payload(
    *,
    owner: Any,
    session_id: Any,
    origin_run_id: Any,
    tool_name: Any,
    content: Any,
    workspace: Any,
    document_id: Any,
    document_version: Any,
    document_digest: Any,
    external_untrusted_context_seen: bool,
    selected_tools: Any,
    continuation_query: Any,
    effects: tuple[str, ...],
    result_integrity: str,
) -> dict[str, Any]:
    return {
        "owner": _normalized_owner(owner),
        "session_id": str(session_id or ""),
        "origin_run_id": str(origin_run_id or ""),
        "tool_name": str(tool_name or ""),
        "content": str(content or ""),
        "workspace": _normalized_workspace(workspace),
        "document_id": str(document_id or ""),
        "document_version": (
            int(document_version) if document_version is not None else None
        ),
        "document_digest": str(document_digest or "").strip().lower(),
        "external_untrusted_context_seen": bool(external_untrusted_context_seen),
        "selected_tools": list(
            _normalized_selected_tools(selected_tools, required_tool=tool_name)
        ),
        "continuation_query": _normalized_continuation_query(continuation_query),
        "effects": list(effects),
        "result_integrity": str(result_integrity),
    }


@dataclass(frozen=True)
class PendingToolApproval:
    approval_id: str
    owner: str
    session_id: str
    origin_run_id: str
    tool_name: str
    content: str
    workspace: str
    document_id: str
    document_version: int | None
    document_digest: str
    external_untrusted_context_seen: bool
    effects: tuple[str, ...]
    result_integrity: str
    digest: str
    created_at: float
    expires_at: float
    # Server-only continuation state. Both fields are digest-bound and never
    # exposed in the browser payload.
    selected_tools: tuple[str, ...] = ()
    continuation_query: str = ""

    def public_payload(self, *, reason: str | None = None) -> dict[str, Any]:
        return {
            "kind": "tool_approval",
            "approval_id": self.approval_id,
            # The browser already owns this chat id. Persisting it with the
            # resolved card lets history-derived session grants remain bound to
            # this exact chat and prevents inheritance by a forked session.
            "session_id": self.session_id,
            "question": "Allow this task to continue?",
            "description": reason or (
                "Untrusted context influenced this run, so continuing with "
                "otherwise-gated actions needs your explicit approval."
            ),
            "options": [
                {
                    "label": "Allow for this task",
                    "value": TASK_APPROVAL_DECISION,
                    "description": (
                        "Execute the sealed action and allow every otherwise-gated "
                        "action needed to finish this request. Current tool, account, "
                        "workspace, and sandbox restrictions still apply."
                    ),
                },
                {
                    "label": "Allow for this chat session",
                    "value": CHAT_SESSION_APPROVAL_DECISION,
                    "description": (
                        "Execute the sealed action and stop asking at this gate for "
                        "later requests in this chat. Current tool, account, workspace, "
                        "and sandbox restrictions still apply."
                    ),
                },
                {
                    "label": "Deny",
                    "value": DENY_APPROVAL_DECISION,
                    "description": "Do not execute the proposed action.",
                },
            ],
            "action": {
                "tool": self.tool_name,
                # Show the complete sealed input so approval never hides
                # trailing lines.  This is not read back as authority.
                "content": self.content,
                "digest": self.digest[:16],
                "effects": list(self.effects),
                "workspace": self.workspace or None,
                "document_id": self.document_id or None,
                "document_version": self.document_version,
            },
        }


@dataclass
class ExactToolApproval:
    """A consumed exact first action plus an explicit continuation scope."""

    pending: PendingToolApproval
    scope: ToolApprovalScope = ToolApprovalScope.TASK
    # The seam consumed by agent_loop. Both chat-card allow choices cover the
    # complete resumed task, because one-action scope there immediately
    # re-entered the same gate on the next round. Callers with no resumable
    # chat still get SINGLE_ACTION, which leaves the gate armed behind the
    # sealed action.
    allow_remaining_actions: bool = True
    _claimed: bool = field(default=False, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @property
    def grants_chat_session(self) -> bool:
        return self.scope is ToolApprovalScope.CHAT_SESSION

    def _matches_unlocked(
        self,
        *,
        owner: Any,
        session_id: Any,
        tool_name: Any,
        content: Any,
        workspace: Any,
    ) -> bool:
        if self._claimed:
            return False
        capabilities = capabilities_for_action(tool_name, content)
        effects = tuple(sorted(effect.value for effect in capabilities.effects))
        result_integrity = capabilities.result_integrity.value
        if (
            effects != self.pending.effects
            or result_integrity != self.pending.result_integrity
        ):
            return False
        expected = _binding_payload(
            owner=owner,
            session_id=session_id,
            origin_run_id=self.pending.origin_run_id,
            tool_name=tool_name,
            content=content,
            workspace=workspace,
            document_id=self.pending.document_id,
            document_version=self.pending.document_version,
            document_digest=self.pending.document_digest,
            external_untrusted_context_seen=(
                self.pending.external_untrusted_context_seen
            ),
            selected_tools=self.pending.selected_tools,
            continuation_query=self.pending.continuation_query,
            effects=effects,
            result_integrity=result_integrity,
        )
        return _canonical_digest(expected) == self.pending.digest

    def matches(
        self,
        *,
        owner: Any,
        session_id: Any,
        tool_name: Any,
        content: Any,
        workspace: Any,
    ) -> bool:
        with self._lock:
            return self._matches_unlocked(
                owner=owner,
                session_id=session_id,
                tool_name=tool_name,
                content=content,
                workspace=workspace,
            )

    def claim(
        self,
        *,
        owner: Any,
        session_id: Any,
        tool_name: Any,
        content: Any,
        workspace: Any,
    ) -> bool:
        with self._lock:
            if not self._matches_unlocked(
                owner=owner,
                session_id=session_id,
                tool_name=tool_name,
                content=content,
                workspace=workspace,
            ):
                return False
            self._claimed = True
            return True


class ToolApprovalStore:
    """Thread-safe pending approval registry with destructive consumption."""

    def __init__(
        self,
        *,
        ttl_seconds: int = DEFAULT_APPROVAL_TTL_SECONDS,
        max_pending: int = DEFAULT_MAX_PENDING_APPROVALS,
    ):
        self._ttl_seconds = max(1, int(ttl_seconds))
        self._max_pending = max(1, int(max_pending))
        self._pending: dict[str, PendingToolApproval] = {}
        self._lock = threading.Lock()

    def _purge_expired_locked(self, now: float) -> None:
        expired = [
            approval_id
            for approval_id, pending in self._pending.items()
            if pending.expires_at <= now
        ]
        for approval_id in expired:
            self._pending.pop(approval_id, None)

    def create(
        self,
        *,
        owner: Any,
        session_id: Any,
        origin_run_id: Any,
        tool_name: Any,
        content: Any,
        workspace: Any,
        document_id: Any = None,
        document_version: Any = None,
        document_digest: Any = None,
        selected_tools: Any = None,
        continuation_query: Any = None,
        external_untrusted_context_seen: bool,
        capabilities: ToolCapabilities,
    ) -> PendingToolApproval:
        now = time.time()
        effects = tuple(sorted(effect.value for effect in capabilities.effects))
        result_integrity = capabilities.result_integrity.value
        payload = _binding_payload(
            owner=owner,
            session_id=session_id,
            origin_run_id=origin_run_id,
            tool_name=tool_name,
            content=content,
            workspace=workspace,
            document_id=document_id,
            document_version=document_version,
            document_digest=document_digest,
            external_untrusted_context_seen=external_untrusted_context_seen,
            selected_tools=selected_tools,
            continuation_query=continuation_query,
            effects=effects,
            result_integrity=result_integrity,
        )
        pending = PendingToolApproval(
            approval_id=secrets.token_urlsafe(32),
            owner=payload["owner"],
            session_id=payload["session_id"],
            origin_run_id=payload["origin_run_id"],
            tool_name=payload["tool_name"],
            content=payload["content"],
            workspace=payload["workspace"],
            document_id=payload["document_id"],
            document_version=payload["document_version"],
            document_digest=payload["document_digest"],
            external_untrusted_context_seen=payload[
                "external_untrusted_context_seen"
            ],
            effects=effects,
            result_integrity=result_integrity,
            digest=_canonical_digest(payload),
            created_at=now,
            expires_at=now + self._ttl_seconds,
            selected_tools=tuple(payload["selected_tools"]),
            continuation_query=payload["continuation_query"],
        )
        with self._lock:
            self._purge_expired_locked(now)
            # The chat UI exposes one pending card per session, so supersede an
            # older action there. Headless/manual-test callers use an empty
            # session id; keep independent origin runs separate so two skill
            # tests owned by the same user cannot invalidate each other.
            superseded = [
                approval_id
                for approval_id, existing in self._pending.items()
                if (
                    existing.owner == pending.owner
                    and existing.session_id == pending.session_id
                    and (
                        bool(pending.session_id)
                        or existing.origin_run_id == pending.origin_run_id
                    )
                )
            ]
            for approval_id in superseded:
                self._pending.pop(approval_id, None)
            while len(self._pending) >= self._max_pending:
                oldest_id = min(
                    self._pending,
                    key=lambda approval_id: self._pending[approval_id].created_at,
                )
                self._pending.pop(oldest_id, None)
            self._pending[pending.approval_id] = pending
        return pending

    def consume(
        self,
        approval_id: Any,
        *,
        decision: Any,
        owner: Any,
        session_id: Any,
        allow_continuation: bool = True,
    ) -> ExactToolApproval | None:
        """Consume a pending approval.

        ``allow_continuation`` is the caller's assertion that it owns a
        resumable conversation the granted scope can apply to. Callers without
        one (the skill tester, unattended audits) pass ``False`` and get the
        original one-use grant, so a button labelled "Allow once" cannot widen
        into a run-long bypass just because the chat card reuses the same wire
        value.
        """
        now = time.time()
        with self._lock:
            self._purge_expired_locked(now)
            approval_key = str(approval_id or "")
            pending = self._pending.get(approval_key)
            if pending is None:
                return None
            if (
                pending.owner != _normalized_owner(owner)
                or pending.session_id != str(session_id or "")
            ):
                # Authentication is checked before destructive consumption so
                # a leaked/guessed opaque id cannot be used to invalidate
                # another owner's pending action.
                return None
            self._pending.pop(approval_key, None)
        normalized_decision = str(decision or "").strip().lower()
        scope = scope_for_decision(normalized_decision)
        if scope is None:
            return None
        if not allow_continuation:
            return ExactToolApproval(
                pending,
                scope=ToolApprovalScope.SINGLE_ACTION,
                allow_remaining_actions=False,
            )
        return ExactToolApproval(
            pending,
            scope=scope,
            allow_remaining_actions=True,
        )

    def peek(self, approval_id: Any) -> PendingToolApproval | None:
        now = time.time()
        with self._lock:
            self._purge_expired_locked(now)
            return self._pending.get(str(approval_id or ""))

    def retire_for_session(self, *, owner: Any, session_id: Any) -> bool:
        """Discard pending actions superseded by an ordinary user turn.

        Returns whether any retired action carried external provenance, so the
        caller can preserve that security state without treating the new user
        message as an approval continuation.
        """
        now = time.time()
        normalized_owner = _normalized_owner(owner)
        normalized_session = str(session_id or "")
        if not normalized_session:
            return False
        with self._lock:
            self._purge_expired_locked(now)
            retired_ids = [
                approval_id
                for approval_id, pending in self._pending.items()
                if (
                    pending.owner == normalized_owner
                    and pending.session_id == normalized_session
                )
            ]
            carried_taint = any(
                self._pending[approval_id].external_untrusted_context_seen
                for approval_id in retired_ids
            )
            for approval_id in retired_ids:
                self._pending.pop(approval_id, None)
        return carried_taint


tool_approval_store = ToolApprovalStore()
