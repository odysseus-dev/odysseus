"""
Generic multi-step tool workflow support.

Tools that return NEEDS_USER_INPUT pause for the user (style choice, confirm,
missing field). The agent loop uses this module to:
  - detect an in-flight workflow from message history
  - pin the right tool in RAG on short follow-ups
  - auto-resume with merged args when the user replies (go / yes / anime / …)
    so the model cannot end the turn without actually calling the tool again.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from titan.image_params import infer_from_text, normalize_image_args, normalize_style

_NEEDS_USER_INPUT_RE = re.compile(r"NEEDS_USER_INPUT", re.IGNORECASE)

# Short replies that mean "proceed" across confirm steps.
USER_APPROVAL_RE = re.compile(
    r"^\s*(?:"
    r"yes|y|yeah|yep|ok|okay|sure|do it|go ahead|continue|carry on|"
    r"go|generate|proceed|approve|approved|confirmed|confirm"
    r")\s*[.!?]*\s*$",
    re.IGNORECASE,
)

# Terse replies that may answer a pending question (choice, confirm, pick-one).
EXPLICIT_CONTINUATION_RE = re.compile(
    r"^\s*(?:"
    r"yes|y|yeah|yep|ok|okay|sure|do it|go ahead|continue|carry on|"
    r"run it|launch it|start it|use that|that one|same|the same|"
    r"go|generate|proceed|approve|confirmed|confirm|"
    r"anime|realistic|photoreal|square|portrait|landscape|"
    r"low|medium|high|auto|"
    r"first|second|third|the first one|the second one|the third one|"
    r"[123]|[abc]"
    r")\s*[.!?]*\s*$",
    re.IGNORECASE,
)

_RETRY_PHRASE_RE = re.compile(
    r"\b(try again|once more|one more time|retry|again|regenerate|redo|"
    r"znovu|ještě jednou|jeste jednou|regeneruj|vygeneruj znovu)\b",
    re.IGNORECASE,
)
_CONFIRM_STEP_RE = re.compile(
    r"\bconfirm\b|\bapprov|\bcall .+ again with confirm",
    re.IGNORECASE,
)
_CHOICE_STEP_RE = re.compile(
    r"which .+ should|choose:|choose one|pick one|realistic vs| or ",
    re.IGNORECASE,
)
_MISSING_FIELD_RE = re.compile(
    r"which (\w+) should|no (\w+) given|(\w+) is missing|without (\w+)",
    re.IGNORECASE,
)

# Extra tools that often belong to the same workflow (optional companions).
PENDING_TOOL_COMPANIONS: Dict[str, frozenset] = {
    "generate_image": frozenset({"edit_image"}),
}

# Tools the model reaches for instead of resuming a paused workflow.
WORKFLOW_DISTRACTOR_TOOLS = frozenset({"web_search", "web_fetch", "pipeline"})


@dataclass(frozen=True)
class PendingToolWorkflow:
    tool_name: str
    last_args: Dict[str, Any]
    needs_input_text: str


def _message_text(msg: Dict) -> str:
    content = msg.get("content", "")
    if isinstance(content, list):
        content = " ".join(
            b.get("text", "") for b in content if isinstance(b, dict)
        )
    return str(content or "")


def _parse_tool_arguments(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        return dict(data) if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def parse_needs_user_input_bullets(text: str) -> Dict[str, str]:
    """Extract `- key: value` lines from a NEEDS_USER_INPUT tool response."""
    if not text or "needs_user_input" not in text.lower():
        return {}
    params: Dict[str, str] = {}
    for line in str(text).splitlines():
        line = line.strip()
        m = re.match(r"^-\s*([\w_]+):\s*(.+)$", line)
        if not m:
            continue
        key = m.group(1).lower()
        val = m.group(2).strip()
        if "(" in val:
            val = val.split("(", 1)[0].strip()
        if val and val.lower() != "(none)":
            params[key] = val
    return params


def _tool_name_from_assistant_message(msg: Dict) -> tuple[Optional[str], Dict[str, Any]]:
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        name = fn.get("name")
        if name:
            return name, _parse_tool_arguments(fn.get("arguments"))
    text = _message_text(msg)
    for pat in (
        r"```(\w+)\s*\n(\{.*?\})",
        r"(\w+):\s*(\{.*?\})",
    ):
        m = re.search(pat, text, re.DOTALL)
        if not m:
            continue
        try:
            args = json.loads(m.group(2))
            if isinstance(args, dict):
                return m.group(1), dict(args)
        except (json.JSONDecodeError, TypeError):
            pass
    return None, {}


def extract_tool_args_from_history(
    messages: List[Dict], tool_name: str
) -> Dict[str, Any]:
    """Best-effort recovery of the last arguments for *tool_name*."""
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            name, args = _tool_name_from_assistant_message(msg)
            if name == tool_name and args:
                return dict(args)
        text = _message_text(msg)
        if tool_name in text:
            parsed = parse_needs_user_input_bullets(text)
            if parsed:
                return parsed
            for pat in (
                rf"{re.escape(tool_name)}:\s*(\{{.*?\}})",
                rf"```{re.escape(tool_name)}\s*\n(\{{.*?\}})",
            ):
                m = re.search(pat, text, re.DOTALL)
                if not m:
                    continue
                try:
                    data = json.loads(m.group(1))
                    if isinstance(data, dict):
                        return dict(data)
                except (json.JSONDecodeError, TypeError):
                    pass
    return {}


def _message_metadata(msg: Dict) -> Dict[str, Any]:
    meta = msg.get("metadata")
    return meta if isinstance(meta, dict) else {}


def _tool_events_from_message(msg: Dict) -> List[Dict[str, Any]]:
    events = _message_metadata(msg).get("tool_events") or []
    return events if isinstance(events, list) else []


def _tool_event_completed(ev: Dict[str, Any]) -> bool:
    if ev.get("image_url"):
        return True
    out = str(ev.get("output") or "")
    if "Generated image for:" in out or "/api/generated-image/" in out:
        return True
    return False


def _parse_tool_event_args(ev: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(ev.get("args"), dict):
        return dict(ev["args"])
    try:
        from mcp_servers.gallery_provenance import build_image_args_from_tool_event

        if ev.get("tool") == "generate_image":
            return build_image_args_from_tool_event(ev)
    except Exception:
        pass
    command = str(ev.get("command") or "")
    if ":" in command:
        _, tail = command.split(":", 1)
        tail = tail.strip()
        if tail.startswith("{"):
            try:
                data = json.loads(tail)
                if isinstance(data, dict):
                    return dict(data)
            except (json.JSONDecodeError, TypeError):
                pass
    return {}


def _last_completion_index(messages: List[Dict], tool_name: str) -> int:
    for i in range(len(messages) - 1, -1, -1):
        for ev in reversed(_tool_events_from_message(messages[i])):
            if ev.get("tool") != tool_name:
                continue
            if _tool_event_completed(ev):
                return i
    return -1


def session_had_image_generation(messages: List[Dict], *, lookback: int = 48) -> bool:
    """True if generate_image completed successfully in recent session history."""
    return find_last_successful_image_args(messages, lookback=lookback) is not None


# English-only: user is asking about a past image, not requesting a new one.
_IMAGE_META_QUESTION_RE = re.compile(
    r"\b("
    r"what (were|was|are)|which parameters?|what parameters?|"
    r"how (was|were|did)|tell me (the|about) (parameters?|params?|settings?)"
    r")\b",
    re.IGNORECASE,
)


def find_last_successful_image_args(
    messages: List[Dict], *, lookback: int = 48
) -> Optional[Dict[str, Any]]:
    """Return generate_image args from the most recent successful image in history."""
    start = max(len(messages) - lookback, 0)
    for i in range(len(messages) - 1, start - 1, -1):
        for ev in reversed(_tool_events_from_message(messages[i])):
            if ev.get("tool") != "generate_image":
                continue
            if not _tool_event_completed(ev):
                continue
            try:
                from mcp_servers.gallery_provenance import build_image_args_from_tool_event

                args = build_image_args_from_tool_event(ev)
            except Exception:
                args = _parse_tool_event_args(ev)
            if not args.get("prompt") and ev.get("image_prompt"):
                args["prompt"] = ev["image_prompt"]
            if args.get("prompt"):
                if args.get("seed") is None and ev.get("seed") is not None:
                    args["seed"] = ev["seed"]
                if args.get("seed") is None and isinstance(ev.get("provenance"), dict):
                    args["seed"] = ev["provenance"].get("seed")
                return normalize_image_args(args)
    return None


def is_image_regenerate_followup(messages: List[Dict], user_text: str) -> bool:
    """Language-agnostic: user likely wants another image after a prior success."""
    if not find_last_successful_image_args(messages):
        return False
    text = str(user_text or "").strip()
    if not text or len(text) > 500:
        return False
    if USER_APPROVAL_RE.match(text) or EXPLICIT_CONTINUATION_RE.match(text):
        return True
    if infer_from_text(text):
        return True
    if _IMAGE_META_QUESTION_RE.search(text):
        return False
    # Question without an action verb → meta, not regenerate.
    if text.rstrip().endswith("?") and not re.search(
        r"\b(generate|draw|create|make|render|regenerat|retry|again)\b",
        text,
        re.IGNORECASE,
    ):
        return False
    # Short/medium reply after a successful image → treat as variation request.
    return len(text) < 280


def user_messages_since_last_image(
    messages: List[Dict], *, lookback: int = 48
) -> List[str]:
    """User turns after the most recent successful generate_image."""
    start = max(len(messages) - lookback, 0)
    last_idx = _last_completion_index(messages, "generate_image")
    from_idx = max(last_idx + 1, start) if last_idx >= 0 else start
    texts: List[str] = []
    for msg in messages[from_idx:]:
        if msg.get("role") != "user":
            continue
        text = _message_text(msg).strip()
        if text:
            texts.append(text)
    return texts


def should_auto_regenerate_image(messages: List[Dict], user_text: str) -> bool:
    """Bypass the LLM when a follow-up clearly needs another generate_image call."""
    return is_image_regenerate_followup(messages, user_text)


def find_pending_tool_workflow(
    messages: List[Dict], *, lookback: int = 24
) -> Optional[PendingToolWorkflow]:
    """Return the most recent tool workflow waiting on user input, if any."""
    start = max(len(messages) - lookback, 0)

    # In-flight API tool messages (same request / full tool transcript).
    for i in range(len(messages) - 1, start - 1, -1):
        text = _message_text(messages[i])
        if not _NEEDS_USER_INPUT_RE.search(text):
            continue

        tool_name: Optional[str] = None
        last_args: Dict[str, Any] = {}

        if messages[i].get("role") == "tool":
            tc_id = messages[i].get("tool_call_id")
            for j in range(i - 1, start - 1, -1):
                prev = messages[j]
                if prev.get("role") != "assistant":
                    continue
                for tc in prev.get("tool_calls") or []:
                    if tc_id and tc.get("id") != tc_id:
                        continue
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        tool_name = fn["name"]
                        last_args = _parse_tool_arguments(fn.get("arguments"))
                        break
                if tool_name:
                    break
        else:
            for j in range(i - 1, start - 1, -1):
                prev = messages[j]
                if prev.get("role") == "assistant":
                    tool_name, last_args = _tool_name_from_assistant_message(prev)
                    if tool_name:
                        break

        bullets = parse_needs_user_input_bullets(text)
        if bullets:
            last_args = {**last_args, **bullets}

        if tool_name:
            if tool_name == "generate_image":
                last_args = normalize_image_args(last_args, source_text=text)
            return PendingToolWorkflow(tool_name, last_args, text)

    # Persisted history: NEEDS_USER_INPUT lives in metadata.tool_events, not content.
    for i in range(len(messages) - 1, start - 1, -1):
        events = _tool_events_from_message(messages[i])
        if not events:
            continue
        pending_ev: Optional[Dict[str, Any]] = None
        for ev in reversed(events):
            tool = ev.get("tool")
            if not tool:
                continue
            if _tool_event_completed(ev):
                pending_ev = None
                break
            out = str(ev.get("output") or "")
            if _NEEDS_USER_INPUT_RE.search(out):
                pending_ev = ev
                break
        if not pending_ev:
            continue
        tool_name = str(pending_ev.get("tool") or "")
        if not tool_name:
            continue
        if i <= _last_completion_index(messages, tool_name):
            continue
        last_args = _parse_tool_event_args(pending_ev)
        bullets = parse_needs_user_input_bullets(str(pending_ev.get("output") or ""))
        if bullets:
            last_args = {**last_args, **bullets}
        if tool_name == "generate_image":
            last_args = normalize_image_args(
                last_args,
                source_text=str(pending_ev.get("output") or ""),
            )
        return PendingToolWorkflow(
            tool_name,
            last_args,
            str(pending_ev.get("output") or ""),
        )

    return None


def infer_missing_field(needs_input_text: str) -> Optional[str]:
    low = needs_input_text.lower()
    if _CONFIRM_STEP_RE.search(needs_input_text) and "which" not in low:
        return None
    m = _MISSING_FIELD_RE.search(needs_input_text)
    if m:
        for g in m.groups():
            if g:
                return g.lower()
    if "style" in low and "anime" in low and "realistic" in low:
        return "style"
    return None


def _normalize_choice(field: str, user_text: str) -> str:
    val = user_text.strip()
    if field == "style":
        return normalize_style(val) or val
    low = val.lower()
    if field == "quality" and low in ("low", "medium", "high", "auto"):
        return low
    if field == "aspect" and low in ("square", "portrait", "landscape"):
        return low
    return val


def enrich_from_user_history(
    messages: List[Dict], args: Dict[str, Any], *, tool_name: str
) -> Dict[str, Any]:
    """Apply size/quality/style hints from earlier user turns."""
    merged = dict(args)
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        text = _message_text(msg)
        if not text.strip():
            continue
        hints = infer_from_text(text)
        for key, val in hints.items():
            if val and not merged.get(key):
                merged[key] = val
        if tool_name == "generate_image" and hints:
            break
    if tool_name == "generate_image":
        return normalize_image_args(merged)
    return merged


def merge_user_into_tool_args(
    workflow: PendingToolWorkflow,
    user_text: str,
    messages: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    """Apply the user's follow-up message to the pending tool arguments."""
    args = dict(workflow.last_args)
    bullets = parse_needs_user_input_bullets(workflow.needs_input_text)
    if bullets:
        args.update(bullets)

    user = str(user_text or "").strip()
    needs = workflow.needs_input_text
    inferred = infer_from_text(user)

    if USER_APPROVAL_RE.match(user):
        if _CONFIRM_STEP_RE.search(needs) or "confirm=true" in needs.lower():
            args["confirm"] = True
        return _finalize_workflow_args(workflow, args, user, messages)

    if _CONFIRM_STEP_RE.search(needs):
        if _RETRY_PHRASE_RE.search(user) or (
            len(user) < 120 and "?" not in user
        ):
            args["confirm"] = True
            return _finalize_workflow_args(workflow, args, user, messages)

    if EXPLICIT_CONTINUATION_RE.match(user) or inferred:
        field = infer_missing_field(needs)
        if field:
            args[field] = _normalize_choice(field, user)
        elif inferred.get("style"):
            args["style"] = inferred["style"]
        for key, val in inferred.items():
            if val and not args.get(key):
                args[key] = val
        return _finalize_workflow_args(workflow, args, user, messages)

    if user and len(user) < 160 and "?" not in user:
        field = infer_missing_field(needs)
        if field:
            args[field] = _normalize_choice(field, user)
        elif inferred:
            args.update({k: v for k, v in inferred.items() if v})

    return _finalize_workflow_args(workflow, args, user, messages)


def _finalize_workflow_args(
    workflow: PendingToolWorkflow,
    args: Dict[str, Any],
    user: str,
    messages: Optional[List[Dict]],
) -> Dict[str, Any]:
    if workflow.tool_name == "generate_image":
        args = normalize_image_args(args, source_text=user)
    if messages:
        args = enrich_from_user_history(messages, args, tool_name=workflow.tool_name)
    return args


def should_auto_resume(workflow: Optional[PendingToolWorkflow], user_text: str) -> bool:
    if not workflow:
        return False
    user = str(user_text or "").strip()
    if not user:
        return False
    if USER_APPROVAL_RE.match(user):
        return True
    if EXPLICIT_CONTINUATION_RE.match(user):
        return True
    if _RETRY_PHRASE_RE.search(user):
        return True
    if infer_from_text(user):
        return True
    if _CONFIRM_STEP_RE.search(workflow.needs_input_text):
        if len(user) < 120 and "?" not in user:
            return True
    if infer_missing_field(workflow.needs_input_text) and len(user) < 160:
        return True
    return False


def is_user_approval(user_text: str) -> bool:
    return bool(USER_APPROVAL_RE.match(str(user_text or "").strip()))


def is_explicit_continuation(user_text: str) -> bool:
    return bool(EXPLICIT_CONTINUATION_RE.match(str(user_text or "").strip()))
