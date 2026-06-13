"""OpenClaw inbox triage routes.

Read Slack-friendly urgent-email state from the existing Odysseus urgency
scanner and store lightweight action state for Slack commands.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.constants import DATA_DIR

BASE_URL = "/api/openclaw/inbox"
EMAIL_READ_SCOPES = {"email:read"}


def _scope_owner(request: Request, allowed: set[str]) -> str:
    if getattr(request.state, "api_token", False):
        scopes = set(getattr(request.state, "api_token_scopes", []) or [])
        if not scopes.intersection(allowed):
            required = " or ".join(sorted(allowed))
            raise HTTPException(403, f"API token missing required scope: {required}")
        owner = getattr(request.state, "api_token_owner", None)
        if not owner:
            raise HTTPException(403, "API token has no owner")
        return owner
    from src.auth_helpers import require_user
    return require_user(request)


def _owner_slug(owner: str | None) -> str:
    return "".join(c if (c.isalnum() or c in "-_.@") else "_" for c in (owner or "default"))


def _state_path(owner: str | None) -> Path:
    return Path(DATA_DIR) / f"email_urgency_state_{_owner_slug(owner)}.json"


def _actions_path(owner: str | None) -> Path:
    return Path(DATA_DIR) / f"openclaw_inbox_actions_{_owner_slug(owner)}.json"


def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(500, f"Failed to read inbox triage state: {exc}") from exc
    if not isinstance(data, dict):
        raise HTTPException(500, "Inbox triage state is malformed")
    return data


def _save_actions(owner: str | None, actions: dict[str, Any]) -> None:
    path = _actions_path(owner)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(actions, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _encode_key(key: str) -> str:
    return base64.urlsafe_b64encode(key.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_key(item_id: str) -> str:
    try:
        padding = "=" * (-len(item_id) % 4)
        return base64.urlsafe_b64decode((item_id + padding).encode("ascii")).decode("utf-8")
    except Exception as exc:
        raise HTTPException(400, "Invalid inbox item id") from exc


def _normalize_sender(sender: str | None) -> str:
    return (sender or "").strip().lower()


def _action_state(owner: str | None) -> dict[str, Any]:
    actions = _load_json(_actions_path(owner), {"acked": {}, "muted_senders": {}})
    actions.setdefault("acked", {})
    actions.setdefault("muted_senders", {})
    return actions


def _triage_state(owner: str | None) -> dict[str, Any]:
    return _load_json(_state_path(owner), {"total_unread": 0, "total_urgent": 0, "max_score": 0, "per_uid": {}})


def _items(owner: str | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    state = _triage_state(owner)
    actions = _action_state(owner)
    now = time.time()
    acked = actions.get("acked", {})
    muted = actions.get("muted_senders", {})
    items = []
    for key, verdict in (state.get("per_uid") or {}).items():
        if not isinstance(verdict, dict):
            continue
        score = int(verdict.get("score") or 0)
        if score < 2:
            continue
        sender = verdict.get("from") or ""
        sender_key = _normalize_sender(sender)
        muted_until = float((muted.get(sender_key) or {}).get("until_ts") or 0)
        item = {
            "id": _encode_key(str(key)),
            "score": score,
            "tier": "urgent" if score >= 3 else "reply-soon",
            "subject": (verdict.get("subject") or "(no subject)")[:240],
            "from": sender[:160],
            "reason": (verdict.get("reason") or "")[:300],
            "tags": verdict.get("tags") or [],
            "acked": str(key) in acked,
            "muted": bool(sender_key and muted_until > now),
            "muted_until": muted_until if sender_key and muted_until > now else None,
            "actions": [
                "ack",
                "mute_sender_2h",
                "summarize_thread",
                "create_redmine_ticket",
            ],
        }
        items.append(item)
    items.sort(key=lambda item: (-item["score"], item["subject"].lower()))
    return state, items


class MuteRequest(BaseModel):
    hours: float = Field(default=2, ge=0.25, le=24)
    reason: str | None = Field(default=None, max_length=300)


def _find_item(owner: str | None, item_id: str) -> dict[str, Any]:
    _decode_key(item_id)
    _state, items = _items(owner)
    item = next((candidate for candidate in items if candidate["id"] == item_id), None)
    if not item:
        raise HTTPException(404, "Inbox item not found")
    return item


def _item_summary(item: dict[str, Any]) -> str:
    parts = [
        f"{item.get('tier', 'inbox').replace('-', ' ').title()} email from {item.get('from') or 'unknown sender'}.",
        f"Subject: {item.get('subject') or '(no subject)'}.",
    ]
    reason = item.get("reason")
    if reason:
        parts.append(f"Why it was flagged: {reason}.")
    tags = [str(tag) for tag in (item.get("tags") or []) if str(tag).strip()]
    if tags:
        parts.append(f"Tags: {', '.join(tags[:8])}.")
    parts.append("Available Slack actions: ack, mute sender, draft Redmine ticket.")
    return " ".join(parts)


def setup_openclaw_inbox_routes() -> APIRouter:
    router = APIRouter(prefix=BASE_URL, tags=["openclaw-inbox"])

    @router.get("/triage")
    async def inbox_triage(request: Request, include_acknowledged: bool = False, include_muted: bool = False):
        owner = _scope_owner(request, EMAIL_READ_SCOPES)
        state, items = _items(owner)
        visible = [
            item for item in items
            if (include_acknowledged or not item["acked"]) and (include_muted or not item["muted"])
        ]
        urgent = sum(1 for item in visible if item["score"] >= 3)
        reply_soon = sum(1 for item in visible if item["score"] == 2)
        if not visible:
            message = "No urgent inbox items need action."
        else:
            message = f"{urgent} urgent and {reply_soon} reply-soon email(s) need action."
        return {
            "status": "ok",
            "message": message,
            "total_unread": int(state.get("total_unread") or 0),
            "total_urgent": int(state.get("total_urgent") or 0),
            "max_score": int(state.get("max_score") or 0),
            "items": visible[:20],
            "links": {"self": f"{BASE_URL}/triage"},
            "requires_approval": False,
        }

    @router.post("/triage/{item_id}/ack")
    async def ack_inbox_item(request: Request, item_id: str):
        owner = _scope_owner(request, EMAIL_READ_SCOPES)
        key = _decode_key(item_id)
        _find_item(owner, item_id)
        actions = _action_state(owner)
        actions.setdefault("acked", {})[key] = {
            "at": time.time(),
            "by": owner,
        }
        _save_actions(owner, actions)
        return {
            "status": "ok",
            "message": "Inbox item acknowledged.",
            "item_id": item_id,
            "requires_approval": False,
        }

    @router.post("/triage/{item_id}/mute-sender")
    async def mute_inbox_sender(request: Request, item_id: str, body: MuteRequest | None = None):
        owner = _scope_owner(request, EMAIL_READ_SCOPES)
        body = body or MuteRequest()
        item = _find_item(owner, item_id)
        sender_key = _normalize_sender(item.get("from"))
        if not sender_key:
            raise HTTPException(400, "Inbox item has no sender to mute")
        until_ts = time.time() + (body.hours * 3600)
        actions = _action_state(owner)
        actions.setdefault("muted_senders", {})[sender_key] = {
            "sender": item.get("from") or "",
            "until_ts": until_ts,
            "hours": body.hours,
            "reason": body.reason or "",
            "by": owner,
        }
        _save_actions(owner, actions)
        return {
            "status": "ok",
            "message": f"Muted {item.get('from')} for {body.hours:g}h.",
            "sender": item.get("from"),
            "muted_until": until_ts,
            "requires_approval": False,
        }

    @router.get("/triage/{item_id}/summary")
    async def summarize_inbox_item(request: Request, item_id: str):
        owner = _scope_owner(request, EMAIL_READ_SCOPES)
        item = _find_item(owner, item_id)
        summary = _item_summary(item)
        return {
            "status": "ok",
            "message": summary,
            "summary": summary,
            "item": item,
            "requires_approval": False,
        }

    @router.post("/triage/{item_id}/redmine-ticket/draft")
    async def draft_redmine_ticket(request: Request, item_id: str):
        owner = _scope_owner(request, EMAIL_READ_SCOPES)
        item = _find_item(owner, item_id)
        subject = (item.get("subject") or "(no subject)")[:180]
        tags = [str(tag) for tag in (item.get("tags") or []) if str(tag).strip()]
        description = "\n".join([
            "Drafted from OpenClaw inbox triage.",
            "",
            f"From: {item.get('from') or 'unknown'}",
            f"Subject: {subject}",
            f"Priority signal: {item.get('tier')} (score {item.get('score')})",
            f"Reason: {item.get('reason') or 'not provided'}",
            f"Tags: {', '.join(tags)}",
            "",
            "This is a draft only. Review before submitting to Converge/Redmine.",
        ])[:4000]
        return {
            "status": "ok",
            "message": "Redmine ticket draft ready. Approval is required before submission.",
            "draft": {
                "subject": subject,
                "description": description,
                "source": "openclaw_inbox_triage",
                "source_item_id": item_id,
                "requested_by": owner,
            },
            "requires_approval": True,
            "links": {"self": f"{BASE_URL}/triage/{item_id}/redmine-ticket/draft"},
        }

    return router
