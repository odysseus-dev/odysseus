#!/usr/bin/env python3
"""Generate, replay, and judge realistic Odysseus Agent conversations.

This runner is intentionally conversation-level.  It uses an external teacher
to vary human wording around stable capability seeds, replays each flow through
the real /api/chat_stream route as the synthetic SFT user, then asks the teacher
to classify any failure.  Raw run artifacts stay in tmp; the durable Markdown
ledger contains only concise, reproducible findings.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import fcntl
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.manage_sft_fixture_state import restore_owner, snapshot_owner

DEFAULT_DATA = Path(os.environ.get("ODYSSEUS_DATA_DIR", str(Path(__file__).resolve().parents[1] / "data")))
DEFAULT_LEDGER = ROOT / "docs" / "ODYSSEUS_HARNESS_QA.md"
DEFAULT_OUT = ROOT / "tmp" / "odysseus-conversation-qa"
DEFAULT_COVERAGE_CORPUS = DEFAULT_OUT / "sft-alex-cooked.jsonl"
DEFAULT_TARGET_ENDPOINT = "1d1022ef"
DEFAULT_TARGET_MODEL = "odysseus-qwen3.5-tools-pre-heretic"
DEFAULT_ROUTING_EXPERIMENT = "recent_model_choice"
DEFAULT_JUDGE_ENDPOINT = "f3904562"
DEFAULT_JUDGE_MODEL = "deepseek/deepseek-v4.1-flash"
DEFAULT_FALLBACK_JUDGE_MODEL = "moonshotai/kimi-k3"
DEFAULT_FIXTURE_DB = DEFAULT_DATA / "app.db"

# These families mutate only owner-scoped stores covered by
# manage_sft_fixture_state. External email state, browser sessions, workspace
# files, research jobs, model processes/downloads, and chat sessions are
# deliberately outside this allowlist.
LOCALLY_RESTORABLE_MUTATION_FAMILIES = frozenset({
    "calendar", "documents", "memory", "notes", "skills", "tasks",
})

# Generated expectations that assume a private store the opening turn never
# identifies. Quarantine them instead of teaching phrase-specific routing.
AMBIGUOUS_GENERATED_SEEDS = frozenset({
    "56b28e74-c890-46a9-a7f5-e3b96b0c8032:2",  # “sprint-notes” assumed document
    "f45bdd38-3002-47a3-add2-94239a6d9c3b:3",  # “anything saved” assumed research
    # “Friday” crosses the UTC/Tokyo date boundary; its judge also incorrectly
    # identifies 2026-09-12 as Friday. There is no stable correction to teach.
    "f62677e6-9bd5-4528-9112-d45e37c9fa6a:2",
    # A one-off personal reminder belongs to a note due date by product
    # contract; this generated expectation incorrectly requires manage_tasks.
    "8ec45076-dfe3-4751-9954-0cc2fe4a7b3a:1",
    # Product wording is explicit: show/list displays personal data in chat;
    # opening a panel requires open/panel/sidebar wording. These generated
    # expectations incorrectly reinterpret ordinary data reads as navigation.
    "578ac44b-cc29-41a0-b078-180c39e03aa2:2",
    "4ca05f55-f799-47a9-ba26-f8ae951c00ed:2",
    "7cbe36da-417d-4758-ad65-0ec588282d24:2",
    "1f0ae2fb-fa00-4bbe-ab86-e9b140db904d:2",
    "afd023f9-4c1b-45ca-bda0-e1512e0a4e38:1",
    # ui_control can open theme settings and set a named theme, but it has no
    # operation that enumerates a theme catalog. Do not teach invented names.
    "dd02b8eb-c0c2-46c2-8b95-43f79934073a:1",
    # Memory entries carry an internal timestamp, but manage_memory exposes no
    # read/view action and omits timestamps from both list and search results.
    # A model therefore cannot inspect even an approximate save date through
    # the public tool contract; treating that as an SFT miss would teach a
    # fabricated capability.
    "f313b13a-efe9-455f-9d5e-216a4f6d27a5:1",
    # No prior family is named, so “what do I have coming up?” could mean
    # calendar events, tasks, reminders, or inbox obligations.
    "a2830b66-79e1-4bf9-990d-5b35502f1198:1",
    # The corpus row assumes an active editor document, but the standalone
    # replay carries no active-document identifier or content to bind.
    "68e92e46-5274-48cf-8eb0-846f3036aaf2:1",
    # “Just the headlines” requests concise titles but no numeric cap. The
    # generated expectation invents “at most three” on turn one.
    "4522013c-db9e-498c-aeb6-d6fd3dcb9a9a:1",
    # Product contract assigns one-off personal reminders to notes.due_date;
    # this row incorrectly requires a calendar event and then calendar listing.
    "16d6308c-9786-4561-bd88-e25a721ad262:14",
})

_MUTATING_ACTION_RE = re.compile(
    r"\baction\s*(?:=|is|:)\s*['\"]?"
    r"(?:add|create|delete|edit|update|toggle_item|publish|send|draft|reply|"
    r"archive|unarchive|mark_read|mark_unread|mark_done|mark_undone|junk|"
    r"block|unblock|unsubscribe|pause|resume|run|enable|disable|set)\b",
    re.I,
)
_MUTATING_REQUEST_RE = re.compile(
    r"(?:^|\n\s*|[.!?,;—-]\s*|\b(?:and|also|please|pls|help\s+me|can (?:you|u)|could (?:you|u)|would (?:you|u)|now|then|ok|okay)\s+)"
    r"(?:so\s+)?(?:note\s+down|add|create|make|delete|edit|update|change|rename|remove|write|draft|send|"
    r"reply|archive|unarchive|mark|move|put|drop|block|unblock|unsubscribe|toggle|"
    r"enable|disable|pause|resume|launch|start|spin\s+up|serve|stop|kill|terminate|download|grab|"
    r"schedule|remind|cancel|save|pin|unpin|upscale|stic+k|clear|jot|ping|shift|"
    r"tick(?:\s+off)?|check\s+off|get\s+rid\s+of|"
    r"set\s+up|expand|shorten|revise|polish|stash|append|tack|swap|switch|"
    r"replace|turn|scrap|take\b[^.!?\n]{0,80}\boff)\b",
    re.I,
)
_MUTATING_CONTEXT_ACTION_RE = re.compile(
    r"\b(?:in|inside|under)\s+(?:an?\s+|the\s+)?[^.!?\n]{0,80}?\s+"
    r"(?:add|create|make|write|save|delete|remove|edit|update)\b",
    re.I,
)
_MUTATING_EXPECTATION_RE = re.compile(
    r"\b(?:create_document|edit_document|update_document|suggest_document|"
    r"download_model|cancel_download|serve_model|serve_preset|stop_served_model|"
    r"send_email|reply_to_email|draft_email|draft_email_reply)\b"
    r"|\bmanage_(?:notes|calendar|tasks|memory|skills|documents|session)\b"
    r"[^\n]{0,100}\b(?:add|create|delete|edit|update)(?:_event|_item)?\b"
    r"|\bmanage_(?:notes|calendar|tasks|memory|skills|documents|session)\b"
    r"[^\n]{0,100}\b(?:publish|toggle|enable|"
    r"disable|set|save|write|remove|pause|resume|run)\b",
    re.I,
)
_MUTATING_EXPECTATION_ACTION_FIRST_RE = re.compile(
    r"\b(?:add|create|delete|edit|update|publish|toggle|enable|disable|set|"
    r"save|write|remove|expand|revise|shorten)\b"
    r"[^\n]{0,140}\b(?:manage_(?:notes|calendar|tasks|memory|skills|documents|session)|"
    r"create_document|edit_document|update_document|suggest_document|download_model|"
    r"serve_model|serve_preset|stop_served_model)\b",
    re.I,
)
_MUTATING_EXPECTATION_FAMILY_TOOL_RE = re.compile(
    r"\b(?:notes?|calendar|tasks?|memory|skills?|documents?|sessions?)\s+tool"
    r"(?:\s+family)?\s+(?:(?:with|using)\s+(?:an?\s+)?|to\s+)?"
    r"(?:add|create|delete|edit|update|publish|send|draft|reply|archive|"
    r"mark|toggle|enable|disable|set|save|write|remove)\b",
    re.I,
)
_MUTATING_EXPECTATION_NATURAL_RE = re.compile(
    r"\b(?:add|create|delete|edit|update|remove|unblock|block|draft|write|"
    r"reschedule|shift|move|toggle)\b[^\n]{0,140}\b"
    r"(?:calendar\s+events?|events?|notes?|documents?|drafts?|tasks?|"
    r"skills?|memories|senders?|email)\b",
    re.I,
)
_NEGATED_MUTATION_CLAUSE_RE = re.compile(
    r"\b(?:(?:do|does|did|should|must|will|would)\s+not|"
    r"don['’]?t|dont|never|without)\b"
    r"[^.!?;\n]*",
    re.I,
)
_NO_MUTATION_NOUN_RE = re.compile(
    r"\b(?:(?:make|with)\s+)?no\s+"
    r"(?:changes?|edits?|updates?|writes?|sends?|deletions?|mutations?)\b",
    re.I,
)
_NO_MUTATION_VERB_LIST_RE = re.compile(
    r"\bno\s+(?:add|create|delete|edit|update|change|modify|send|write)"
    r"(?:\s*[/,]\s*|\s+(?:or|and)\s+)?"
    r"(?:(?:add|create|delete|edit|update|change|modify|send|write)"
    r"(?:\s*[/,]\s*|\s+(?:or|and)\s+)?)*",
    re.I,
)
_REFERENTIAL_REMIND_RE = re.compile(
    r"\bremind\s+me\s*[-—,:]\s*(?:what|when|where|which|who|how)\b",
    re.I,
)
_SFT_WEBUI_POLICY_DISABLED_TOOLS = frozenset({
    "python", "read_file", "write_file", "edit_file", "apply_patch",
})


FAMILY_SEEDS: dict[str, dict[str, Any]] = {
    "calendar": {"tools": ["manage_calendar"], "seeds": [
        ["What is on my calendar this week?", "Open the first event.", "Back to my calendar—what is next after that?"],
    ]},
    "notes": {"tools": ["manage_notes"], "seeds": [
        ["Show my notes.", "Which one mentions Japan?", "Open it."],
    ]},
    "email": {"tools": ["list_email_accounts", "list_emails", "read_email", "ui_control"], "seeds": [
        ["What is my email account?", "Show the latest inbox message.", "Draft a reply to this, but do not send it."],
    ]},
    "memory": {"tools": ["manage_memory"], "seeds": [
        ["Search my memories for timezone.", "What else is related to that?"],
    ]},
    "documents": {"tools": ["manage_documents", "ui_control"], "seeds": [
        ["List my documents.", "Open the first one.", "Summarize it."],
    ]},
    "tasks": {"tools": ["manage_tasks"], "seeds": [
        ["List my scheduled tasks.", "Which are active?", "Show the first one."],
    ]},
    "skills": {"tools": ["manage_skills"], "seeds": [
        ["List my skills.", "Which one is about email?", "Show it."],
    ]},
    "search_browser": {"tools": ["web_fetch", "web_search", "private_browser"], "seeds": [
        ["Summarize https://investors.bendingspoons.com/newsroom/bending-spoons-agrees-to-acquire-miro", "What else did it say about Miro?"],
        ["Browse IKEA and find a good office chair.", "Open the best option and tell me its price."],
    ]},
    "cookbook_admin": {"tools": ["list_cookbook_servers", "list_served_models", "list_cached_models"], "seeds": [
        ["List running model servers.", "Which model is currently served?"],
    ]},
    "shell_files": {"tools": ["ls", "read_file", "bash"], "seeds": [
        ["List the files in the current workspace without changing anything.", "Which Markdown files are there?"],
    ]},
    "research": {"tools": ["trigger_research", "manage_research"], "seeds": [
        ["Research why Boston terriers make good companion dogs.", "Is it still running?"],
    ]},
    "ui": {"tools": ["ui_control"], "seeds": [
        ["Open documents.", "Now open the gallery."],
    ]},
    "switching": {"tools": ["manage_calendar", "manage_notes"], "seeds": [
        ["Show my calendar.", "Actually show my notes.", "Go back—what was next on my calendar?"],
    ]},
}


def compact_tool_catalog() -> dict[str, Any]:
    """Describe the complete native Odysseus tool surface for the teacher.

    The catalog is intentionally compact enough to include on every generation
    and judging call.  It explains all tools, while the target model still sees
    only the per-turn contract selected by the production harness.
    """
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    from src.turn_contract import FAMILY_TOOLS

    family_by_tool: dict[str, list[str]] = {}
    for family, names in FAMILY_TOOLS.items():
        for name in names:
            family_by_tool.setdefault(name, []).append(family)
    tools = []
    for schema in FUNCTION_TOOL_SCHEMAS:
        function = schema.get("function") or {}
        name = str(function.get("name") or "")
        params = function.get("parameters") or {}
        props = params.get("properties") or {}
        fields = []
        for field, spec in props.items():
            if not isinstance(spec, dict):
                continue
            entry: dict[str, Any] = {"name": field, "type": spec.get("type") or "any"}
            if isinstance(spec.get("enum"), list):
                entry["values"] = spec["enum"]
            fields.append(entry)
        purpose = re.sub(r"\s+", " ", str(function.get("description") or ""))[:280]
        if name == "manage_notes":
            purpose = (
                "Saved notes/checklists. Personal one-off reminders belong here: add/update a note "
                "with due_date (natural language or ISO), which fires a notification. Do not use "
                "manage_tasks merely because a user says remind me once."
            )
        elif name == "manage_tasks":
            purpose = (
                "Scheduled automation and future agent work: recurring jobs, delayed retries, or a "
                "future LLM/research/action run. A simple personal one-off reminder that only needs "
                "a notification belongs to manage_notes.due_date."
            )
        tools.append({
            "name": name,
            "families": sorted(family_by_tool.get(name, [])),
            "purpose": purpose,
            "required": params.get("required") or [],
            "fields": fields,
        })
    return {
        "tool_count": len(tools),
        "tools": tools,
        "aliases": {
            "mcp__email__*": "Email MCP aliases execute the corresponding canonical email tool.",
            "browser MCP tools": "Raw browser actions are represented to the model by private_browser in the compact WebUI contract.",
        },
    }


@dataclass(frozen=True)
class TeacherEndpoint:
    base_url: str
    api_key: str
    model: str


def _decrypt(value: str, data_dir: Path) -> str:
    if not value or not value.startswith("enc:"):
        return value or ""
    from cryptography.fernet import Fernet, InvalidToken
    try:
        return Fernet((data_dir / ".app_key").read_bytes()).decrypt(
            value.removeprefix("enc:").encode("ascii")
        ).decode("utf-8")
    except (OSError, InvalidToken, ValueError):
        return ""


def endpoint_from_db(data_dir: Path, endpoint_id: str, model: str) -> TeacherEndpoint:
    db = sqlite3.connect(data_dir / "app.db")
    db.row_factory = sqlite3.Row
    try:
        row = db.execute(
            "SELECT base_url, api_key FROM model_endpoints WHERE id=? AND is_enabled=1",
            (endpoint_id,),
        ).fetchone()
    finally:
        db.close()
    if not row:
        raise RuntimeError(f"enabled teacher endpoint {endpoint_id!r} was not found")
    key = _decrypt(str(row["api_key"] or ""), data_dir)
    if not key:
        raise RuntimeError(f"teacher endpoint {endpoint_id!r} has no usable credential")
    return TeacherEndpoint(str(row["base_url"]).rstrip("/"), key, model)


def _json_from_text(text: str) -> Any:
    value = str(text or "").strip()
    value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.I)
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        # OpenAI-compatible gateways occasionally prepend prose or concatenate
        # a second object despite response_format=json_object. Recover the
        # first complete JSON value rather than expanding from the first open
        # bracket to the final close bracket, which turns concatenation into
        # an avoidable ``Extra data`` failure.
        decoder = json.JSONDecoder()
        candidates = []
        for start, char in enumerate(value):
            if char not in "[{":
                continue
            try:
                parsed, _ = decoder.raw_decode(value[start:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, (dict, list)):
                candidates.append(parsed)
                if isinstance(parsed, dict) and (
                    parsed.get("verdict") in {"pass", "fail", "uncertain"}
                    or isinstance(parsed.get("flows"), list)
                ):
                    return parsed
        if candidates:
            return candidates[0]
        raise


def teacher_json(endpoint: TeacherEndpoint, payload: dict[str, Any], *, max_tokens=5000,
                 temperature=0.25, attempts: int | None = None) -> Any:
    body = {
        "model": endpoint.model,
        "messages": [
            {"role": "system", "content": (
                "Return exactly one strict JSON object, never an array, prose, or markdown. "
                "Never include secrets."
            )},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    last_error = ""
    if attempts is None:
        attempts = int(os.environ.get("ODYSSEUS_QA_TEACHER_ATTEMPTS", "3"))
    attempts = max(1, min(3, attempts))
    timeout = max(15.0, min(120.0, float(os.environ.get("ODYSSEUS_QA_TEACHER_TIMEOUT", "120"))))
    for attempt in range(attempts):
        try:
            response = httpx.post(
                endpoint.base_url + "/chat/completions",
                headers={"Authorization": f"Bearer {endpoint.api_key}"},
                json=body,
                timeout=timeout,
            )
            response.raise_for_status()
            message = response.json()["choices"][0]["message"]
            content = message.get("content") or ""
            if not content and isinstance(message.get("reasoning"), str):
                content = message["reasoning"]
            return _json_from_text(content)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < attempts - 1:
                time.sleep(1 + attempt)
    raise RuntimeError(
        f"teacher did not return valid JSON after {attempts} attempts ({last_error})"
    )


def generate_flows(endpoint: TeacherEndpoint, families: Iterable[str], flows_per_family: int) -> list[dict[str, Any]]:
    specs = {family: FAMILY_SEEDS[family] for family in families}
    result = teacher_json(endpoint, {
        "task": "Generate realistic multi-turn QA conversations for an AI tool harness.",
        "rules": [
            f"Return exactly {flows_per_family} flows per family.",
            "Use the seeds as behavioral inspiration, not literal templates.",
            "Each flow must contain 2-4 user turns and at least one ambiguous follow-up.",
            "Include natural typos in roughly one third of flows.",
            "Do not invent record IDs, secret values, or destructive requests.",
            "Keep expected behavior semantic; do not prescribe exact assistant wording.",
            "Family switches are allowed only for the switching family.",
        ],
        "schema": {"flows": [{
            "id": "short_unique_id", "family": "one supplied family",
            "purpose": "behavior under test",
            "turns": [{"user": "message", "expect": "semantic expected behavior"}],
        }]},
        "complete_odysseus_tool_catalog": compact_tool_catalog(),
        "families": specs,
    }, temperature=0.65)
    flows = result.get("flows", []) if isinstance(result, dict) else []
    valid = []
    counts = {family: 0 for family in families}
    for item in flows:
        if not isinstance(item, dict) or item.get("family") not in counts:
            continue
        turns = item.get("turns")
        if not isinstance(turns, list) or not 2 <= len(turns) <= 4:
            continue
        if any(not isinstance(t, dict) or not str(t.get("user", "")).strip() for t in turns):
            continue
        family = item["family"]
        if counts[family] >= flows_per_family:
            continue
        counts[family] += 1
        item["id"] = re.sub(r"[^a-zA-Z0-9_-]", "-", str(item.get("id") or uuid.uuid4().hex[:10]))[:60]
        valid.append(item)
    missing = {family: flows_per_family - count for family, count in counts.items() if count < flows_per_family}
    if missing:
        raise RuntimeError(f"teacher returned an incomplete flow set: {missing}")
    return valid


def flows_from_file(path: Path, families: Iterable[str], *,
                    prior_verdict: str | None = None,
                    prior_owner: str | None = None,
                    transport_only: bool = False) -> list[dict[str, Any]]:
    """Load prior generated flows so a fix can replay identical prompts."""
    raw = path.read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
        rows = (
            payload.get("flows") or payload.get("results") or payload.get("candidates")
        ) if isinstance(payload, dict) else payload
    except json.JSONDecodeError:
        rows = []
        for number, line in enumerate(raw.splitlines(), 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid JSONL flow on line {number}: {exc}") from exc
    if not isinstance(rows, list):
        raise RuntimeError("flow file must contain an array or a top-level flows array")
    wanted = set(families)
    flows = []
    for row in rows:
        if not isinstance(row, dict) or row.get("family") not in wanted:
            continue
        if prior_verdict and (row.get("judge") or {}).get("verdict") != prior_verdict:
            continue
        if prior_owner and (row.get("judge") or {}).get("owner") != prior_owner:
            continue
        if transport_only and not replay_transport_failure(row):
            continue
        turns = row.get("turns")
        # Generated flows remain 2-4 turns, while historical contract corpora
        # also contain useful single-turn routing probes.
        if not isinstance(turns, list) or not 1 <= len(turns) <= 4:
            continue
        flow = {key: value for key, value in row.items()
                if key not in {"observed", "judge", "session_id", "url"}}
        flow.setdefault("id", source_seed_id(row))
        flows.append(flow)
    if not flows:
        raise RuntimeError("flow file contained no valid requested flows")
    return flows


def source_seed_id(flow: dict[str, Any]) -> str:
    """Return the stable corpus identity used to distinguish coverage from retries."""
    return str(flow.get("source_seed_id") or flow.get("id") or "").strip()


def flow_may_mutate(flow: dict[str, Any]) -> bool:
    """Conservatively identify flows that can alter the shared SFT fixture."""
    family = str(flow.get("family") or "")
    turns = flow.get("turns") or []
    user_text = "\n".join(
        str(turn.get("user") or "") for turn in turns if isinstance(turn, dict)
    )
    expected_text = "\n".join(
        str(turn.get("expect") or "") for turn in turns if isinstance(turn, dict)
    )
    # Negative safety qualifiers are common in cooked read-only probes. Strip
    # only their local clause before looking for positive mutation authority;
    # otherwise “do not edit, delete, or create anything” is misread as three
    # write requests and silently removed from read-only coverage.
    def positive_only(value: str) -> str:
        value = _NEGATED_MUTATION_CLAUSE_RE.sub("", value)
        value = _NO_MUTATION_NOUN_RE.sub("", value)
        value = _NO_MUTATION_VERB_LIST_RE.sub("", value)
        return _REFERENTIAL_REMIND_RE.sub("ask ", value)

    positive_user = positive_only(user_text)
    positive_expected = positive_only(expected_text)
    if (_MUTATING_ACTION_RE.search(positive_user)
            or _MUTATING_ACTION_RE.search(positive_expected)
            or _MUTATING_REQUEST_RE.search(positive_user)
            or _MUTATING_CONTEXT_ACTION_RE.search(positive_user)
            or _MUTATING_EXPECTATION_RE.search(positive_expected)
            or _MUTATING_EXPECTATION_ACTION_FIRST_RE.search(positive_expected)
            or _MUTATING_EXPECTATION_FAMILY_TOOL_RE.search(positive_expected)
            or _MUTATING_EXPECTATION_NATURAL_RE.search(positive_expected)):
        return True
    # Starting deep research creates a durable background job/report even if
    # the wording does not contain a conventional CRUD verb.
    if family == "research":
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            user = str(turn.get("user") or "")
            if re.search(r"\b(?:research|investigate|look into|deep dive)\b", user, re.I) \
                    and not re.search(r"\b(?:list|show|open|read|status|still running)\b", user, re.I):
                return True
    return False


def flow_has_locally_restorable_mutation(flow: dict[str, Any]) -> bool:
    """Return whether every durable mutation stays in the owner fixture."""
    return (
        flow_may_mutate(flow)
        and str(flow.get("family") or "") in LOCALLY_RESTORABLE_MUTATION_FAMILIES
    )


def flow_has_orphaned_opening_followup(flow: dict[str, Any]) -> bool:
    """Reject standalone QA flows whose first turn requires missing history.

    Generated variants sometimes preserve a follow-up but drop its setup turn.
    Keep this deliberately narrow: named operations such as ``rerun the nightly
    backup task`` remain valid, while demonstrative/past-comparison openings do
    not become model or harness failures.
    """
    turns = flow.get("turns") or []
    if not turns or not isinstance(turns[0], dict):
        return False
    opening = str(turns[0].get("user") or "").strip()
    return bool(re.match(
        r"(?:"
        r"your\s+previous\s+(?:reply|answer|response)\b[^.!?\n]{0,120}"
        r"(?:cut\s+off|ended|stopped)|"
        r"(?:re-?run|repeat|redo|do)\s+(?:that|it|the\s+same)\b"
        r"|(?:pull|bring)\s+(?:those|them|it|that)\b[^.!?\n]{0,100}\bagain\b"
        r"|same\s+(?:result|answer|output|thing)\s+as\s+(?:before|last\s+time)\b"
        r"|(?:tell|show|give)\s+me\s+more\s+(?:about\s+)?(?:that|it)\b"
        r")",
        opening,
        re.I,
    ))


def flow_is_auditable(flow: dict[str, Any]) -> bool:
    """Keep only self-contained flows with an unambiguous expected surface."""
    expected = "\n".join(
        str(turn.get("expect") or "")
        for turn in (flow.get("turns") or [])
        if isinstance(turn, dict)
    )
    requires_native_workspace_tool = any(
        re.search(rf"(?<!\w){re.escape(tool)}(?!\w)", expected, re.I)
        for tool in _SFT_WEBUI_POLICY_DISABLED_TOOLS
    ) or bool(re.search(
        r"\b(?:run|use|execute)\s+(?:a\s+)?(?:local\s+)?(?:shell|bash)\b|"
        r"\b(?:shell|bash)\s+(?:version\s+)?check\b",
        expected,
        re.I,
    ))
    return (
        source_seed_id(flow) not in AMBIGUOUS_GENERATED_SEEDS
        and not flow_has_orphaned_opening_followup(flow)
        and not requires_native_workspace_tool
    )


def balanced_flows(flows: list[dict[str, Any]], per_family: int | None) -> list[dict[str, Any]]:
    """Round-robin families while capping each for broad early coverage."""
    if per_family is None:
        return flows
    grouped: dict[str, list[dict[str, Any]]] = {}
    for flow in flows:
        family = str(flow.get("family") or "unknown")
        bucket = grouped.setdefault(family, [])
        if len(bucket) < per_family:
            bucket.append(flow)
    selected: list[dict[str, Any]] = []
    for index in range(per_family):
        for bucket in grouped.values():
            if index < len(bucket):
                selected.append(bucket[index])
    return selected


def audited_source_seed_ids(out_dir: Path, target_model: str,
                            routing_experiment: str = "baseline") -> set[str]:
    """Collect seeds with a completed judgment over valid replay evidence."""
    audited: set[str] = set()
    for path in sorted(out_dir.glob("run-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        # Unstamped historical artifacts exercised the baseline router.  They
        # must not suppress replay coverage for the model-specific runtime.
        if (payload.get("target_model") != target_model
                or payload.get("routing_experiment", "baseline") != routing_experiment):
            continue
        for result in payload.get("results") or ():
            if not isinstance(result, dict):
                continue
            # Older runner versions sent connection-refused traces to the
            # external judge, which could still return pass/fail.  Such a
            # verdict is not coverage: no model/harness behavior was observed.
            if replay_transport_failure(result):
                continue
            verdict = (result.get("judge") or {}).get("verdict")
            seed_id = source_seed_id(result)
            if seed_id and verdict in {"pass", "fail"}:
                audited.add(seed_id)
    return audited


def write_coverage_manifest(path: Path, corpus: list[dict[str, Any]], *,
                            audited_ids: set[str], target_model: str,
                            routing_experiment: str = "baseline") -> dict[str, Any]:
    """Persist unique judged coverage over the canonical cooked corpus."""
    corpus_ids = {source_seed_id(flow) for flow in corpus if source_seed_id(flow)}
    covered = corpus_ids & audited_ids
    pending = corpus_ids - covered
    by_family: dict[str, dict[str, int]] = {}
    family_seen: set[tuple[str, str]] = set()
    for flow in corpus:
        seed_id = source_seed_id(flow)
        family = str(flow.get("family") or "unknown")
        if not seed_id or (family, seed_id) in family_seen:
            continue
        family_seen.add((family, seed_id))
        bucket = by_family.setdefault(family, {"total": 0, "audited": 0, "pending": 0})
        bucket["total"] += 1
        bucket["audited" if seed_id in covered else "pending"] += 1
    manifest = {
        "target_model": target_model,
        "routing_experiment": routing_experiment,
        "corpus_seeds": len(corpus_ids),
        "audited_unique_seeds": len(covered),
        "pending_unique_seeds": len(pending),
        "coverage_percent": round(100 * len(covered) / len(corpus_ids), 2) if corpus_ids else 0,
        "by_family": dict(sorted(by_family.items())),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def latest_cookie(data_dir: Path, owner: str) -> str:
    values = json.loads((data_dir / "sessions.json").read_text(encoding="utf-8"))
    candidates = [
        (str(meta.get("expiry") or ""), token)
        for token, meta in values.items()
        if isinstance(meta, dict) and meta.get("username") == owner
    ]
    if not candidates:
        raise RuntimeError(f"no browser session exists for {owner}")
    return max(candidates)[1]


def sse_events(response: httpx.Response) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    event_name = ""
    parts: list[str] = []
    for line in response.iter_lines():
        if line.startswith("event:"):
            event_name = line.partition(":")[2].strip()
        elif line.startswith("data:"):
            parts.append(line.partition(":")[2].lstrip())
        elif not line.strip() and parts:
            raw = "\n".join(parts)
            parts = []
            if raw == "[DONE]":
                events.append({"type": "done"})
            else:
                try:
                    item = json.loads(raw)
                except json.JSONDecodeError:
                    item = {"type": event_name or "raw", "content": raw}
                if isinstance(item, dict):
                    item.setdefault("type", event_name or "data")
                    events.append(item)
            event_name = ""
    return events


def create_session(client: httpx.Client, args: argparse.Namespace, flow: dict[str, Any]) -> str:
    last_error: Exception | None = None
    for attempt in range(6):
        try:
            response = client.post(args.base_url + "/api/session", data={
                "name": f"[harness-qa] {flow['family']} {flow['id']}",
                "endpoint_id": args.target_endpoint_id,
                "model": args.target_model,
                "rag": "false",
            }, timeout=30)
            response.raise_for_status()
            session_id = str(response.json()["id"])
            # Replay sessions must not launch post-response memory extraction
            # against the model under test. It competes for inference slots,
            # mutates fixture memory, and is not part of tool-harness scoring.
            toggle = client.post(
                args.base_url + f"/api/session/{session_id}/memory-extraction",
                json={"enabled": False}, timeout=30,
            )
            toggle.raise_for_status()
            return session_id
        except (httpx.ConnectError, httpx.ReadError) as exc:
            last_error = exc
            if attempt < 5:
                time.sleep(1)
    raise RuntimeError(f"7011 did not become ready ({last_error})")


def run_turn(client: httpx.Client, args: argparse.Namespace, sid: str, message: str,
             *, family: str = "") -> list[dict[str, Any]]:
    fields = {
        "message": message, "session": sid, "mode": "agent",
        "selected_endpoint_id": args.target_endpoint_id,
        "selected_model": args.target_model,
        "thinking_mode": "off", "allow_web_search": "false",
        # Shell/files probes must exercise the real shell surface. Keeping the
        # toggle off made those rows authorization tests, not model/harness QA.
        "allow_bash": "true" if family == "shell_files" else "false",
        "use_rag": "false",
    }
    with client.stream(
        "POST", args.base_url + "/api/chat_stream", data=fields,
        headers={
            "Accept": "text/event-stream",
            "x-odysseus-routing-experiment": args.routing_experiment,
        }, timeout=args.timeout,
    ) as response:
        response.raise_for_status()
        return sse_events(response)


def compact_turn(user: str, expected: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    contract = next((e for e in events if e.get("type") == "turn_contract"), {})
    metrics = next((e.get("data", {}) for e in events if e.get("type") == "metrics"), {})
    starts = [e for e in events if e.get("type") == "tool_start"]
    outputs = [e for e in events if e.get("type") in {"tool_output", "tool_result"}]
    final = ""
    if isinstance(metrics, dict):
        texts = metrics.get("round_texts") or []
        if texts:
            final = str(texts[-1])
    if not final:
        final = "".join(str(e.get("delta") or "") for e in events)
    return {
        "user": user,
        "expected": expected,
        "contract": {
            "capabilities": contract.get("capabilities", []),
            "required": contract.get("required", []),
            "offered": contract.get("offered", []),
            "unavailable": contract.get("unavailable", []),
        },
        "tool_calls": [{"tool": e.get("tool"), "command": str(e.get("command") or "")[:500]} for e in starts],
        "tool_results": [{"tool": e.get("tool"), "output": str(e.get("output") or "")[:1200]} for e in outputs],
        "errors": [e for e in events if e.get("type") == "error"],
        "final": final[:4000],
        "saved": any(e.get("type") == "message_saved" for e in events),
        "metrics": {key: metrics.get(key) for key in (
            "time_to_first_token", "input_tokens", "output_tokens", "tokens_per_second"
        ) if isinstance(metrics, dict) and metrics.get(key) is not None},
    }


def replay_flow(flow: dict[str, Any], args: argparse.Namespace, cookie: str) -> dict[str, Any]:
    with httpx.Client(cookies={"odysseus_session": cookie}, follow_redirects=True) as client:
        sid = ""
        turns = []
        try:
            sid = create_session(client, args, flow)
            for turn in flow["turns"]:
                events = run_turn(
                    client, args, sid, str(turn["user"]), family=str(flow.get("family") or ""),
                )
                turns.append(compact_turn(str(turn["user"]), str(turn.get("expect") or ""), events))
            history_response = client.get(args.base_url + f"/api/history/{sid}", timeout=30)
            history_response.raise_for_status()
            rows = history_response.json().get("history") or []
            assistant_rows = [row for row in rows if isinstance(row, dict) and row.get("role") == "assistant"]
            for index, observed in enumerate(turns):
                if index < len(assistant_rows):
                    # Canonical tool-owned rendering can intentionally omit a
                    # streamed prose delta. The durable history is what the
                    # user sees after reconciliation and therefore what the
                    # judge must evaluate.
                    observed["final"] = str(assistant_rows[index].get("content") or "")[:4000]
        except Exception as exc:
            turns.append({"user": "", "expected": "", "errors": [repr(exc)], "final": ""})
        return {
            **flow,
            "session_id": sid,
            "url": f"{args.public_url}/#{sid}" if sid else "",
            "observed": turns,
        }


def replay_flows_with_fixture_isolation(
    flows: list[dict[str, Any]], args: argparse.Namespace, cookie: str,
) -> list[dict[str, Any]]:
    """Replay serially and restore the fixture owner around every flow.

    Sessions/messages are intentionally retained by the snapshot utility so
    WebUI evidence remains inspectable. Tool-owned state is restored even when
    a replay raises unexpectedly.
    """
    baseline = snapshot_owner(args.fixture_db, args.owner, args.data_dir)
    results: list[dict[str, Any]] = []
    try:
        for flow in flows:
            restore_owner(args.fixture_db, baseline, args.owner, args.data_dir)
            try:
                results.append(replay_flow(flow, args, cookie))
            finally:
                restore_owner(args.fixture_db, baseline, args.owner, args.data_dir)
    finally:
        # A final idempotent restore also covers failures between flows.
        restore_owner(args.fixture_db, baseline, args.owner, args.data_dir)
    return results


def _validated_judge_verdict(judged: Any) -> dict[str, Any]:
    """Validate the judge contract without interpreting malformed output."""
    if isinstance(judged, dict):
        verdict = str(judged.get("verdict") or "").strip().lower()
        if verdict in {"pass", "fail", "uncertain"}:
            summary = str(judged.get("summary") or "").strip()
            evidence = judged.get("evidence") or []
            placeholder = lambda value: not re.search(r"[A-Za-z0-9]", str(value or ""))
            if placeholder(summary) or not isinstance(evidence, list) or any(
                placeholder(value) for value in evidence
            ):
                raise RuntimeError("judge returned placeholder summary/evidence")
            if verdict == "fail" and (
                str(judged.get("owner") or "") not in {
                    "harness_routing", "harness_execution", "harness_rendering",
                    "model_sft", "backend_data", "none",
                }
                or not str(judged.get("failure_category") or "").strip()
                or not isinstance(judged.get("failed_turns"), list)
                or not judged.get("failed_turns")
                or not evidence
            ):
                raise RuntimeError("judge returned an incomplete failure verdict")
            judged["verdict"] = verdict
            return judged
        detail = f"keys={sorted(judged)} verdict={judged.get('verdict')!r}"
    else:
        detail = f"type={type(judged).__name__} sample={repr(judged)[:500]}"
    raise RuntimeError(f"judge returned an invalid verdict ({detail})")


def judge_flow(endpoint: TeacherEndpoint, result: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "task": "Audit this AI tool-harness conversation. Judge behavior, not exact wording.",
        "rules": [
            "A required action must have an appropriate successful tool call.",
            "A prose answer may use prior successful evidence without another tool call.",
            "Do not penalize optional arguments or stylistic differences.",
            "Flag wrong-family routing, missing tools, malformed calls, loops, false success, empty rendering, and broken follow-up continuity.",
            "Markdown inside <!-- ... --> is hidden expansion payload, not visibly listed content; judge explicit item limits from the visible text before that block, and treat an '[...and N more]' expansion link as compliant.",
            "For ui_control, Cookbook view='Search' is the UI's Download/models tab and satisfies a request for the models view; view='Serve' is its Launch/serve tab.",
            "Classify ownership as harness_routing, harness_execution, harness_rendering, model_sft, backend_data, or none.",
            "Do not reveal or reconstruct hidden personal data from tool outputs.",
        ],
        "output_contract": [
            "Return one object, not an array.",
            "verdict: exactly pass, fail, or uncertain.",
            "score: integer from 0 through 100.",
            "owner: harness_routing, harness_execution, harness_rendering, model_sft, backend_data, or none.",
            "failure_category: short stable category, or an empty string.",
            "summary: one sentence.",
            "failed_turns: array of 1-based turn numbers.",
            "evidence: array of short observable facts.",
            "reproduction: array containing only user messages.",
        ],
        "complete_odysseus_tool_catalog": compact_tool_catalog(),
        "flow": result,
    }
    judged = teacher_json(endpoint, payload, max_tokens=2500, temperature=0, attempts=1)
    try:
        return _validated_judge_verdict(judged)
    except RuntimeError as first_error:
        # Some OpenAI-compatible judges occasionally ignore json_object and
        # emit a bare list. Retry only schema-invalid responses; transport
        # failures still escape immediately to the configured fallback.
        # The first audit sees the complete tool catalog. A schema-only retry
        # does not need to resend that ~46 KB catalog; doing so caused some
        # judges to repeat only the ``failed_turns`` array instead of the
        # required object. Keep the observed flow, rules, and output contract.
        correction = {
            key: value for key, value in payload.items()
            if key != "complete_odysseus_tool_catalog"
        }
        correction["task"] = (
            "Correct a malformed audit verdict. Return exactly one complete JSON object "
            "matching output_contract; never return a list, scalar, prose, or markdown."
        )
        correction["previous_invalid_output"] = judged
        corrected = teacher_json(
            endpoint, correction, max_tokens=2500, temperature=0, attempts=1,
        )
        try:
            return _validated_judge_verdict(corrected)
        except RuntimeError as second_error:
            raise RuntimeError(
                f"judge schema correction failed: first={first_error}; second={second_error}"
            ) from second_error


def replay_transport_failure(result: dict[str, Any]) -> str:
    """Return an infrastructure error without confusing it for model behavior."""
    for turn in result.get("observed") or []:
        for error in turn.get("errors") or []:
            value = str(error)
            if re.search(
                r"ConnectError|ReadError|RemoteProtocolError|Connection refused|"
                r"did not become ready|timed? out",
                value,
                re.I,
            ):
                return value[:500]
    return ""


_ROUTING_FAILURE_CATEGORIES = re.compile(
    r"(?:missing.*tool|tool.*not.*(?:called|offered)|required.*tool.*not.*offered|"
    r"capability.*(?:dropped|not.*offered)|wrong.*family.*routing|"
    r"false.*success.*missing.*tool)",
    re.I,
)


def normalize_judge_ownership(result: dict[str, Any], judged: dict[str, Any]) -> dict[str, Any]:
    """Correct only ownership claims contradicted by recorded tool availability.

    The external judge is useful for semantics but occasionally calls a missing
    tool invocation ``model_sft`` even when the harness offered zero tools.  A
    model cannot invoke an absent tool.  Keep this correction deliberately
    narrow so weak prose and misuse of an offered tool remain model-owned.
    """
    if judged.get("verdict") != "fail":
        return judged
    observed = result.get("observed") or []
    failed_turns = judged.get("failed_turns") or []
    indexes = [value - 1 for value in failed_turns if isinstance(value, int) and value > 0]
    candidates = [observed[index] for index in indexes if index < len(observed)]
    if not candidates:
        candidates = observed
    source_turns = result.get("turns") or []
    expected_tool_pattern = re.compile(
        r"\b(?:bash|python|read_file|write_file|web_search|web_fetch|private_browser|"
        r"manage_[a-z_]+|list_[a-z_]+|trigger_research|ui_control|extract_text)\b",
        re.I,
    )
    for index in indexes:
        if index >= len(observed) or index >= len(source_turns):
            continue
        turn = observed[index] if isinstance(observed[index], dict) else {}
        expected = str((source_turns[index] or {}).get("expect") or "")
        expected_tools = {name.casefold() for name in expected_tool_pattern.findall(expected)}
        offered = {
            str(name).rsplit("__", 1)[-1].casefold()
            for name in ((turn.get("contract") or {}).get("offered") or [])
        }
        if expected_tools and not (expected_tools & offered) and not (turn.get("tool_calls") or []):
            corrected = dict(judged)
            corrected["judge_reported_owner"] = judged.get("owner")
            corrected["owner"] = "harness_routing"
            return corrected
    if re.search(r"(?:item|result|title|entry).*limit|limit.*(?:violation|exceed)",
                 str(judged.get("failure_category") or ""), re.I):
        canonical_list_actions = {
            "manage_calendar": {"list", "list_events"},
            "manage_documents": {"list"},
            "manage_memory": {"list"},
            "manage_notes": {"list", "search", "find"},
            "manage_skills": {"list", "index", "search", "find"},
            "manage_tasks": {"list"},
        }
        for turn in candidates:
            if not isinstance(turn, dict):
                continue
            for call in turn.get("tool_calls") or []:
                tool = str(call.get("tool") or "").rsplit("__", 1)[-1]
                try:
                    command = json.loads(call.get("command") or "{}")
                except (TypeError, json.JSONDecodeError):
                    command = {}
                action = str(command.get("action") or "").replace("-", "_").casefold()
                if action in canonical_list_actions.get(tool, set()):
                    corrected = dict(judged)
                    corrected["judge_reported_owner"] = judged.get("owner")
                    corrected["owner"] = "harness_execution"
                    corrected["failure_category"] = "canonical_result_limit"
                    return corrected
    # If the flow explicitly names the expected tool, the harness offered it,
    # and the model called a different offered tool, that is a selection miss.
    # Judges sometimes label this "wrong tool family" and incorrectly assign
    # it to routing even though routing exposed the required choice.
    if (
        judged.get("owner") in {"harness_routing", "harness_execution"}
        and re.search(
            r"wrong.*tool|wrong.*family|missing.*tool.*call",
            str(judged.get("failure_category") or ""), re.I,
        )
    ):
        for index in indexes:
            if index >= len(observed) or index >= len(source_turns):
                continue
            turn = observed[index] if isinstance(observed[index], dict) else {}
            expected = str((source_turns[index] or {}).get("expect") or "")
            offered = {
                str(name).rsplit("__", 1)[-1]
                for name in ((turn.get("contract") or {}).get("offered") or [])
            }
            expected_offered = {
                name for name in offered
                if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", expected, re.I)
            }
            called = {
                str(call.get("tool") or "").rsplit("__", 1)[-1]
                for call in (turn.get("tool_calls") or []) if isinstance(call, dict)
            }
            if expected_offered and not (called & expected_offered):
                corrected = dict(judged)
                corrected["judge_reported_owner"] = judged.get("owner")
                corrected["owner"] = "model_sft"
                return corrected
    # Canonical one-off personal reminders are note notifications. A judge or
    # generated expectation may incorrectly demand manage_tasks because the
    # word "remind" sounds scheduled. If notes was offered and the model added
    # a note without due_date, the real failure is the model's missing argument.
    for turn in candidates:
        if not isinstance(turn, dict) or not re.search(
            r"\bremind\s+me\b.*\b(?:tomorrow|tonight|morning|afternoon|evening|"
            r"today|next\s+week|in\s+\d+\s+(?:minutes?|hours?|days?))\b",
            str(turn.get("user") or ""),
            re.I,
        ):
            continue
        offered = set((turn.get("contract") or {}).get("offered") or [])
        note_calls = []
        for call in turn.get("tool_calls") or []:
            tool_name = str(call.get("tool") or "")
            if tool_name.rsplit("__", 1)[-1] != "manage_notes":
                continue
            try:
                command = json.loads(call.get("command") or "{}")
            except (TypeError, json.JSONDecodeError):
                command = {}
            note_calls.append(command)
        if "manage_notes" in offered and note_calls and all(
            not str(command.get("due_date") or "").strip() for command in note_calls
        ):
            corrected = dict(judged)
            corrected["judge_reported_owner"] = judged.get("owner")
            corrected["owner"] = "model_sft"
            corrected["failure_category"] = "missing_reminder_due_date"
            return corrected
    if not _ROUTING_FAILURE_CATEGORIES.search(
        str(judged.get("failure_category") or "")
    ):
        return judged
    # Do not let an empty downstream follow-up erase an earlier model-owned
    # miss where the required tool was available. The harness owns the flow
    # only when every failed candidate lacked an executable surface.
    absent_tool_surface = bool(candidates) and all(
        isinstance(turn, dict)
        and not ((turn.get("contract") or {}).get("offered") or [])
        and not (turn.get("tool_calls") or [])
        for turn in candidates
    )
    if absent_tool_surface and judged.get("owner") != "harness_routing":
        judged = dict(judged)
        judged["judge_reported_owner"] = judged.get("owner")
        judged["owner"] = "harness_routing"
    return judged


def ungrounded_visible_referent(result: dict[str, Any]) -> dict[str, Any] | None:
    """Reject entity follow-ups that select a row the user never saw."""
    entity_fields = {
        "manage_notes": ("id", "#note-"),
        "manage_calendar": ("uid", "#event-"),
        "manage_tasks": ("task_id", "#task-"),
        "manage_memory": ("memory_id", "#memory-"),
        "manage_documents": ("document_id", "#document-"),
        "read_email": ("uid", "#email-"),
    }
    referential = re.compile(
        r"\b(?:show|open|read|view)\b[^.!?]{0,80}\b"
        r"(?:it|that|this|(?:the\s+)?(?:first|second|third|last|latest|newest|oldest)\s+one)\b",
        re.I,
    )
    observed = result.get("observed") or []
    for index, turn in enumerate(observed):
        if index == 0 or not isinstance(turn, dict) or not referential.search(
            str(turn.get("user") or "")
        ):
            continue
        visible = "\n".join(
            str(prior.get("final") or "")
            for prior in observed[:index] if isinstance(prior, dict)
        )
        for call in turn.get("tool_calls") or []:
            tool = str(call.get("tool") or "").rsplit("__", 1)[-1]
            if tool not in entity_fields:
                continue
            field, prefix = entity_fields[tool]
            if prefix not in visible:
                continue
            try:
                command = json.loads(call.get("command") or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            identifier = str(command.get(field) or "").strip()
            if identifier.startswith(prefix):
                identifier = identifier[len(prefix):]
            if identifier and f"{prefix}{identifier}" not in visible:
                return {
                    "verdict": "fail", "score": 30, "owner": "model_sft",
                    "failure_category": "ungrounded_visible_referent",
                    "summary": "A referential follow-up selected an entity that was not present in the user-visible prior results.",
                    "failed_turns": [index + 1],
                    "evidence": [
                        f"Turn {index + 1} called {tool} with {field}={identifier!r}, but {prefix}{identifier} was absent from prior visible answers."
                    ],
                    "reproduction": [
                        str(source.get("user") or "")
                        for source in result.get("turns") or []
                        if str(source.get("user") or "").strip()
                    ],
                }
    return None


def safe_judge_flow(endpoint: TeacherEndpoint, result: dict[str, Any],
                    fallback: TeacherEndpoint | None = None) -> dict[str, Any]:
    transport_error = replay_transport_failure(result)
    if transport_error:
        return {
            "verdict": "uncertain", "score": 0, "owner": "backend_data",
            "failure_category": "replay_transport_unavailable",
            "summary": "The 7011 replay transport failed, so model and harness behavior were not judged.",
            "failed_turns": [], "evidence": [transport_error],
            "reproduction": [
                str(turn.get("user") or "") for turn in result.get("turns") or []
                if str(turn.get("user") or "").strip()
            ],
        }
    deterministic_failure = ungrounded_visible_referent(result)
    if deterministic_failure is not None:
        return deterministic_failure
    try:
        return normalize_judge_ownership(result, judge_flow(endpoint, result))
    except Exception as exc:
        if fallback is not None:
            try:
                judged = normalize_judge_ownership(result, judge_flow(fallback, result))
                judged["judge_fallback"] = fallback.model
                return judged
            except Exception as fallback_exc:
                exc = RuntimeError(f"primary={exc!r}; fallback={fallback_exc!r}")
        return {
            "verdict": "uncertain", "score": 0, "owner": "none",
            "failure_category": "judge_unavailable",
            "summary": "The external judge did not return a valid verdict.",
            "failed_turns": [], "evidence": [f"{type(exc).__name__}: {exc}"],
            "reproduction": [],
        }


def append_ledger(path: Path, stamp: str, results: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            "# Odysseus Harness QA\n\n"
            "Conversation-level findings from the real 7011 Agent route. Raw traces live in `tmp/`; "
            "this ledger keeps only reproducible failures and run summaries.\n",
            encoding="utf-8",
        )
    failures = [r for r in results if r.get("judge", {}).get("verdict") == "fail"]
    uncertain = sum(r.get("judge", {}).get("verdict") == "uncertain" for r in results)
    passed = sum(r.get("judge", {}).get("verdict") == "pass" for r in results)
    lines = [f"\n## Run {stamp}\n",
             f"- Flows: {len(results)}; pass: {passed}; fail: {len(failures)}; judge unavailable: {uncertain}"]
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for result in failures:
        judge = result.get("judge", {})
        key = (
            str(result.get("family") or "unknown"),
            str(judge.get("owner") or "uncertain"),
            str(judge.get("failure_category") or "uncertain"),
        )
        grouped.setdefault(key, []).append(result)
    for (family, owner, category), members in grouped.items():
        representative = members[0]
        judge = representative.get("judge", {})
        count = f" ({len(members)} occurrences)" if len(members) > 1 else ""
        lines.append(
            f"- `{family}` / `{owner}` / `{category}`{count} — "
            f"{judge.get('summary', '')} ([representative replay]({representative.get('url', '')}))"
        )
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Persist an audit checkpoint without exposing a partially written run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    temporary.replace(path)


def judge_replayed_flows(
    replayed: list[dict[str, Any]], teacher: TeacherEndpoint,
    fallback_judge: TeacherEndpoint | None, *, workers: int,
    checkpoint: Path, target_model: str, judge_model: str,
    routing_experiment: str = "baseline",
    resume_results: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Judge saved replay evidence and checkpoint each completed verdict.

    Results retain replay order.  A provider stall can therefore be interrupted
    and resumed from the immutable replay without exercising 7011 again.
    """
    index_by_id = {
        str(result.get("source_seed_id") or result.get("id") or index): index
        for index, result in enumerate(replayed)
    }
    completed: dict[int, dict[str, Any]] = {}
    for result in resume_results or []:
        key = str(result.get("source_seed_id") or result.get("id") or "")
        index = index_by_id.get(key)
        # Only terminal behavioral verdicts are reusable. ``uncertain`` means
        # transport/judge evidence was unavailable and must be retried from
        # the immutable replay rather than silently treated as complete.
        if index is not None and result.get("judge", {}).get("verdict") in {
            "pass", "fail",
        }:
            completed[index] = result
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(safe_judge_flow, teacher, result, fallback_judge): (index, result)
            for index, result in enumerate(replayed)
            if index not in completed
        }
        for future in concurrent.futures.as_completed(futures):
            index, replay = futures[future]
            result = dict(replay)
            result["judge"] = future.result()
            completed[index] = result
            atomic_write_json(checkpoint, {
                "target_model": target_model,
                "routing_experiment": routing_experiment,
                "judge_model": judge_model,
                "complete": len(completed) == len(replayed),
                "judged": len(completed),
                "total": len(replayed),
                "results": [completed[key] for key in sorted(completed)],
            })
    return [completed[index] for index in range(len(replayed))]


def resume_replay_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Recover immutable replay evidence from a judge checkpoint.

    A resume is intentionally self-contained: it must never generate or replay
    the CLI's default seed families merely because ``--judge-replay`` was not
    repeated.  Checkpoints contain the complete replay rows alongside verdicts,
    so stripping only the judge field gives the exact evidence to rejudge.
    """
    rows = payload.get("results") or []
    if not isinstance(rows, list) or not rows:
        return []
    return [{key: value for key, value in row.items() if key != "judge"}
            for row in rows if isinstance(row, dict)]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:7011")
    parser.add_argument("--public-url", required=True)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--owner", default="sft_alex_creator")
    parser.add_argument("--target-endpoint-id", default=DEFAULT_TARGET_ENDPOINT)
    parser.add_argument("--target-model", default=DEFAULT_TARGET_MODEL)
    parser.add_argument(
        "--routing-experiment",
        choices=("baseline", "recent", "all", "recent_model_choice"),
        default=DEFAULT_ROUTING_EXPERIMENT,
        help="Exact 7011 routing runtime to audit (default: model-specific Odysseus runtime)",
    )
    parser.add_argument("--judge-endpoint-id", default=DEFAULT_JUDGE_ENDPOINT)
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument("--fallback-judge-model", default=DEFAULT_FALLBACK_JUDGE_MODEL)
    parser.add_argument("--skip-judge", action="store_true",
                        help="Checkpoint real replay evidence and return without external judging")
    parser.add_argument("--judge-replay", type=Path,
                        help="Judge an immutable replay artifact without replaying 7011")
    parser.add_argument("--resume-judge", type=Path,
                        help="Reuse completed verdicts from an atomic run checkpoint")
    parser.add_argument("--families", default="calendar,notes,email,search_browser,switching")
    parser.add_argument("--flows-per-family", type=int, default=1)
    parser.add_argument("--flows-file", type=Path,
                        help="Replay generated flows from a prior run/replay artifact")
    parser.add_argument("--flow-offset", type=int, default=0,
                        help="Skip this many matching flows from --flows-file")
    parser.add_argument("--flow-limit", type=int,
                        help="Replay at most this many matching flows")
    parser.add_argument("--per-family-limit", type=int,
                        help="Replay at most this many matching flows from each family")
    parser.add_argument("--prior-verdict", choices=("pass", "fail", "uncertain"),
                        help="From a prior run artifact, replay only this judged verdict")
    parser.add_argument(
        "--prior-owner",
        choices=("harness_routing", "harness_execution", "harness_rendering", "model_sft", "backend_data", "none"),
        help="From a prior run artifact, replay only this judged owner",
    )
    parser.add_argument("--transport-only", action="store_true",
                        help="From a prior replay/run artifact, replay only flows with 7011 transport failures")
    parser.add_argument("--exclude-audited", action="store_true",
                        help="Skip source seeds already judged pass/fail for this target model")
    parser.add_argument(
        "--read-only-only", action="store_true",
        help="Skip flows that may mutate the shared SFT fixture; safe for parallel audit waves",
    )
    parser.add_argument(
        "--fixture-isolation", action="store_true",
        help=(
            "Replay owner-scoped local mutations serially, restoring the fixture before and "
            "after every flow; external mutations remain excluded"
        ),
    )
    parser.add_argument(
        "--fixture-db", type=Path, default=DEFAULT_FIXTURE_DB,
        help="Live app.db whose fixture owner is snapshotted for --fixture-isolation",
    )
    parser.add_argument(
        "--mutations-only", action="store_true",
        help="With --fixture-isolation, select only genuinely mutating flows",
    )
    parser.add_argument("--coverage-corpus", type=Path, default=DEFAULT_COVERAGE_CORPUS,
                        help="Canonical cooked corpus used for unique-seed coverage accounting")
    parser.add_argument("--coverage-manifest", type=Path,
                        help="Coverage JSON path (defaults to OUT_DIR/coverage.json)")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--judge-workers", type=int,
        help="DeepSeek/Kimi grading concurrency (defaults to --workers)",
    )
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.read_only_only and args.fixture_isolation:
        raise SystemExit("choose either --read-only-only or --fixture-isolation, not both")
    if args.mutations_only and not args.fixture_isolation:
        raise SystemExit("--mutations-only requires --fixture-isolation")
    if args.fixture_isolation and not args.fixture_db.is_file():
        raise SystemExit(f"fixture database does not exist: {args.fixture_db}")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    lock_path = args.out_dir / ".conversation-qa.lock"
    lock_handle = lock_path.open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(
            f"another conversation QA run owns {lock_path}; inspect it instead of overlapping endpoint load"
        )
    families = [x.strip() for x in args.families.split(",") if x.strip()]
    unknown = sorted(set(families) - set(FAMILY_SEEDS))
    if unknown:
        raise SystemExit(f"unknown families: {', '.join(unknown)}")
    teacher = endpoint_from_db(args.data_dir, args.judge_endpoint_id, args.judge_model)
    fallback_judge = TeacherEndpoint(
        teacher.base_url, teacher.api_key, args.fallback_judge_model,
    ) if args.fallback_judge_model else None
    audited_before = audited_source_seed_ids(
        args.out_dir, args.target_model, args.routing_experiment,
    )
    stamp = time.strftime("%Y%m%d-%H%M%S")
    resume_payload: dict[str, Any] = {}
    if args.resume_judge and args.resume_judge.exists():
        resume_payload = json.loads(args.resume_judge.read_text(encoding="utf-8"))
    if args.judge_replay:
        replay_payload = json.loads(args.judge_replay.read_text(encoding="utf-8"))
        replayed = replay_payload.get("flows") or []
        replay_artifact = args.judge_replay
        if not isinstance(replayed, list) or not replayed:
            raise SystemExit(f"no replay flows found in {args.judge_replay}")
    elif args.resume_judge:
        replayed = resume_replay_rows(resume_payload)
        replay_artifact = args.resume_judge
        if not replayed:
            raise SystemExit(
                f"no replay evidence found in resume checkpoint {args.resume_judge}; "
                "pass --judge-replay with its immutable replay artifact"
            )
    else:
        cookie = latest_cookie(args.data_dir, args.owner)
        flows = (flows_from_file(
            args.flows_file, families, prior_verdict=args.prior_verdict,
            prior_owner=args.prior_owner,
            transport_only=args.transport_only,
        ) if args.flows_file
                 else generate_flows(teacher, families, args.flows_per_family))
        if args.exclude_audited:
            flows = [flow for flow in flows if source_seed_id(flow) not in audited_before]
        flows = [flow for flow in flows if flow_is_auditable(flow)]
        if args.mutations_only:
            flows = [flow for flow in flows if flow_may_mutate(flow)]
        skipped_unsafe_mutations = 0
        if args.read_only_only:
            flows = [flow for flow in flows if not flow_may_mutate(flow)]
        elif args.fixture_isolation:
            unsafe = [
                flow for flow in flows
                if flow_may_mutate(flow) and not flow_has_locally_restorable_mutation(flow)
            ]
            skipped_unsafe_mutations = len(unsafe)
            flows = [
                flow for flow in flows
                if not flow_may_mutate(flow) or flow_has_locally_restorable_mutation(flow)
            ]
        else:
            mutating = [flow for flow in flows if flow_may_mutate(flow)]
            if mutating:
                raise SystemExit(
                    f"{len(mutating)} selected flow(s) may mutate durable state; use "
                    "--read-only-only or --fixture-isolation"
                )
        flows = balanced_flows(flows, args.per_family_limit)
        if args.flow_offset:
            flows = flows[args.flow_offset:]
        if args.flow_limit is not None:
            flows = flows[:args.flow_limit]
        if not flows:
            raise SystemExit("no flows remain after offset/limit selection")
        if args.fixture_isolation:
            replayed = replay_flows_with_fixture_isolation(flows, args, cookie)
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
                replayed = list(pool.map(lambda flow: replay_flow(flow, args, cookie), flows))
        args.out_dir.mkdir(parents=True, exist_ok=True)
        replay_artifact = args.out_dir / f"replay-{stamp}.json"
        atomic_write_json(replay_artifact, {
            "created_at": stamp, "target_model": args.target_model,
            "routing_experiment": args.routing_experiment,
            "fixture_isolation": bool(args.fixture_isolation),
            "skipped_unsafe_mutations": skipped_unsafe_mutations,
            "flows": replayed,
        })
    if args.skip_judge:
        print(json.dumps({
            "replay_artifact": str(replay_artifact), "flows": len(replayed),
            "sessions": [result["url"] for result in replayed],
        }, ensure_ascii=False, indent=2))
        return 0
    artifact = args.resume_judge or (args.out_dir / f"run-{stamp}.json")
    resume_results: list[dict[str, Any]] = []
    if resume_payload:
        resume_results = resume_payload.get("results") or []
    results = judge_replayed_flows(
        replayed, teacher, fallback_judge,
        workers=args.judge_workers or args.workers,
        checkpoint=artifact, target_model=args.target_model,
        judge_model=args.judge_model, routing_experiment=args.routing_experiment,
        resume_results=resume_results,
    )
    atomic_write_json(artifact, {
        "created_at": stamp, "target_model": args.target_model,
        "routing_experiment": args.routing_experiment,
        "judge_model": args.judge_model, "complete": True,
        "judged": len(results), "total": len(results), "results": results,
    })
    # A resume updates the same raw checkpoint. Re-appending every previously
    # completed verdict would duplicate an entire run in the compact ledger.
    if not args.resume_judge:
        append_ledger(args.ledger, stamp, results)
    audited_after = audited_before | {
        source_seed_id(result) for result in results
        if source_seed_id(result) and result.get("judge", {}).get("verdict") in {"pass", "fail"}
    }
    coverage = None
    if args.coverage_corpus.exists():
        coverage_flows = flows_from_file(args.coverage_corpus, FAMILY_SEEDS)
        coverage_flows = [
            flow for flow in coverage_flows
            if flow_is_auditable(flow)
        ]
        coverage = write_coverage_manifest(
            args.coverage_manifest or (args.out_dir / "coverage.json"),
            coverage_flows, audited_ids=audited_after, target_model=args.target_model,
            routing_experiment=args.routing_experiment,
        )
    failures = [r for r in results if r["judge"]["verdict"] != "pass"]
    print(json.dumps({
        "artifact": str(artifact), "replay_artifact": str(replay_artifact),
        "ledger": str(args.ledger),
        "flows": len(results), "passed": len(results) - len(failures),
        "flagged": len(failures),
        "coverage": coverage,
        "findings": [{
            "family": r["family"], "url": r["url"], **r["judge"],
        } for r in failures],
    }, ensure_ascii=False, indent=2))
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
