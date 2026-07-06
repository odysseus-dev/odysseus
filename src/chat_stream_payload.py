"""Validation helpers for /api/chat_stream request payloads."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping


_TRUE_VALUES = {"true", "1", "yes", "on"}
_FALSE_VALUES = {"false", "0", "no", "off", ""}
_TIME_FILTERS = {"day", "week", "month", "year"}


@dataclass(frozen=True)
class ChatStreamPayload:
    message: str | None
    session: str | None
    attachments: list[str]
    use_web: bool
    use_research: bool
    time_filter: str | None
    preset_id: str | None
    allow_bash: bool | None
    allow_web_search: bool | None
    use_rag: bool | None
    search_context: str | None
    compare_mode: bool
    incognito: bool
    chat_mode: str
    workspace: str | None
    approved_plan: str
    active_doc_id: str
    no_memory: bool


def parse_chat_stream_payload(form_data: Any, body: Any) -> ChatStreamPayload:
    """Normalize JSON/FormData chat-stream fields before route logic runs."""
    if body is not None and not isinstance(body, Mapping):
        raise ValueError("JSON body must be an object")

    attachments = _parse_attachments(_field(form_data, body, "attachments"))
    chat_mode = _parse_mode(_field(form_data, body, "mode", "chat"))

    return ChatStreamPayload(
        message=_optional_text(_field(form_data, body, "message")),
        session=_optional_text(_field(form_data, body, "session")),
        attachments=attachments,
        use_web=_parse_bool(_field(form_data, body, "use_web"), "use_web", default=False),
        use_research=_parse_bool(_field(form_data, body, "use_research"), "use_research", default=False),
        time_filter=_parse_time_filter(_field(form_data, body, "time_filter")),
        preset_id=_optional_text(_field(form_data, body, "preset_id")),
        allow_bash=_parse_bool(_field(form_data, body, "allow_bash"), "allow_bash", default=None),
        allow_web_search=_parse_bool(_field(form_data, body, "allow_web_search"), "allow_web_search", default=None),
        use_rag=_parse_bool(_field(form_data, body, "use_rag"), "use_rag", default=None),
        search_context=_optional_text(_field(form_data, body, "search_context")),
        compare_mode=_parse_bool(_field(form_data, body, "compare_mode"), "compare_mode", default=False),
        incognito=_parse_bool(_field(form_data, body, "incognito"), "incognito", default=False),
        chat_mode=chat_mode,
        workspace=_optional_text(_field(form_data, body, "workspace")),
        approved_plan=(_optional_text(_field(form_data, body, "approved_plan")) or "")[:8192],
        active_doc_id=_optional_text(_field(form_data, body, "active_doc_id")) or "",
        no_memory=_parse_bool(_field(form_data, body, "no_memory"), "no_memory", default=False),
    )


def _field(form_data: Any, body: Mapping[str, Any] | None, key: str, default: Any = None) -> Any:
    value = form_data.get(key) if form_data is not None else None
    if value is not None:
        return value
    if body is not None:
        return body.get(key, default)
    return default


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("expected text field")
    return value.strip()


def _parse_bool(value: Any, field_name: str, default: bool | None) -> bool | None:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUE_VALUES:
            return True
        if normalized in _FALSE_VALUES:
            return False
    raise ValueError(f"{field_name} must be a boolean")


def _parse_mode(value: Any) -> str:
    if value is None:
        return "chat"
    if not isinstance(value, str):
        raise ValueError("mode must be chat or agent")
    mode = value.strip().lower() or "chat"
    if mode not in {"chat", "agent"}:
        raise ValueError("mode must be chat or agent")
    return mode


def _parse_time_filter(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("time_filter must be text")
    value = value.strip().lower()
    if not value or value not in _TIME_FILTERS:
        return None
    return value


def _parse_attachments(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    raw = value
    if isinstance(value, str):
        try:
            raw = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("attachments must be a JSON array") from exc
    if not isinstance(raw, list):
        raise ValueError("attachments must be a list")

    attachments: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError("attachment ids must be strings")
        attachment_id = item.strip()
        if not attachment_id:
            raise ValueError("attachment ids must not be empty")
        attachments.append(attachment_id)
    return attachments
