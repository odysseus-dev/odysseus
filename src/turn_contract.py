"""One request-scoped authority for product tool selection and execution.

Selection is a routing decision. Denial is a permission decision. Neither the
model nor recovery code may turn a selection into a new permission grant.
"""
from __future__ import annotations

import json
import re
from contextlib import aclosing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from functools import wraps
from inspect import signature
from types import MappingProxyType
from typing import Iterable, Mapping

from src.action_intents import classify_tool_intent
from src.tool_policy import ToolPolicy
from src.text_scanning import has_prefixed_token_match


FAMILY_TOOLS = {
    "calendar": frozenset({"manage_calendar"}),
    "notes": frozenset({"manage_notes"}),
    "tasks": frozenset({"manage_tasks"}),
    "skills": frozenset({"manage_skills"}),
    "memory": frozenset({"manage_memory", "search_chats"}),
    "documents": frozenset({"manage_documents", "create_document", "edit_document", "update_document", "suggest_document"}),
    "email": frozenset({"list_email_accounts", "list_emails", "search_emails", "read_email", "download_attachment", "scan_email_unsubscribes", "scan_spam", "unsubscribe_email", "send_email", "reply_to_email", "draft_email", "draft_email_reply", "ai_draft_email_reply", "bulk_email", "block_sender", "manage_email_state", "archive_email", "delete_email", "mark_email_read", "resolve_contact", "manage_contact"}),
    "search_browser": frozenset({"web_search", "web_fetch", "get_weather", "private_browser", "youtube_tool", "search_hf_models", "pdf_extract"}),
    "shell_files": frozenset({"bash", "python", "host_shell", "read_file", "write_file", "edit_file", "apply_patch", "grep", "glob", "ls", "get_workspace", "manage_bg_jobs", "inspect_media", "extract_text", "transcribe_media"}),
    "cookbook_admin": frozenset({"download_model", "serve_model", "serve_preset", "list_serve_presets", "list_served_models", "stop_served_model", "tail_serve_output", "list_downloads", "cancel_download", "list_cached_models", "list_cookbook_servers", "adopt_served_model", "list_models", "manage_settings", "manage_endpoints", "manage_mcp", "manage_webhooks", "manage_tokens", "api_call", "app_api", "list_sessions", "manage_session", "create_session", "send_to_session", "chat_with_model", "ask_teacher"}),
    "ui": frozenset({"ui_control"}),
    "research": frozenset({"trigger_research", "manage_research"}),
    "contacts": frozenset({"resolve_contact", "manage_contact"}),
    "sessions": frozenset({"list_sessions", "manage_session", "create_session", "send_to_session", "chat_with_model", "pipeline"}),
    "image_generation": frozenset({"generate_image"}),
    "image_editing": frozenset({"edit_image"}),
    "transcription": frozenset({"transcribe_media"}),
    "media_inspection": frozenset({"inspect_media"}),
    "ocr": frozenset({"extract_text"}),
}

# Retrieval is selection, not authorization. A normal agent turn must retain
# this small recovery surface when no family was recognized; explicit family
# selections and policy denials remain narrower.
CONTRACT_CORE_TOOLS = frozenset({
    "bash", "python", "read_file", "web_search", "web_fetch", "ask_user",
})

_WORKSPACE_PREFIX_RE = re.compile(r"/workspace/", re.I)
_WORKSPACE_ARTIFACT_RE = re.compile(
    r"/workspace/[^\s`\"']+\.(?:csv|html?|json|md|svg|txt)\b", re.I
)
_WORKSPACE_OUTPUT_PREFIX_RE = re.compile(r"/workspace/(?!input/)", re.I)
_WORKSPACE_OUTPUT_RE = re.compile(
    r"/workspace/(?!input/)[^\s`\"']+\."
    r"(?:csv|html?|json|md|svg|txt|avif|bmp|gif|jpe?g|png|webp|pdf|mp4|webm)\b",
    re.I,
)
_WORKSPACE_TOKEN_TAIL_RE = re.compile(r"[^\s`\"']*")


def _mentions_workspace_artifact(text: str) -> bool:
    return has_prefixed_token_match(
        str(text or ""),
        _WORKSPACE_PREFIX_RE,
        _WORKSPACE_ARTIFACT_RE,
        _WORKSPACE_TOKEN_TAIL_RE,
    )


def _mentions_workspace_output(text: str) -> bool:
    return has_prefixed_token_match(
        str(text or ""),
        _WORKSPACE_OUTPUT_PREFIX_RE,
        _WORKSPACE_OUTPUT_RE,
        _WORKSPACE_TOKEN_TAIL_RE,
    )


def _mentions_under_budget(text: str) -> bool:
    value = str(text or "")
    for under in re.finditer(r"\bunder", value, re.I):
        cursor = under.end()
        if cursor >= len(value) or not value[cursor].isspace():
            continue
        while cursor < len(value) and value[cursor].isspace():
            cursor += 1
        if cursor < len(value) and value[cursor] in "¥$€£":
            cursor += 1
            while cursor < len(value) and value[cursor].isspace():
                cursor += 1
        digit_start = cursor
        while cursor < len(value) and value[cursor].isdecimal():
            cursor += 1
        if cursor == digit_start:
            continue
        if cursor == len(value) or not (value[cursor].isalnum() or value[cursor] == "_"):
            return True
        if value[cursor:cursor + 3].casefold() == "yen":
            cursor += 3
            if cursor == len(value) or not (value[cursor].isalnum() or value[cursor] == "_"):
                return True
    return False
_FAMILY_WORDS = {
    "calendar": r"\b(?:calendar|calender|events?|appointments?|meetings?|agenda)\b",
    "notes": r"\b(?:notes?|checklists?|groceries|remind\s+me)\b",
    "tasks": r"\b(?:tasks?|todos?|schedul(?:ed|d)\s+jobs?|automations?)\b",
    "skills": r"\bskills?\b",
    "memory": r"\b(?:memory|memories|memores|remember|forget|past\s+chats?|previous\s+conversations?)\b",
    "documents": r"\b(?:documents?|documets?|docs?|editor)\b",
    "email": r"\b(?:emails?|inbox|mailbox|mail|spam)\b",
    "search_browser": r"\b(?:search\s+(?:the\s+)?web|web|online|browse|browser|websites?|sites?|news|weather|youtube|arxiv|hugging\s*face)\b|https?://|\b\w+\.(?:com|org|net|io)\b",
    "shell_files": r"\b(?:files?|folders?|directory|shell|terminal|workspace|repo|repository|python|hostname|b?ssh|bash)\b",
    "cookbook_admin": r"\b(?:cookbo{1,2}k|endpoints?|models?|servers?|settings|integrations?)\b",
    "research": r"\bresearch\b",
    "contacts": r"\bcontacts?\b",
    "sessions": r"\b(?:sessions?|chats?|conversations?)\b",
    "ui": r"\b(?:panels?|themes?|toggles?|sidebar)\b",
    "ocr": r"\b(?:ocr|extract|read|recognize|transcribe)\b.{0,32}\b(?:text|words?|labels?|numbers?|digits?|screenshot|scan|image)\b|文字|文本|字幕|编号|数字|标签|票据",
}
_FUZZY_FAMILY_TERMS = {
    "calendar": ("calendar", "event", "meeting", "appointment", "agenda"),
    "notes": ("note", "notes", "checklist", "groceries"),
    "tasks": ("task", "tasks", "todo", "reminder"),
    "skills": ("skill", "skills"),
    "memory": ("memory", "memories", "remember", "forget"),
    "documents": ("document", "documents", "editor"),
    "email": ("email", "emails", "inbox", "mailbox"),
    "search_browser": ("search", "browser", "website", "youtube"),
    "shell_files": ("file", "files", "folder", "directory", "shell", "terminal", "workspace", "python", "bash"),
    "cookbook_admin": ("cookbook", "endpoint", "settings", "download"),
}

_REQUEST_PREFIX = (
    r"(?:(?:please|ok(?:ay)?|cool|nice|great|cheers|also|then|now|yes|yeah|sure|actually|only|go\s+ahead)[\s,!—–:-]+)*"
    r"(?:(?:can|could|would|will)\s+you\s+)?"
    r"(?:(?:i\s+(?:want|need)\s+you\s+to|i(?:['’]d|\s+would)\s+like\s+you\s+to)\s+)?"
)
_ACTION_REQUEST = _REQUEST_PREFIX + (
    r"(?:add|create|make|write|draft|edit|rewrite|shorten|revise|change|update|"
    r"replace|append|polish|fix|review|proofread|suggest|delete|remove|cancel|list|show|check|find|search|navigate|read|open|save|publish|set|put|schedule|"
    r"reschedule|move|block\s+off|reserve|send|reply|remember|forget|run|rerun|repeat|do|use|download|"
    r"serve|stop|enable|disable|switch|research|investigate|generate|upscale|transcribe|inspect|browse)\b"
)
_ACTION = re.compile(r"^\s*" + _ACTION_REQUEST, re.I)
_CONVERSATIONAL_ACTION_LEAD = re.compile(
    r"^\s*(?:hey|hi|hiya|hello)[,!]?\s+"
    r"(?:quick\s+(?:one|question)\s*[—–:,-]\s*)?"
    r"(?P<request>" + _ACTION_REQUEST + r"[\s\S]*)$",
    re.I,
)


def editor_request_instructions(value: str) -> str:
    """Exclude writing-menu source blocks from routing, not from model context.

    These labelled blocks carry the selected prose or saved writing style. Their
    nouns and imperative sentences are data, not additional tool requests.
    Preserve instructions outside the blocks, including any trailing request.
    """
    return re.sub(
        r"(?:Selected passage:|Use this configured writing style as the source of truth:)"
        r"[ \t]*\r?\n---[ \t]*\r?\n[\s\S]*?\r?\n---(?=\r?\n|$)",
        "[editor content supplied]",
        str(value or ""),
    ).strip()


def _normalize_request_lead(value: str) -> str:
    """Remove harmless conversational wrappers before intent classification."""
    text = editor_request_instructions(value)
    text = re.sub(r"^(?:thx|thank\s+you)\s*[,!]\s+(?=\S)", "", text, flags=re.I)
    text = re.sub(
        r"^thanks?\s*[,!]\s+(?=(?:do|repeat|show|list|read|open|find|search|check)\b)",
        "",
        text,
        flags=re.I,
    )
    text = _strip_cancelled_request_lead(text)
    text = re.sub(r"^k(?:ay)?\s*[,!]?\s+(?=\S)", "", text, flags=re.I)
    text = re.sub(
        r"^(?:(?:great|nice|cool)\s*[,!.]|thanks?\s*[.!])\s+"
        r"(?=(?:now\s+)?\S)",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(r"^ok(?:ay)?\s+thanks?\s*[,!:-]?\s+", "", text, flags=re.I)
    text = re.sub(r"^(?:fine|alright|all\s+right)\s*[,!:-]?\s+", "", text, flags=re.I)
    text = re.sub(
        r"^while\s+(?:you(?:['’]?re|\s+are))\s+at\s+it\s*[,;:-]\s*",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"^while\s+your\s+at\s+it\s*[,;:-]?\s*",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(r"^(?:hey|hiya|hello)[,!]?\s+", "", text, flags=re.I)
    text = re.sub(
        r"^quick\s+(?:one|thing|check|question|lookup)\s*[,!:—–-]*\s*",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(r"^quick\s*[,!:—–-]+\s*", "", text, flags=re.I)
    text = re.sub(r"^quick(?:ly)?\s+(?=(?:search|list|show|check|find|read|open)\b)", "", text, flags=re.I)
    text = re.sub(r"^((?:can|could|would|will)\s+)u\b", r"\1you", text, flags=re.I)
    text = re.sub(r"\boffical\b", "official", text, flags=re.I)
    return text


def _strip_cancelled_request_lead(text: str) -> str:
    lead = re.match(r"^(?:never\s*mind|scratch\s+that)", text, re.I)
    if lead is None:
        return text
    cursor = lead.end()
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    if cursor < len(text) and text[cursor] in ",;:—–-":
        cursor += 1
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    action = re.match(r"(?:open|show|list|read|search|find|check|switch|go)\b", text[cursor:], re.I)
    return text[cursor:] if action is not None else text
_MISSPELLED_RESEARCH_ACTION = re.compile(
    r"^\s*" + _REQUEST_PREFIX + r"(?:reserch|reasearch|reseach)\b",
    re.I,
)
_ORDINAL_EMAIL_FOLLOWUP = re.compile(
    _REQUEST_PREFIX
    + r"(?:read|open|show|summarize)\s+(?:the\s+)?(?P<ordinal>"
      r"first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|"
      r"[1-9]\d*(?:st|nd|rd|th))\s+(?:email|message)\s+from\s+"
      r"(?:the\s+)?(?:earlier|previous|last)\s+(?:(?:inbox|email)\s+)?list"
      r"(?:\s+and\s+summarize\s+it)?[.!?]*",
    re.I,
)
_ORDINAL_SKILL_FOLLOWUP = re.compile(
    _REQUEST_PREFIX
    + r"(?:read|open|show|view)\s+(?:the\s+)?(?P<ordinal>"
      r"first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|"
      r"[1-9]\d*(?:st|nd|rd|th))\s+(?:"
      r"skill\s+from\s+(?:(?:that|the|an?)\s+)?(?:earlier|previous|last)?\s*"
      r"(?:skill\s+)?list(?:\s+and\s+summarize\s+it)?|"
      r"one(?:\s*[—–:,-]\s*)?(?:what(?:['’]?s|\s+is)\s+its\s+"
      r"(?:procedure|instructions?|details?))?"
      r")[.!?]*",
    re.I,
)
_PURE_ACTION_PROHIBITION = re.compile(
    r"^\s*(?:read[- ]only(?:\s+and)?\s+)?(?:do\s+not|don['’]?t|never)\s+"
    r"(?:add|create|make|write|draft|edit|change|update|delete|remove|send|reply|"
    r"run|execute|download|serve|open|save|schedule|transcribe|inspect)\b"
    r"[^.;\n]*[.!?]*\s*$",
    re.I,
)


def _is_pure_action_prohibition(text: str) -> bool:
    value = str(text or "").strip()
    prefix = re.match(
        r"(?:read[- ]only(?:\s+and)?\s+)?(?:do\s+not|don['’]?t|never)\s+"
        r"(?:add|create|make|write|draft|edit|change|update|delete|remove|send|reply|"
        r"run|execute|download|serve|open|save|schedule|transcribe|inspect)\b",
        value,
        re.I,
    )
    if prefix is None:
        return False
    tail = value[prefix.end():].rstrip(".!?")
    return not any(char in ".;\n" for char in tail)
_RETURN_TO_ACTION = re.compile(r"^\s*" + _REQUEST_PREFIX + r"return\s+to\b", re.I)
_PANEL_NAVIGATION = re.compile(
    r"^\s*" + _REQUEST_PREFIX
    + r"(?:(?:go\s+back\s+(?:and\s+)?)?open(?:\s+up)?|return\s+to)\s+"
      r"(?:me\s+)?(?:my\s+|the\s+)?"
      r"(?:calendar|schedule|documents?|docs?|library|gallery|images?|emails?|inbox|mail|"
      r"sessions?|chats?|history|notes?|brain|memor(?:y|ies)|skills?|settings|preferences|"
      r"themes?|appearance|cookbook|models?|serv(?:e|ing))"
      r"(?:\s+(?:panel|sidebar|tab|view))?"
      r"(?:\s+(?:again|now|instead))?[.!?]*\s*$",
    re.I,
)
_PANEL_POP_NAVIGATION = re.compile(
    r"\bpop\s+(?:(?:open|up)\s+)?(?:the\s+)?"
    r"(?:calendar|schedule|documents?|docs?|library|gallery|images?|emails?|inbox|mail|"
    r"sessions?|chats?|history|notes?|brain|memor(?:y|ies)|skills?|settings|preferences|"
    r"themes?|appearance|cookbook|models?|serv(?:e|ing))\s+"
    r"(?:(?:panel|sidebar|tab)\s+)?open\b|"
    r"\bpop\s+open\s+(?:the\s+)?"
    r"(?:calendar|schedule|documents?|docs?|library|gallery|images?|emails?|inbox|mail|"
    r"sessions?|chats?|history|notes?|brain|memor(?:y|ies)|skills?|settings|preferences|"
    r"themes?|appearance|cookbook|models?|serv(?:e|ing))\s+(?:panel|sidebar|tab)\b",
    re.I,
)
_THEME_CHANGE = re.compile(
    r"\b(?:set|switch|change|put|go)\b[^.;\n]{0,80}\b(?:dark|light)\b"
    r"(?:\s+(?:theme|mode))?|\b(?:dark|light)\s+(?:theme|mode)\b",
    re.I,
)
_PANEL_CONTROLS_NAVIGATION = re.compile(
    r"\b(?:pull|bring|open|show)\s+(?:up\s+)?(?:the\s+)?"
    r"(?:theme|appearance|settings?|preferences?)\s+(?:controls?|panel|sidebar|tab)\b",
    re.I,
)
_CONTEXTUAL_UI_VIEW_CHANGE = re.compile(
    r"\b(?:flip|swi(?:t)?ch|change)\s+(?:it|this|that)\s+(?:over\s+)?to\s+(?:the\s+)?"
    r"(?:models?|calendar|notes?|documents?|gallery|images?|email|inbox|cookbook|settings?)\s+"
    r"(?:view|panel)\b",
    re.I,
)
_CONTEXTUAL_ACTION = re.compile(
    r"^\s*(?:in|on|for)\s+(?:this|that|the)\s+"
    r"(?:document|doc|note|task|event|calendar|memory|skill|email)\b"
    r"[^.;\n]{0,80}?\b(?:add|create|write|draft|edit|rewrite|shorten|revise|change|"
    r"update|replace|append|polish|fix|delete|remove|cancel|save|schedule|"
    r"reschedule|move|send|reply|remember|forget)\b",
    re.I,
)
_CONDITIONAL_ACTION = re.compile(
    r"^\s*(?:if|when|once|since|given|with|assuming|provided)\b[\s\S]{0,240}?"
    r"(?:,\s*|\bthen\s+)(?P<action>" + _ACTION_REQUEST + r"[\s\S]*)$",
    re.I,
)
_EXPLICIT_URL_RETRIEVAL = re.compile(
    r"\b(?:read|visit|open|browse|fetch|download|inspect|extract)\b"
    r"[\s\S]{0,320}?https?://",
    re.I,
)
_NAMED_EXTERNAL_DOCUMENT_RETRIEVAL = re.compile(
    r"\b(?:from|using|based\s+on)\s+(?:the\s+)?(?:paper|report|study)\b"
    r"[\s\S]{0,1200}?\b(?:tables?|figures?)\s*\d+",
    re.I,
)
_LOCAL_PDF_REFERENCE = re.compile(
    r"(?:^|\s)(?:file://)?/workspace/[^\s`\"']+\.pdf\b",
    re.I,
)
_SHELL_COMMAND_SEQUENCE = re.compile(
    r"\b(?:echo|printf)\b[\s\S]{0,180}\b(?:cat|head|tail)\s+"
    r"/(?:etc|proc|sys)/[^\s`\"']+",
    re.I,
)
_EXPLICIT_INLINE_SHELL_COMMAND = re.compile(
    r"\b(?:run|execute)\b[^.;\n]{0,100}\b(?:read[- ]only\s+)?command\b"
    r"[^\n]{0,80}?(?::|`)\s*(?:printf|echo|pwd|whoami|uname|date|true|false|test)\b",
    re.I,
)
_LOOKUP = re.compile(
    r"^\s*(?:(?:what(?:['’]?s|\s+is|\s+are)|which|where(?:['’]?s|\s+is|\s+are))"
    r"\s+(?:my|our|the|today['’]?s)\b|what\s+(?:does|did)\s+(?:my|our|the|this|that)\b|"
    r"what\s+about\s+(?:(?:my|our|the)\s+)?\b|"
    r"(?:is|are)\s+there\s+(?:an?\s+)?(?:calendar\s+(?:thing|entry)|any\s+"
    r"(?:emails?|mail|events?|notes?|tasks?|documents?|files?))\b|"
    r"any\s+(?:emails?|mail|events?|notes?|tasks?|documents?|files?)\b|"
    r"(?:do\s+i\s+have|have\s+i\s+got)\b)",
    re.I,
)
_PERSONAL_STORE_LOOKUP = re.compile(
    r"^\s*(?:please\s+)?look\s+(?:(?:in|at|through)\s+)?(?:(?:my|our|the)\s+)?"
    r"(?:emails?|mail|inbox|notes?|documents?|calendar|memories|tasks?)\b|"
    r"^\s*(?:what|which|where|when|how\s+many)\b[\s\S]{0,180}?"
    r"(?:\b(?:my|our)\b|\bdo\s+(?:i|we)\s+have\b|\b(?:is|are)\s+saved\b)|"
    r"^\s*(?:does?|is|are)\s+any\s+"
    r"(?:notes?|documents?|docs?|memories|tasks?|skills?|emails?|events?)\b"
    r"[\s\S]{0,180}\b(?:mention|contain|match|have|include)\b",
    re.I,
)
# Treat "schedule" as a calendar noun only when the wording makes that
# meaning explicit.  Keeping it out of _FAMILY_WORDS avoids conflating
# calendar lookups with task phrases such as "scheduled tasks/jobs".
_PERSONAL_CALENDAR_SCHEDULE = re.compile(
    r"\b(?:(?:my|our|the)\s+schedule|schedule\s+(?:for\s+)?"
    r"(?:today|tomorrow|this\s+(?:week|month)|next\s+(?:week|month)))\b",
    re.I,
)
_REFERENCE = re.compile(
    r"\b(?:it|its|this|that|them|em|their|those|these|again|same|another|first|second)\b|"
    r"\b(?:which|that|this|the)\s+one\b",
    re.I,
)
_CONVERSATIONAL_FOLLOWUP = re.compile(
    r"^\s*" + _REQUEST_PREFIX +
    r"(?:reply|respond|answer|open|read|show|summarize|suggest|archive|unarchive|"
    r"block|unblock|mark|move|delete|remove|edit|update|change|send|do|"
    r"tell\s+me\s+more(?:\s+about)?|more\s+about|the\s+attachment|"
    r"what\s+else(?:\s+did\s+(?:it|this|that)\s+say)?|"
    r"what\s+(?:did|does)\s+(?:it|this|that)\s+say|"
    r"this|that|it|them|those|these)\b",
    re.I,
)
_CONTEXTUAL_STATE_LOOKUP = re.compile(
    r"^\s*(?:what(?:['’]?s|\s+is)\s+(?:scheduled|coming\s+up)|"
    r"do\s+(?:i|we)\s+have\s+anything|did\s+(?:i|we)\s+(?:already\s+)?put\s+anything|"
    r"anything\s+(?:on|in)\s+(?:there|here))\b",
    re.I,
)
_CONTEXTUAL_RESULT_LOOKUP = re.compile(
    r"^\s*(?:what(?:['’]?s|\s+is)?\s+(?:actually\s+)?(?:in|inside|about)\s+|"
    r"which\s+(?:of\s+)?)(?:the\s+)?(?:it|that|there|those|these|top|first|second|last|newest|oldest)\b",
    re.I,
)
_CONTEXTUAL_WEB_EVIDENCE = re.compile(
    r"^\s*(?:is\s+there\s+)?anything\s+(?:in\s+there\s+)?about\b|"
    r"^\s*(?:is\s+there\s+)?anything\s+(?:new|recent|latest)\s+"
    r"(?:on|about)\b[^?!.]{1,160}\b(?:there|that|it)\b|"
    r"^\s*where\s+did\s+you\s+get\s+(?:it|that|this)\s+from\b|"
    r"^\s*(?:please\s+)?double[- ]check\b[^.;\n]{0,180}\b(?:official|source|docs?|blog)\b|"
    r"^\s*(?:give|show|send)\s+me\s+(?:the\s+)?(?:source|link|url)\b",
    re.I,
)
_CONTEXTUAL_COLLECTION_FILTER = re.compile(
    r"^\s*(?:(?:is|was)\s+there\s+(?:one|any|anything)(?:\s+in\s+(?:there|them))?\s+"
    r"(?:with|about|mention(?:ing)?|for)|any\s+of\s+them\s+"
    r"(?:with|about|mention(?:ing)?|for)|got\s+anything(?:\s+more\s+detail(?:e)?d)?\s+"
    r"(?:with|about|mention(?:ing)?|for)|(?:is|was)\s+there\s+an?\s+[^?!.]{1,80}?"
    r"\s+one\s+in\s+there|(?:now\s+)?(?:just\s+)?search\b[^?!.]{0,80}"
    r"\banything\s+in\s+there\s+(?:with|about|mention(?:ing)?|for)|"
    r"(?:also\s+)?search\s+(?:my|the)\s+(?:reports?|items?|results?|entries?)\s+"
    r"(?:with|about|mention(?:ing)?|for))\b",
    re.I,
)
_CONTEXTUAL_ITEM_DETAIL = re.compile(
    r"^\s*(?:please\s+)?tell\s+me\s+(?:more\s+)?(?:about\s+)?what\s+"
    r"(?:the\s+)?(?:first|second|last|top|that|this)\s+(?:one\s+)?(?:does|is|contains?)\b|"
    r"^\s*(?:please\s+)?tell\s+me\s+more\s+about\s+(?:it|that|this|the\s+(?:first|second|last|top)\s+one)\b",
    re.I,
)
_REFERENTIAL_FOLLOWUP_QUESTION = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|cool|thanks?|nice)[,!]?\s+)?(?:"
    r"pull\b[\s\S]{0,180}\bup\b|"
    r"(?:(?:do\s+not|don['’]?t|dont)\b[^,.;]{0,100}[,.;]\s*)?"
    r"(?:just\s+)?tell\s+me\s+(?:if|whether|who|what|which|when|where|how)\b|"
    r"(?:who(?:['’]?s|\s+is)?|what(?:['’]?s|s|\s+is)?|which|when|where|how(?:\s+(?:many|much))?|does?|did|is|are|"
    r"was|were|has|have|any(?:thing)?)\b|"
    r"the\s+(?:first|second|third|last|top)\s+one\b[\s\S]{0,160}"
    r"(?:[—–:,-]\s*)?(?:who|what|which|when|where|how|does?|is|are)\b"
    r")",
    re.I,
)
_CONTEXTUAL_CALENDAR_ACTION = re.compile(
    r"^\s*" + _REQUEST_PREFIX + r"(?:block(?:\s+off)?|reserve|move|reschedule)\b[\s\S]{0,180}"
    r"(?:\b(?:today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"morning|afternoon|evening)\b|\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b)",
    re.I,
)
_CONTEXTUAL_CALENDAR_LOOKUP = re.compile(
    r"\b(?:today|tomor{1,2}ow|tmrw|tonight|this\s+(?:week|weekend|month)|next\s+(?:week|month)|"
    r"mon(?:day)?|tue(?:s|sday)?|wed(?:s|nesday)?|thu(?:rs|rsday)?|fri(?:day)?|"
    r"sat(?:urday)?|sun(?:day)?|morning|afternoon|evening)\b",
    re.I,
)
_REFERENTIAL_TOOL_CONTINUATION = re.compile(
    r"^\s*" + _REQUEST_PREFIX + r"(?:"
    r"(?:search|find|show|list|read|open|pull\s+up|grab|fetch|extract|summarize|inspect|transcribe|"
    r"get|refresh|narrow|filter|sort|compare)\b[\s\S]{0,280}"
    r"|from\s+(?:it|this|that|the\s+same)\b[\s\S]{0,280}"
    r")$",
    re.I,
)
_EXTERNAL_WEB_VERIFICATION = re.compile(
    r"\b(?:verify|confirm|check|determine)\b.{0,240}"
    r"\b(?:official(?:ly)?|publication|published|accepted)\b.{0,160}"
    r"\b(?:as\s+of|current(?:ly)?|latest|today)\b",
    re.I | re.S,
)
_EDITOR_WRITE_VERB = (
    r"(?:write|draft|reply|respond|make|edit|rewrite|revise|shorten|expand|polish|fix|"
    r"broaden|deepen|lighten|review|proofread|suggest|update|change|replace|append|add|"
    r"improve|correct|clean\s+up|tighten|fact[ -]?check)"
)
_BOUND_EDITOR_WRITE = re.compile(
    r"^\s*" + _REQUEST_PREFIX + r"(?:"
    + _EDITOR_WRITE_VERB
    + r"|(?:in|on)\s+(?:(?:this|the|my)\s+)?(?:(?:current|open|active)\s+)?"
      r"(?:email(?:\s+(?:message|reply|draft))?|mail(?:\s+(?:message|reply|draft))?|"
      r"message|reply|draft|document|doc)\s*,?\s*"
    + _EDITOR_WRITE_VERB
    + r")\b",
    re.I,
)
_BOUND_EDITOR_IMPLICIT_REVISION = re.compile(
    r"^\s*" + _REQUEST_PREFIX + r"(?:broaden|expand|deepen|lighten|go\s+deeper|"
    r"clean\s+(?:this|it|the\s+(?:text|draft|document|doc))\s+up|"
    r"give\s+(?:me\s+)?(?:feedback|a\s+critique|suggestions?|comments?)|"
    r"remove\b[^.;\n]{0,100}\b(?:mistakes?|errors?|inaccurac(?:y|ies)|misinformation)|"
    r"(?:apply|make|do)\s+(?:(?:all|any)\s+)?(?:those|these|the)\s+"
    r"(?:fixes|changes|edits|revisions|suggestions?)|"
    r"go\s+ahead\s+with\s+(?:those|these|the)?\s*(?:fixes|changes|edits|revisions|suggestions?)|"
    r"work\s+on\s+(?:this|it|the\s+(?:text|draft|document|doc)))\b",
    re.I,
)
_BOUND_EDITOR_TRAILING_WRITE = re.compile(
    r"\b(?:and|then)\s+" + _EDITOR_WRITE_VERB
    + r"\s+(?:this|it|the\s+(?:text|draft|document|doc)|my\s+(?:text|draft|document|doc))\b",
    re.I,
)
_NEW_EDITOR_OBJECT = re.compile(
    r"\b(?:new|another|separate)\s+(?:email|mail|message|reply|draft|document|doc)\b",
    re.I,
)
_NON_EDITOR_WRITE_TARGET = re.compile(
    r"\b(?:notes?|checklists?|calendar|events?|appointments?|tasks?|todos?|skills?|"
    r"memories|memory|files?|folders?|python|javascript|typescript|bash|shell|scripts?|"
    r"functions?|images?|pictures?)\b",
    re.I,
)
_WARM_RECALL = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|and|then)\s+)?(?:back\s+to|return\s+to|"
    r"what\s+about|check|show|open)?\s*(?:my|the)?\s*"
    r"(?P<target>calendar|emails?|inbox|notes?|tasks?|skills?|memories|memory|"
    r"documents?|docs?|web|browser|cookbook|files?|shell)\s*(?:again|now)?[.!?]*\s*$",
    re.I,
)
_WARM_RECALL_WITH_FOLLOWUP = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|and|then)\s+)?(?:back\s+to|return\s+to|"
    r"what\s+about|check|show|open)\s+(?:my|the)?\s*"
    r"(?P<target>calendar|emails?|inbox|notes?|tasks?|skills?|memories|memory|"
    r"documents?|docs?|web|browser|cookbook|files?|shell)\b"
    r"(?:\s*(?:[-—,:;]|\b(?:and|then)\b)\s*|\s+)"
    r"(?P<followup>(?:what(?:['’]?s|\s+is)?|which|who|where|when|how|show|open|read|list|find|search)\b[\s\S]{0,180})$",
    re.I,
)

_WARM_TARGET_RE = re.compile(
    r"calendar|emails?|inbox|notes?|tasks?|skills?|memories|memory|"
    r"documents?|docs?|web|browser|cookbook|files?|shell",
    re.I,
)
_WARM_FOLLOWUP_RE = re.compile(
    r"(?:what(?:['’]?s|\s+is)?|which|who|where|when|how|show|open|read|list|find|search)"
    r"\b[\s\S]{0,180}\Z",
    re.I,
)


def _consume_space(value: str, cursor: int, *, required: bool = False) -> int | None:
    start = cursor
    while cursor < len(value) and value[cursor].isspace():
        cursor += 1
    return None if required and cursor == start else cursor


def _phrase_end(value: str, cursor: int, phrase: str) -> int | None:
    for index, word in enumerate(phrase.split(" ")):
        if value[cursor:cursor + len(word)].casefold() != word:
            return None
        cursor += len(word)
        if index + 1 < len(phrase.split(" ")):
            cursor = _consume_space(value, cursor, required=True)
            if cursor is None:
                return None
    return cursor


def _warm_recall_parts(value: str, *, with_followup: bool = False) -> tuple[str, str] | None:
    value = str(value or "")
    cursor = _consume_space(value, 0) or 0
    states = [cursor]
    discourse = re.match(r"(?:ok(?:ay)?|and|then)", value[cursor:], re.I)
    if discourse is not None:
        after = _consume_space(value, cursor + discourse.end(), required=True)
        if after is not None:
            states.insert(0, after)

    action_phrases = ("back to", "return to", "what about", "check", "show", "open")
    action_states: list[int] = []
    for state in states:
        if not with_followup:
            action_states.append(state)
        for phrase in action_phrases:
            end = _phrase_end(value, state, phrase)
            if end is None:
                continue
            if with_followup:
                end = _consume_space(value, end, required=True)
                if end is None:
                    continue
            action_states.append(end)

    for state in dict.fromkeys(action_states):
        state = _consume_space(value, state) or 0
        possessive_states = [state]
        for possessive in ("my", "the"):
            if value[state:state + len(possessive)].casefold() == possessive:
                possessive_states.insert(0, state + len(possessive))
        for target_state in possessive_states:
            target_state = _consume_space(value, target_state) or 0
            target = _WARM_TARGET_RE.match(value, target_state)
            if target is None:
                continue
            target_text = target.group(0)
            suffix = target.end()
            if with_followup:
                if suffix < len(value) and (value[suffix].isalnum() or value[suffix] == "_"):
                    continue
                separator_states = []
                spaced = _consume_space(value, suffix) or 0
                if spaced > suffix:
                    separator_states.append(spaced)
                if spaced < len(value) and value[spaced] in "-—,:;":
                    separator_states.append(_consume_space(value, spaced + 1) or 0)
                for conjunction in ("and", "then"):
                    end = spaced + len(conjunction)
                    if (value[spaced:end].casefold() == conjunction
                            and (end == len(value) or not (value[end].isalnum() or value[end] == "_"))):
                        separator_states.append(_consume_space(value, end) or 0)
                for followup_start in dict.fromkeys(separator_states):
                    followup = _WARM_FOLLOWUP_RE.match(value, followup_start)
                    if followup is not None:
                        return target_text, followup.group(0)
                continue
            suffix = _consume_space(value, suffix) or 0
            suffix_states = [suffix]
            for word in ("again", "now"):
                if value[suffix:suffix + len(word)].casefold() == word:
                    suffix_states.insert(0, suffix + len(word))
            for end in suffix_states:
                while end < len(value) and value[end] in ".!?":
                    end += 1
                end = _consume_space(value, end) or 0
                if end == len(value):
                    return target_text, ""
    return None
_REQUIRED_TOOLS = {
    "calendar": "manage_calendar", "notes": "manage_notes",
    "tasks": "manage_tasks", "skills": "manage_skills",
    "image_generation": "generate_image", "image_editing": "edit_image",
    "transcription": "transcribe_media", "media_inspection": "inspect_media", "ocr": "extract_text",
}

# These capabilities have no action_intents category. Match explicit actions
# and supported media targets, not incidental image/audio words in prose.
# Prompt edits of prior generated images are resolved separately from history.
_MEDIA_REQUESTS = tuple(
    (family, re.compile(r"^\s*" + _REQUEST_PREFIX + pattern, re.I))
    for family, pattern in (
        ("image_generation", r"(?:generate|create|make)\s+(?:(?:me|us)\s+)?"
         r"(?:(?:an?|the|new)\s+)*(?:images?|pictures?|illustrations?)\b"),
        ("image_editing", r"upscale\b.{0,100}\b(?:images?|pictures?|photos?)\b"),
        ("image_editing", r"remove\s+(?:the\s+)?background\s+(?:from|of)\b"
         r".{0,100}\b(?:images?|pictures?|photos?)\b"),
        ("transcription", r"transcribe\b.{0,120}(?:\b(?:audio|video|recording|speech)\b"
         r"|\S+\.(?:wav|mp3|m4a|flac|ogg|mp4|webm|mov)\b)"),
        ("ocr", r"(?:(?:use\s+(?:local\s+)?)?ocr\b.{0,160}(?:\b(?:text|words?|labels?|numbers?|digits?|"
         r"image|screenshot|scan)\b|\S+\.(?:png|jpg|jpeg|webp|gif)\b)|"
         r"(?:extract|read|recognize)\b.{0,100}\b(?:exact\s+)?(?:visible\s+)?"
         r"(?:text|words?|labels?|numbers?|digits?)\b.{0,160}(?:\b(?:image|screenshot|scan)\b"
         r"|\S+\.(?:png|jpg|jpeg|webp|gif)\b))"),
        ("media_inspection", r"inspect\b.{0,120}(?:\b(?:images?|pictures?|photos?|video|pdf|svg)\b"
         r"|\S+\.(?:png|jpg|jpeg|webp|gif|svg|pdf|mp4|webm|mov)\b)"),
    )
)


def _damerau_distance(left: str, right: str) -> int:
    """Small unrestricted-enough edit metric for human trigger-word typos."""
    rows = [[0] * (len(right) + 1) for _ in range(len(left) + 1)]
    for i in range(len(left) + 1): rows[i][0] = i
    for j in range(len(right) + 1): rows[0][j] = j
    for i in range(1, len(left) + 1):
        for j in range(1, len(right) + 1):
            rows[i][j] = min(rows[i-1][j] + 1, rows[i][j-1] + 1,
                             rows[i-1][j-1] + (left[i-1] != right[j-1]))
            if i > 1 and j > 1 and left[i-1] == right[j-2] and left[i-2] == right[j-1]:
                rows[i][j] = min(rows[i][j], rows[i-2][j-2] + 1)
    return rows[-1][-1]


def _has_cookbook_server_reference(text: str) -> bool:
    """Recognize the named Cookbook server surface with a small human typo."""
    if not re.search(r"\bcookbo{1,2}k\b", str(text or ""), re.I):
        return False
    tokens = re.findall(r"[a-z]+", str(text or "").casefold())
    return any(
        len(token) >= 5
        and min(_damerau_distance(token, "server"), _damerau_distance(token, "servers")) <= 2
        for token in tokens
    )


def _fuzzy_family(text: str) -> str | None:
    """Resolve one unambiguous misspelled family noun, otherwise abstain."""
    tokens = re.findall(r"[a-z]+", text.lower())
    candidates = [(token, False) for token in tokens]
    candidates.extend((tokens[i] + tokens[i + 1], True) for i in range(len(tokens) - 1))
    hits: list[tuple[int, str]] = []
    for token, joined in candidates:
        if len(token) < 4:
            continue
        for family, terms in _FUZZY_FAMILY_TERMS.items():
            for term in terms:
                if term == "search" and not joined and token.endswith("search"):
                    continue
                distance = _damerau_distance(token, term)
                limit = 1 if max(len(token), len(term)) <= 6 else 2
                if joined and distance != 0:
                    continue
                if (distance == 0 and joined) or 0 < distance <= limit:
                    hits.append((distance, family))
    if not hits:
        return None
    best = min(distance for distance, _ in hits)
    families = {family for distance, family in hits if distance == best}
    return next(iter(families)) if len(families) == 1 else None


_ACTION_VERBS = frozenset({
    "add", "create", "make", "write", "draft", "edit", "rewrite", "shorten",
    "revise", "change", "update", "delete", "remove", "cancel", "list", "show",
    "check", "find", "search", "navigate", "read", "open", "save", "publish", "set", "schedule",
    "reschedule", "move", "block", "reserve", "send", "reply", "remember", "forget", "run", "repeat",
    "download", "serve", "stop", "enable", "disable", "switch", "put", "research", "rerun",
    "investigate", "generate", "upscale", "transcribe", "inspect", "browse",
    "review", "proofread", "suggest", "stick",
})


def calendar_retiming_request(text: str) -> bool:
    """Recognize an explicit temporal move of a named calendar object."""
    return bool(re.match(
        r'^\s*' + _REQUEST_PREFIX
        + r'(?:push|bring|postpone|delay|shift)\s+'
          r'(?:(?:my|our|the|this|that|an?)\s+)?'
          r'(?:event|meeting|appointment)\b[^.;!?\n]{0,100}'
          r'\b(?:by|until|to)\s+\S+',
        str(text or ''), re.I,
    ))


def _has_action_signal(text: str) -> bool:
    """Recognize a normal action prefix or one transposition/typo in its verb."""
    if (_ACTION.search(text) or _CONTEXTUAL_ACTION.search(text)
            or _RETURN_TO_ACTION.search(text) or calendar_retiming_request(text)):
        return True
    tokens = re.findall(r"[a-z]+", str(text or "").lower())[:6]
    while tokens and tokens[0] in {"please", "ok", "okay", "also", "then", "yes", "yeah", "sure"}:
        tokens.pop(0)
    if len(tokens) >= 3 and tokens[:2] in (["can", "you"], ["could", "you"], ["would", "you"], ["will", "you"]):
        tokens = tokens[2:]
    if (not tokens or len(tokens[0]) < 3
            or tokens[0] in {"how", "what", "when", "where", "which", "who", "why"}):
        return False
    # A missing letter in a four-letter verb is common ("lst", "shw"), but
    # accepting every nearby verb would turn ordinary prose into authority.
    # Require the first token to have one unique action-verb interpretation.
    matches = {
        verb for verb in _ACTION_VERBS
        if _damerau_distance(tokens[0], verb) == 1
    }
    return len(matches) == 1


def targets_bound_editor_request(message: str) -> bool:
    """Recognize a write to the visible editor without stealing explicit targets."""
    text = _normalize_request_lead(message)
    explicit_inline_review = (re.search(r'\b(?:open|active)\s+document\b', text, re.I)
                              and re.search(r'\binline\s+suggestions?\b', text, re.I))
    if (not (_BOUND_EDITOR_WRITE.search(text) or _BOUND_EDITOR_IMPLICIT_REVISION.search(text)
             or _BOUND_EDITOR_TRAILING_WRITE.search(text) or explicit_inline_review)
            or _NEW_EDITOR_OBJECT.search(text)):
        return False
    return not _NON_EDITOR_WRITE_TARGET.search(text)


def preserve_bound_editor_selected_tools(
    message: str,
    selected_tools: Iterable[str] | None,
    *,
    active_document: bool,
) -> set[str] | None:
    """Prevent an exact secondary lookup from erasing visible-editor writers.

    ``selected_tools_for_request`` can narrow a mixed request to a web lookup.
    When the browser has also bound a visible document and the same request
    asks to revise it, retain the writer family in that narrow selection. A
    ``None`` selection remains family-driven and needs no expansion here.
    """
    if selected_tools is None:
        return None
    selected = set(selected_tools)
    if active_document and targets_bound_editor_request(message):
        selected.update({"edit_document", "update_document", "suggest_document"})
    return selected


def _bound_editor_requests_web_verification(message: str) -> bool:
    """Keep evidence retrieval beside an edit when the user asks for both."""
    text = _normalize_request_lead(message)
    evidence = re.search(
        r"\b(?:web|online|sources?|citations?|references?|links?)\b",
        text,
        re.I,
    )
    verification = re.search(
        r"\b(?:fact[ -]?check|verify|check|research|misinformation|inaccurac(?:y|ies)|claims?)\b",
        text,
        re.I,
    )
    return bool(evidence and verification)


def requests_independent_web_source(message: str) -> bool:
    """Recognize an explicit request to corroborate with a different source."""
    text = _normalize_request_lead(message)
    return bool(
        re.search(r"\b(?:double[ -]?check|cross[ -]?check|verify|confirm)\b", text, re.I)
        and re.search(r"\b(?:proper|credible|reliable|another|different|second|other|independent)\s+source\b", text, re.I)
        and re.search(r"\b(?:link|url|source|citation|online|web)\b", text, re.I)
    )


def requests_supporting_web_source(message: str) -> bool:
    """Recognize a request to substantiate the preceding answer with a link."""
    text = _normalize_request_lead(message)
    return bool(
        re.search(r"\b(?:link|url|source|citation)\b", text, re.I)
        and re.search(
            r"\b(?:where\s+(?:does|did)\s+that\s+come\s+from|"
            r"source\s+(?:you|u)\s+(?:used|relied\s+on)|"
            r"link\s+(?:me\s+)?(?:the\s+)?source)\b",
            text,
            re.I,
        )
    )


def _explicit_email_attachment_read(message: str) -> tuple[str, int] | None:
    """Resolve an explicitly numbered message attachment, or a singular one."""
    text = _normalize_request_lead(message)
    command = re.fullmatch(
        _REQUEST_PREFIX
        + r"(?:open|read|download|show|pull\s+up)\s+(?:the\s+)?attachment\s+"
          r"(?P<index>\d+)\s+(?:on|from|in)\s+(?:the\s+)?"
          r"(?:(?:email|message)\s+)?(?:uid\s*)?"
          r"(?P<uid>[A-Za-z0-9._:@+\-]+)"
          r"(?:\s+and\s+(?:tell|show)\s+me\s+what\s+it\s+is)?[.!?]*",
        text,
        re.I,
    )
    if command:
        return command["uid"], int(command["index"])
    descriptive = re.search(
        r"\battached\s+to\s+(?:the\s+)?(?:email|message)\s+"
        r"(?:uid\s*)?(?P<uid>[A-Za-z0-9._:@+\-]+)",
        text,
        re.I,
    )
    if (
        descriptive
        and re.search(r"\b(?:read|open|download|summari[sz]e|inspect|tell\s+me)\b", text, re.I)
        and re.search(r"\b(?:attachment|attached|file|document|sample)\b", text, re.I)
    ):
        numbered = re.search(r"\battachment\s+(\d+)\b", text, re.I)
        return descriptive["uid"], int(numbered.group(1)) if numbered else 0
    return None


def inline_text_transformation(message: str) -> bool:
    """An explicit text-editing prefix makes the colon payload data, not a tool request.

    Do not match edits *in* an account/editor or compound instructions before
    the delimiter. Names of tools or personal objects inside supplied text do
    not grant authority to operate on those objects.
    """
    return bool(re.fullmatch(
        r'\s*(?:please\s+)?(?:'
        r'(?:fix|correct)\s+(?:only\s+)?(?:the\s+)?(?:spelling|grammar|typos)(?:\s+only)?'
        r'|proofread(?:\s+(?:this|the following)(?:\s+text)?)?'
        r'|translate\s+(?:this\s+)?(?:to|into)\s+[A-Za-z]+(?:\s+[A-Za-z]+)?'
        r')\s*:\s*\S[\s\S]*',
        str(message or ''), re.I,
    ))


def scheduled_automation_request(message: str) -> bool:
    """Recognize a leading cadence that schedules the following operation."""
    text = _normalize_request_lead(message)
    # A leading cadence scopes the following operation to future runs. The
    # operation's subject (email, news, documents) is not work to do now.
    return bool(re.match(
        r'^\s*' + _REQUEST_PREFIX
        + r'(?:(?:every|each)\s+(?:day|week|month|morning|evening|weekday|weekend|'
        r'monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?|daily|weekly|monthly)'
        r'(?:\s+at\s+\d{1,2}(?::\d{2})?(?:\s*(?:am|pm))?(?:\s+(?:UTC|GMT))?)?'
        r'\s*,?\s+(?:please\s+)?(?:research|summari[sz]e|review|check|audit|sync|'
        r'notify|remind|monitor|back\s+up)\s+\S', text, re.I,
    ))


def creation_container_tool(message: str) -> str | None:
    """The explicitly created container owns its content, not vice versa."""
    text = _normalize_request_lead(message)
    if scheduled_automation_request(text):
        return 'manage_tasks'
    match = re.match(
        r'^\s*' + _REQUEST_PREFIX
        + r'(?:add|create|write|save|make|set\s+up)\s+'
        r'(?:(?:a|an|the|my|new|quick|short|freeform|temporary|scheduled|recurring|'
        r'single|one|two|three|four|five|six|seven|eight|nine|ten|[1-9]\d*)\s+)*'
        r'(?P<container>to[ -]?dos?|checklists?|tasks?|automations?|scheduled\s+jobs?)\b',
        text, re.I,
    )
    if not match:
        return None
    container = match['container'].lower()
    return 'manage_tasks' if re.match(r'(?:task|automation|scheduled)', container) else 'manage_notes'


def standalone_code_request(message: str) -> bool:
    """Recognize a new code artifact, leaving explicit filesystem work alone."""
    text = _normalize_request_lead(message)
    if re.search(r'\b(?:repo(?:sitory)?|workspace|directory|folder|filesystem|on disk|terminal)\b|(?:~?/|[A-Za-z]:\\\\)\S+', text, re.I):
        return False
    if re.search(r'\b(?:using|with|via)\s+(?:bash|shell|python)\b', text, re.I):
        return False
    if re.match(r'^' + _REQUEST_PREFIX + r'(?:write|create|make|build|generate|implement|code)\s+', text, re.I):
        body = re.sub(r'^' + _REQUEST_PREFIX + r'(?:write|create|make|build|generate|implement|code)\s+', '', text, flags=re.I)
        if re.match(r'(?:(?:a|an|the|new|short|brief|simple)\s+)*(?:email|reply|note|task|document|article|explanation|tutorial|example|snippet)\b', body, re.I):
            return False
        return bool(re.search(r'\b(?:code|script|program|game|app|website|webpage|html|svg)\b|\bin\s+(?:python|javascript|typescript|rust|go|java|c\+\+|ruby|php)\b', body, re.I))
    # A format-only reply can complete an artifact request without shell access.
    return bool(re.fullmatch(r'(?:just\s+)?(?:an?\s+)?(?:svg|html)(?:\s+(?:please|instead))?[.!]?', text, re.I))


def image_edit_followup(message: str, history: Iterable, *, image_attachment=False) -> bool:
    """A scene revision follows a successful image, not an unrelated old image."""
    text = _normalize_request_lead(message)
    if not re.match(r'^' + _REQUEST_PREFIX + r'(?:add|remove|change|replace|edit|adjust|make|turn|put)\b', text, re.I):
        return False
    if image_creation_tools(text) or creation_container_tool(text):
        return False
    if re.match(r'^' + _REQUEST_PREFIX + r'make\s+(?:a\s+)?(?:new|different|another)\s+(?:one|image|picture)\b', text, re.I):
        return False
    if re.search(r'\b(?:email|document|note|task|calendar|workspace|file|code)\b', text, re.I):
        return False
    if re.search(r'\bmake\s+sense\b', text, re.I):
        return False
    if image_attachment:
        return True
    for row in reversed(tuple(history)):
        role = row.get('role') if isinstance(row, dict) else getattr(row, 'role', '')
        if role != 'assistant':
            continue
        metadata = row.get('metadata', {}) if isinstance(row, dict) else getattr(row, 'metadata', {})
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (ValueError, TypeError):
                metadata = {}
        for event in reversed((metadata or {}).get('tool_events') or []):
            if event.get('tool') not in {'generate_image', 'edit_image'} or event.get('error') or event.get('exit_code') not in (None, 0):
                continue
            try:
                result = json.loads(event.get('output') or '{}')
            except (ValueError, TypeError):
                result = {}
            if event.get('image_id') or (isinstance(result, dict) and result.get('image_id')):
                return True
        return False
    return False


def image_creation_tools(message: str) -> frozenset[str] | None:
    """Leading visual creation or a standalone visual brief owns generation."""
    text = _normalize_request_lead(message)
    match = re.match(
        r'^\s*' + _REQUEST_PREFIX + r'(?:generates?|creates?|makes?|draws?|designs?)\s+'
        r'(?:(?:me|us)\s+)?(?:(?:an?|the|new)[.,]?\s+)*'
        r'(?:(?:youtube|video|blog|custom)\s+)?'
        r'(?:images?|pictures?|illustrations?|thumbnails?|logos?|posters?)\b', text, re.I)
    if not match:
        # Chat users commonly give a visual brief without an imperative verb.
        # Anchor at the start so search, description, and document requests
        # mentioning an image retain their own operation.
        match = re.match(
            r'^\s*(?:please\s+)?(?:an?\s+)?'
            r'(?:image|picture|illustration|portrait|drawing|photo)\s+of\s+\S+',
            text, re.I,
        )
        if not match:
            return None
    tools = {'generate_image'}
    # Explicit insertion is a second operation, not a content/topic keyword.
    if re.search(r'\b(?:and|then)\s+(?:insert|add|put|place)\b[^.!?\n]{0,60}'
                 r'\b(?:into|in|to)\s+(?:(?:this|the|my|open|current|active)\s+)*document\b',
                 text[match.end():], re.I):
        tools.add('update_document')
    return frozenset(tools)


def _routing_email_scope(message: str) -> str:
    """A mailbox location qualifier is not an independent filesystem command."""
    text = str(message or '')
    if not re.search(r'\b(?:emails?|mail|inbox|mailbox)\b', text, re.I):
        return text
    return re.sub(
        r'(?P<boundary>^|[.!?;]\s+)use\s+(?:the\s+)?'
        r'[\w /\-\"\x27()]{1,64}\s+folder\s+(?:on|in)\s+'
        r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+[.!?]?\s*$',
        lambda match: match['boundary'] + 'Use the email mailbox.',
        text, flags=re.I,
    )


def selected_tools_for_request(message: str) -> frozenset[str] | None:
    """Narrow only a complete, explicit operation; None retains family scope.

    Full matching intentionally excludes compound instructions, sends, and
    mailbox-content requests. Account discovery needs only local metadata.
    """
    message = _routing_email_scope(editor_request_instructions(message))
    raw_text = str(message or "").strip()
    if inline_text_transformation(raw_text):
        return frozenset()
    text = _normalize_request_lead(message)
    if re.search(
        r"\bfirst\s+tool\s+call\s+(?:must|should|needs?\s+to)\s+be\s+inspect_media\b",
        raw_text,
        re.I,
    ):
        # A trusted user can prescribe the first native evidence operation.
        # Keep the remainder of an explicit media-to-artifact workflow
        # available without letting content nouns (for example musical
        # "notes") route to an unrelated personal-data product.
        tools = {"inspect_media"}
        if (
            re.search(r"\b(?:create|write|save|build|produce)\b", raw_text, re.I)
            and _mentions_workspace_artifact(raw_text)
        ):
            tools.update({"write_file", "read_file"})
        if re.search(r"\b(?:preview|render|open)\b[^.\n]{0,100}\b(?:page|html|browser)\b", raw_text, re.I):
            tools.add("private_browser")
        return frozenset(tools)
    explicitly_named = {
        name
        for name in ("manage_notes", "manage_calendar", "manage_tasks")
        if re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])",
            raw_text,
        )
    }
    if explicitly_named:
        return frozenset(explicitly_named)
    container_tool = creation_container_tool(text)
    if container_tool:
        return frozenset({container_tool})
    image_tools = image_creation_tools(text)
    if image_tools:
        return image_tools
    if standalone_code_request(text):
        return frozenset({'create_document'})
    explicitly_named_web = {
        name
        for name in ("web_search", "web_fetch")
        if re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])",
            raw_text,
            re.I,
        )
    }
    if explicitly_named_web:
        if (
            "web_search" in explicitly_named_web
            and re.search(
                r"\b(?:fetch|read|open|visit|inspect)\b[^.\n]{0,120}"
                r"\b(?:sources?|pages?|urls?|links?)\b",
                raw_text,
                re.I,
            )
        ):
            explicitly_named_web.add("web_fetch")
        if (
            re.search(r"(?:file://)?/(?:tmp_)?workspace(?:/|\b)", raw_text, re.I)
            and (
                re.search(
                    r"\b(?:build|create|edit|persist|produce|save|write)\b",
                    raw_text,
                    re.I,
                )
                or re.search(r"\brequired\s+outputs?\b", raw_text, re.I)
            )
            and re.search(
                r"\b(?:artifacts?|director(?:y|ies)|files?|outputs?|results?)\b|"
                r"\.(?:csv|html|json|jsonl|md|tex|txt)\b",
                raw_text,
                re.I,
            )
        ):
            # Explicit native Web names seal selection to the named tools.
            # Compound autonomous jobs also require a bounded local
            # read/write/verify surface; without it, requiring shell_files
            # makes the whole contract fail closed and drops the Web tools.
            explicitly_named_web.update(
                {"read_file", "write_file", "edit_file", "python"}
            )
        return frozenset(explicitly_named_web)
    workspace_media_request = bool(
        re.search(
            r"(?:file://)?/workspace/[^\s`\"']+\."
            r"(?:avif|bmp|gif|jpe?g|png|svg|tiff?|webp|mp3|m4a|ogg|wav|flac|"
            r"aac|mp4|m4v|mov|mkv|avi|webm)\b",
            raw_text,
            re.I,
        )
        and re.search(
            r"\b(?:inspect|view|watch|review|study|look\s+at|analy[sz]e|read|"
            r"transcribe|caption|recreate|reproduce|identify|describe|extract)\b|"
            r"(?:浏览|查看|观看|分析|检查|识别|转录|截图)",
            raw_text,
            re.I,
        )
    )
    named_media_chain = {
        name for name in ("inspect_media", "extract_text", "write_file", "read_file")
        if re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])",
            raw_text,
        )
    }
    if workspace_media_request and not (
        {"write_file", "read_file"}.issubset(named_media_chain)
        and named_media_chain.intersection({"inspect_media", "extract_text"})
    ):
        # The concrete workspace asset already seals the evidence source.
        # Content words such as "reviews", "which", "highlights", or
        # "final" must not become a public-Web lookup operation.
        return None
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:which\s+search\s+(?:backend|provider)\s+am\s+i\s+on"
        r"(?:\s+right\s+now)?|what\s+(?:default\s+)?time\s+filter\s+is\s+"
        r"my\s+search\s+set\s+to(?:\s+by\s+default)?|show\s+me\s+the\s+whole\s+"
        r"search\s+(?:settings?\s+)?group)[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"manage_settings"})
    web_lookup_fallback = False
    if (
        re.search(r"\b(?:look\s*up|search|find)\b", text, re.I)
        and re.search(
            r"\b(?:current|latest|today(?:'s)?|right\s+now|this\s+(?:week|month|year))\b",
            text,
            re.I,
        )
        and not re.search(r"\bhttps?://", text, re.I)
        and not re.match(
            r"^\s*" + _REQUEST_PREFIX + r"(?:open|browse|visit|navigate|go\s+to)\b",
            text,
            re.I,
        )
        and not re.search(r"\b(?:inbox|emails?|mails?|calendar|meetings?|my\s+notes?)\b", text, re.I)
    ):
        # Current lookups need discovery before navigation. Letting the model
        # begin on an arbitrary browser page can ground an answer in stale or
        # unrelated content without ever establishing a current source set.
        web_lookup_fallback = True
    if re.search(
        r"\b(?:reviews?|ratings?|評判|レビュー|testimonials?)\b",
        text,
        re.I,
    ) and re.search(
        r"\b(?:what(?:'s|\s+is|\s+are)|how\s+are|which|best|good|bad|worth|recommend|"
        r"compare|pros?|cons?|opinions?|thoughts?|about)\b",
        text,
        re.I,
    ) and not re.search(r"\b(?:inbox|emails?|mails?|calendar|meetings?|my\s+notes?)\b", text, re.I):
        # Product/service review requests are current public-web lookups even
        # when the user does not say "search". Route them to web_search before
        # the model sees a schema; otherwise a no-tool contract invites raw
        # provider-specific markup (notably DeepSeek DSML) that cannot execute.
        web_lookup_fallback = True
    if re.search(
        r"\buse\s+(?:the\s+)?(?:odysseus\s+)?web_search\b",
        raw_text,
        re.I,
    ):
        # An explicit native search workflow owns the turn.  Labels such as
        # ``Task:`` and subject matter such as Python/memory are payload, not
        # requests for the scheduler, shell, or personal-memory products.
        # Keep fetch available only when the user also asks to read/fetch the
        # discovered source pages.
        tools = {"web_search"}
        if re.search(
            r"\b(?:fetch|read|open|visit)\b[^.\n]{0,100}\b(?:sources?|pages?|urls?|links?)\b",
            raw_text,
            re.I,
        ):
            tools.add("web_fetch")
        return frozenset(tools)
    if (
        re.search(r"\b(?:email|mail|inbox|message)\b", text, re.I)
        and re.search(r"\battachments?\b", text, re.I)
        and re.search(r"\b(?:draft|write|create)\b", text, re.I)
        and re.search(r"\b(?:search|find|latest|newest)\b", text, re.I)
    ):
        # Keep the complete evidence chain available from the first round;
        # otherwise routing can freeze after read_email and strand an
        # explicitly requested attachment before the dependent draft.
        return frozenset({
            "search_emails", "read_email", "download_attachment", "draft_email",
        })
    if (
        re.search(r"\b(?:calendar|events?|meetings?|agenda)\b", text, re.I)
        and (
            re.search(r"\b(?:draft|write|create)\b[^.;\n]{0,80}\b(?:email|mail|message)\b", text, re.I)
            or re.search(r"\b(?:email|mail|message)\s+draft\b", text, re.I)
        )
        and not re.search(r"\b(?:send|deliver)\s+(?:it|now|immediately)\b", text, re.I)
    ):
        # A calendar-to-draft workflow needs both schemas from round one.
        # "email draft" and "draft email" are equivalent; neither grants
        # authority to send or mutate the calendar.
        return frozenset({"manage_calendar", "draft_email"})
    explicit_media_chain = {
        name for name in ("inspect_media", "extract_text", "write_file", "read_file")
        if re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])",
            raw_text,
        )
    }
    if (
        {"write_file", "read_file"}.issubset(explicit_media_chain)
        and explicit_media_chain.intersection({"inspect_media", "extract_text"})
    ):
        return frozenset(explicit_media_chain)
    concrete_urls = re.findall(r"\bhttps?://[^\s<>\"']+", raw_text, re.I)
    if (
        len(concrete_urls) >= 2
        and re.search(r"\b(?:open|fetch|read|retrieve|check|use)\b", text, re.I)
        and re.search(
            r"\b(?:compare|contrast|synthesi[sz]e|explain|summari[sz]e|cite|citing|evidence)\b",
            text,
            re.I,
        )
        and not re.search(
            r"\b(?:click|fill|submit|login|log\s+in|screenshot|render|navigate)\b",
            text,
            re.I,
        )
    ):
        # Multiple concrete text sources are a bounded fetch/compare
        # operation. Do not force the rendered browser merely because the
        # request says "open"; browser state adds screenshots and encourages
        # repeated DOM searches where web_fetch can supply source text.
        return frozenset({"web_fetch"})
    if (
        re.search(r"\b[^\s<>\"']+\.(?:pdf|png|jpe?g|webp|tiff?)\b", raw_text, re.I)
        and re.search(r"\b(?:ocr|extract|inspect|read)\b", text, re.I)
        and re.search(r"\b(?:write|save|create)\b[^.!?\n]{0,100}\b(?:report|file|markdown|json|csv)\b", text, re.I)
        and re.search(r"\b(?:read|verify|check|inspect)\b[^.!?\n]{0,100}\b(?:saved|output|file|report|it)\b", text, re.I)
    ):
        # Exact local media-to-artifact workflows do not need a shell or
        # broad workspace discovery. Keep the model on the evidence, mutation,
        # and completion tools named by the requested workflow.
        return frozenset({"inspect_media", "extract_text", "write_file", "read_file"})
    if re.search(
        r"\b(?:jot|write|put|save)\b[^.!?\n]{0,100}\b(?:in|into|as)\s+"
        r"(?:my\s+)?notes?\b|\bjot\s+(?:down\s+)?(?:a\s+)?reminder\b",
        text,
        re.I,
    ):
        return frozenset({"manage_notes"})
    if re.search(
        r"\b(?:ping|remind|notify)\s+me\b[^.!?\n]{0,100}\b"
        r"(?:every|daily|weekly|monthly|each)\b",
        text,
        re.I,
    ):
        return frozenset({"manage_tasks"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:review|check|run|audit|sync|back\s*up)\b"
        r"[^?!.]{1,120}\bat\s+(?:[01]?\d|2[0-3])(?::[0-5]\d)?\s*(?:am|pm)"
        r"[?!.]*",
        text,
        re.I,
    ) or re.fullmatch(
        _REQUEST_PREFIX + r"(?:yeah\s+)?make\s+it\s+(?:a\s+)?"
        r"(?:daily|weekly|monthly|weekday|weekend)\s+thing\b[^?!.]*[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"manage_tasks"})
    if re.search(
        r"\b(?:coffee|lunch|dinner|meeting|appointment|call|trip|flight)\b"
        r"[^?!.]{0,120}\bon\s+the\s+books\b",
        text,
        re.I,
    ):
        return frozenset({"manage_calendar"})
    if re.fullmatch(
        _REQUEST_PREFIX
        + r"(?:(?:next\s+month|next\s+week|tomor{1,2}ow)\s+)?"
          r"(?:date|coffee|lunch|dinner|meeting|appointment|call)\s+with\s+"
          r"[^?!.]{2,140}\b(?:tomor{1,2}ow|next\s+(?:week|month)|"
          r"(?:at\s+)?(?:[01]?\d|2[0-3])(?::[0-5]\d)?\s*(?:am|pm)?)\b"
          r"[^?!.]*[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"manage_calendar"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:where(?:['’]?s|s|\s+is)|where\s+do\s+(?:i|we)\s+find)\s+"
        r"(?:the\s+)?official\s+(?:site|website|page)\s+for\s+[^?!.]{2,160}[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"web_search"})
    if re.fullmatch(
        _REQUEST_PREFIX
        + r"(?:quick\s+)?(?:[A-Za-z][A-Za-z-]*\s+){0,4}news\s+"
          r"(?:rundown|update|summary)(?:\s+(?:please|pls))?[?!.]*",
        text,
        re.I,
    ) or re.fullmatch(
        _REQUEST_PREFIX + r"(?:hey\s+)?what(?:['’]?s|s|\s+is)\s+goin(?:g)?\s+on\s+"
        r"(?:in|with)\s+[^?!.]{2,100}\b(?:right\s+now|today|this\s+week)"
        r"(?:[?!.]\s*(?:quick|short|brief)(?:\s+version)?\s*(?:please|pls)?)?[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"web_search"})
    if re.search(
        r"\b(?:quick\s+look\s*up|quick\s+search|try\s+(?:a\s+)?(?:search|one)\s+on)\b",
        text,
        re.I,
    ):
        return frozenset({"web_search"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:is\s+there\s+)?anything\s+new\s+(?:in|on|about)\s+"
        r"[^?!.]{2,160}\b(?:today|this\s+(?:week|month|year)|recently)[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"web_search"})
    if (
        re.match(
            r"^(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:quick\s+)?look\s*up\b",
            raw_text,
            re.I,
        )
        and re.search(r"\bofficial\b[^\n]{0,80}\b(?:link|url|source)\b", raw_text, re.I)
    ):
        return frozenset({"web_search"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:give|show)\s+me\s+(?:my\s+)?(?:"
        r"calend(?:ar|er)\s+for\s+(?:this|next)\s+week|upcoming\s+events)"
        r"(?:\s+(?:please|pls|plz))?[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"manage_calendar"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"wat\s+(?:scheduled\s+)?ta(?:s)?ks\s+"
        r"do\s+i\s+have(?:\s+set\s+up)?(?:\s+rn)?[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"manage_tasks"})
    if (
        re.search(r"\b(?:do\s+i\s+have|are\s+there)\b[^?!.]{0,80}\bskills?\b", text, re.I)
        and re.search(r"\b(?:cover|handle|handling|about|for)\b", text, re.I)
    ):
        return frozenset({"manage_skills"})
    if (
        re.search(r"\b(?:anthropic|openai|google|gemini|claude)\s+models?\b", text, re.I)
        and re.search(r"\b(?:compar(?:e|ed|ison)|equivall?ent|alternative|closest)\b", text, re.I)
    ):
        return frozenset({"web_search"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"what(?:['’]?s|s|\s+is)\s+(?:the\s+)?latest\s+"
        r"[^?!.]{1,100}\b(?:driver|release|version)\b[^?!.]*[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"web_search"})
    if (
        re.match(r"^\s*" + _REQUEST_PREFIX + r"(?:open|navigate|browse|visit|go\s+to)\b", text, re.I)
        and not re.search(r"(?:file://)?/(?:tmp_)?workspace/", text, re.I)
        and (
            re.search(r"\bhttps?://[^\s<>\"']+", text, re.I)
            or re.search(r"\b(?:[a-z0-9-]+\.)+(?:com|org|net|io|ai|jp|co\.jp)\b", text, re.I)
        )
    ):
        # Navigation is an interactive browser operation even for loopback or
        # LAN URLs.  The old domain-only check let ``Go to http://127...`` fall
        # through to the single-URL ``web_fetch`` rule.  With Web Search off,
        # that made the immutable contract deny the turn before inference.
        # Selecting private_browser grants only the named interactive target;
        # it does not enable open-ended web_search/web_fetch.
        return frozenset({"private_browser"})
    if (
        re.search(r"\b(?:find|search|look\s+for|recommend)\b", text, re.I)
        and re.search(r"\b(?:services?|providers?|companies|contractors?)\b", text, re.I)
        and re.search(r"\b(?:quote|price|cost|hire|haul|remove|repair|deliver)\b", text, re.I)
    ):
        return frozenset({"web_search"})
    if (
        re.search(r"\bwebh(?:ooks?|oks?)\b", text, re.I)
        and re.search(r"\b(?:any|what|which|show|list|check|review|inspect|look)\b", text, re.I)
        and re.search(r"\b(?:hooked\s+up|connected|configured|available|status|active|enabled)\b", text, re.I)
        and not re.search(r"\b(?:create|add|delete|remove|update|change|enable|disable)\b", text, re.I)
    ):
        return frozenset({"manage_webhooks"})
    if (
        re.search(r"\b(?:image\s+gen(?:eration)?|imagegen|images?)\b", text, re.I)
        and re.search(r"\b(?:switch|turn|set|toggle|put)\b", text, re.I)
        and re.search(r"\b(?:off|on|disable[ds]?|enable[ds]?)\b", text, re.I)
    ):
        return frozenset({"manage_settings"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:hey\s+)?what\s+(?:chats?|sessions?|conversations?)\s+"
        r"(?:(?:do\s+)?(?:i|we)\s+have|have\s+(?:i|we)\s+got)\s+"
        r"(?:going|open|active)"
        r"(?:\s+(?:right|rite)\s+now)?[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"list_sessions"})
    if (
        re.search(r"\b(?:show|list|check|give)\b", text, re.I)
        and re.search(r"\bunread(?:\s+(?:emails?|messages?|mail))?\b", text, re.I)
        and re.search(r"\b(?:second|secondary|other)\s+(?:email\s+)?account\b", text, re.I)
    ):
        return frozenset({"list_email_accounts", "list_emails"})
    if (
        re.match(r"^\s*" + _REQUEST_PREFIX + r"open\b", text, re.I)
        and (
            (
                re.search(r"\bflights?\b", text, re.I)
                and re.search(r"\b(?:from|for)\s+[^?!.]{1,80}\s+to\s+[^?!.]{1,80}", text, re.I)
            )
            or (
                re.search(r"\b(?:current|recent|latest)\s+reviews?\b", text, re.I)
                and re.search(r"\b(?:find|check|show|read)\b", text, re.I)
            )
        )
    ):
        return frozenset({"private_browser"})
    if (
        re.search(r"\blatest\s+(?:stable\s+)?[^?!.]{1,80}\s+release\b", text, re.I)
        and re.search(r"\b(?:find|check|what|tell|show|link|change|version)\b", text, re.I)
    ):
        return frozenset({"web_search"})
    if (
        re.search(r"\bmcp\b", text, re.I)
        and re.search(r"\b(?:servers?|connections?|tools?)\b", text, re.I)
        and re.search(
            r"\b(?:what|which|wich|show|list|check|connected|configured|hooked\s+up|"
            r"available|expose[ds]?)\b",
            text,
            re.I,
        )
        and not re.search(r"\b(?:add|delete|remove|enable|disable|reconnect|change)\b", text, re.I)
    ):
        return frozenset({"manage_mcp"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:use|open|navigate|browse)\s+(?:the\s+)?google\s+maps\b"
        r"[^.!?]{0,240}\b(?:navigate|directions?|route|from|to)\b[^.!?]*[.!?]*",
        text,
        re.I,
    ):
        return frozenset({"private_browser"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:open|find|show|read)\s+(?:the\s+)?"
        r"[A-Za-z0-9.+_-]{2,80}\s+release\s+notes[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"web_search", "web_fetch"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"latest\s+[^?!.]{2,100}\b(?:driver|release|version)\b[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"web_search"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:has|did)\s+[^?!.]{2,100}\s+"
        r"(?:uploaded?\s+anything|post(?:ed)?\s+(?:a\s+)?new\s+one)[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"web_search", "youtube_tool"})
    if (
        re.fullmatch(
            _REQUEST_PREFIX + r"(?:has|did)\s+[^?!.]{2,100}?\s+uploaded?[?!.]*",
            text,
            re.I,
        )
        or re.fullmatch(
            _REQUEST_PREFIX
            + r"(?:(?:what(?:['’]?s|s|\s+is)\s+)?[^?!.]{2,100}?\s+)?"
              r"latest(?:\s+\d+)?\s+(?:youtube\s+)?videos?[?!.]*",
            text,
            re.I,
        )
    ):
        # A bare creator/channel upload question has no local upload target.
        # Discovery finds the canonical channel while youtube_tool supplies
        # metadata, transcript, and comments for later turns.
        return frozenset({"web_search", "youtube_tool"})
    if (
        re.search(r"\b(?:model\s+)?downloads?\b", text, re.I)
        and re.search(r"\b(?:progress|far\s+along|in\s+flight|queue|status|stuck|errored?)\b", text, re.I)
        and not re.search(r"\b(?:cancel|delete|remove|start)\b", text, re.I)
    ):
        return frozenset({"list_downloads"})
    if (
        re.search(r"\bwebhook\s+status\b", text, re.I)
        and re.search(r"\b(?:show|list|check|what)\b", text, re.I)
    ):
        return frozenset({"manage_webhooks"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:open|opne)\s+(?:my\s+|the\s+)?"
        r"(?:calendar|calender)\s+\d{4}\s+"
        r"(?:jan\w*|feb\w*|mar\w*|apr\w*|may|jun\w*|jul\w*|aug\w*|"
        r"sep\w*|oct\w*|nov\w*|dec\w*)[.!?]*",
        text,
        re.I,
    ):
        return frozenset({"ui_control"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:open|opne)\s+(?:(?:that|this)\s+(?:up\s+)?in\s+the\s+)?"
        r"(?:calendar|calender)(?:\s+(?:panel|view|tab|sidebar|that\s+month))?"
        r"(?:\s+that\s+month)?(?:\s+so\s+i\s+can\s+see\s+it)?[.!?]*",
        text,
        re.I,
    ):
        return frozenset({"ui_control"})
    if (
        re.search(r"\blatest\s+(?:youtube\s+)?video\b", text, re.I)
        and re.search(r"\byoutube\b", text, re.I)
        and re.search(r"\b(?:what|summari[sz]e|say|says|about|from)\b", text, re.I)
    ):
        return frozenset({"web_search", "youtube_tool"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"does?\s+[^?!.]{2,100}\s+have\s+(?:an?\s+)?youtube\s+"
        r"channel[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"web_search"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"what(?:['’]?s|s|\s+is)\s+[^?!.]{2,100}?(?:['’]s|s)\s+"
        r"latest\s+(?:youtube\s+)?video[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"web_search", "youtube_tool"})
    if (
        re.search(r"\b(?:recent|latest|scratch)\s+(?:chats?|sessions?|conversations?)\b", text, re.I)
        and re.search(r"\b(?:give|list|show|find|help|made|created)\b", text, re.I)
    ):
        return frozenset({"list_sessions"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"what\s+models?\s+(?:are\s+)?available\s+to\s+(?:me|us)"
        r"(?:\s+right\s+now)?[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"list_models"})
    model_discovery_clauses = re.split(r"[\n.!?;]+", text)
    if any(
        re.search(r"\b(?:models?|qwen|llama|gemma|mistral|instruct)\b", clause, re.I)
        and re.search(
            r"\b(?:i(?:['’]?m|\s+am)\s+after|look(?:ing)?\s+for|find|search|show|"
            r"recommend|suggest|anything\s+in)\b",
            clause,
            re.I,
        )
        and re.search(
            r"\b(?:hugging\s*face|huggingface|hf|small|local(?:ly)?|at\s+home|"
            r"\d+(?:\.\d+)?\s*[-–]\s*\d+(?:\.\d+)?\s*b|\d+(?:\.\d+)?b)\b",
            clause,
            re.I,
        )
        for clause in model_discovery_clauses
    ):
        # Model discovery belongs to the Hugging Face catalog.  This covers
        # natural recommendation wording, not only the literal phrase
        # “Hugging Face model search”.
        return frozenset({"search_hf_models"})
    if (
        re.search(r"\b(?:inbox|mailbox|email)\b", text, re.I)
        and re.search(r"\b(?:sketchy|suspicious|spam|phishing|malicious)\b", text, re.I)
        and not re.search(r"\b(?:delete|remove|archive|mark)\b", text, re.I)
    ):
        return frozenset({"scan_spam"})
    if (
        re.search(r"\bwebh(?:ooks?|oks?)\b", text, re.I)
        and re.search(
            r"^(?:\s*" + _REQUEST_PREFIX + r")?(?:what|which|show|list|check|review|inspect|look)\b",
            text,
            re.I,
        )
        and not re.search(r"\b(?:create|add|delete|remove|update|change|enable|disable)\b", text, re.I)
    ):
        return frozenset({"manage_webhooks"})
    if (
        re.search(r"https?://(?:www\.)?(?:youtube\.com|youtu\.be)(?:/|$)", text, re.I)
        and re.search(r"\bmetadata\b", text, re.I)
        and not re.search(r"\bprivate\s+brow(?:ser|esr|sr)\b", text, re.I)
    ):
        return frozenset({"youtube_tool"})
    if (
        re.search(r"\bteacher(?:\s+model)?\b", text, re.I)
        and re.search(r"\b(?:ask|check|review|second\s+opinion|judge|rewrite|verify)\b", text, re.I)
    ):
        return frozenset({"ask_teacher"})
    if (
        re.search(r"https?://", text, re.I)
        and re.search(r"\bprivate\s+brow(?:ser|esr|sr)\b", text, re.I)
        and re.search(r"\b(?:open|browse|visit|navigate)\b", text, re.I)
    ):
        return frozenset({"private_browser"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:the\s+)?newsy\s+(?:kind|one|version)[.!?]*",
        text,
        re.I,
    ):
        return frozenset({"web_search"})
    if (
        re.search(r"\b(?:what(?:['’]?s|\s+is)\s+downloading|downloads?\s+in\s+progress)\b", text, re.I)
        and re.search(r"\bcookbo{1,2}k\b|\bdownloads?\b", text, re.I)
    ):
        return frozenset({"list_downloads"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"summari[sz]e\s+(?:my|our|the)\s+(?:inbox|mailbox)\s+"
        r"(?:this|past|last)\s+week[.!?]*",
        text,
        re.I,
    ):
        return frozenset({"list_emails"})
    if (
        re.fullmatch(
            _REQUEST_PREFIX + r"how\s+many\s+results?\s+does\s+(?:my|our|the)\s+"
            r"search\s+return(?:\s+at\s+a\s+time)?[?!.]*",
            text,
            re.I,
        )
        or (
            re.search(r"\bsearch\s+(?:prefs?|preferences?|settings?)\b", text, re.I)
            and re.search(r"\b(?:check|read|show|list|paste|what|which|how\s+many)\b", text, re.I)
        )
        or (
            re.search(r"\b(?:region|language)\b", text, re.I)
            and re.search(r"\bprefs?|preferences?|settings?\b", text, re.I)
            and re.search(r"\b(?:my|our|the)\s+search\b", text, re.I)
        )
    ):
        return frozenset({"manage_settings"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:do\s+)?(?:one|a)\s+real\s+search\s+for\s+"
        r"[^?!.]{2,220}[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"web_search"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:find|locate|look\s*up)\s+[^?!.]{2,180}?"
        r"\b(?:page|site)\b[^?!.]{0,100}\b(?:return|give|show)\b"
        r"[^?!.]{0,60}\b(?:link|url)\b(?:\s*,?\s*please)?[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"web_search"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:do|run)?\s*(?:a\s+)?quick\s+"
        r"(?:look\s*up|lookup)\s+(?:on|about|for)\s+[^?!.]{2,220}[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"web_search"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:yeah[,!]?\s+)?(?:do\s+)?(?:a\s+)?quick\s+"
        r"(?:look\s*up|lookup|search)\s+(?:to\s+)?"
        r"(?:back|verify|check|confirm)?\s*(?:that|this|it)\s+up(?:\s+please)?[?!.]*",
        text,
        re.I,
    ) or re.fullmatch(
        r"to\s+(?:back|verify|check|confirm)\s+(?:that|this|it)\s+up"
        r"(?:\s+please)?[?!.]*",
        text,
        re.I,
    ) or re.fullmatch(
        _REQUEST_PREFIX + r"(?:yeah[,!]?\s+)?(?:do\s+)?(?:a\s+)?quick\s+search\s+"
        r"(?:on|for|about)\s+(?:that|this|it)[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"web_search"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:open|fetch|read|visit|check)\s+(?:the\s+)?"
        r"(?:top|first|second|third|last)\s+(?:result|link|source)\b[^.!?]*[.!?]*",
        text,
        re.I,
    ):
        return frozenset({"web_fetch"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:quick\s+)?search\s+(?:to\s+[^:!?]{2,100}:\s*|"
        r"(?:for|on|about)\s+)[^?!.]{2,180}[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"web_search"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:search|find\s+in|look\s+through)\s+"
        r"(?:my|our|the)\s+notes?\s+(?:for|about|mentioning)\s+"
        r"[^?!.]{2,160}?(?:\s+then)?[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"manage_notes"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:open(?:\s+up)?|show)\s+(?:me\s+)?(?:the\s+)?"
        r"(?:settings|preferences)\s+(?:area|screen)[.!?]*",
        text,
        re.I,
    ):
        return frozenset({"ui_control"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"open(?:\s+up)?\s+(?:my\s+|the\s+)?calend(?:ar|er)\s+"
        r"(?:for\s+|to\s+|at\s+)?(?:jan\w*|feb\w*|mar\w*|apr\w*|may|jun\w*|"
        r"jul\w*|aug\w*|sep\w*|oct\w*|nov\w*|dec\w*)"
        r"(?:\s+\d{4})?[.!?]*",
        text,
        re.I,
    ):
        return frozenset({"ui_control"})
    if (
        re.search(r"\bweb\s+look\s*up\b", text, re.I)
        and re.search(r"\b(?:official\s+)?(?:source\s+)?(?:link|url|page|site)\b", text, re.I)
    ):
        return frozenset({"web_search"})
    if _explicit_email_attachment_read(text):
        return frozenset({"download_attachment"})
    if re.fullmatch(
        _REQUEST_PREFIX
        + r"(?:i\s+had\s+)?(?:a\s+)?doc(?:ument)?\s+[^.!?\n]{0,100}?"
          r"(?:called|named|titled)\s+['\"][^'\"\n]{2,160}['\"]"
          r"[^.!?\n]{0,120}\b(?:pull|open|bring|show)\b[^.!?\n]{0,80}"
          r"\b(?:editor|documents?\s+(?:panel|view))(?:\s+for\s+me)?[.!?]*",
        text,
        re.I,
    ):
        return frozenset({"manage_documents", "ui_control"})
    if (
        re.fullmatch(
            _REQUEST_PREFIX + r"(?:open(?:\s+up)?|pull\s+up|pop\s+open)\s+"
            r"(?:my\s+|the\s+)?(?:calendar|calender|schedule|documents?|docs?|"
            r"gallery|images?|e-?mail|inbox|notes?|memor(?:y|ies)|brain|skills?|"
            r"settings|cookbook)\s+(?:panel|sidebar|tab|view|modal)[^.!?]*[.!?]*",
            text,
            re.I,
        )
        or re.fullmatch(
            _REQUEST_PREFIX + r"pop\s+(?:my\s+|the\s+)?(?:calendar|calender|schedule|"
            r"documents?|docs?|gallery|images?|e-?mail|inbox|notes?|memor(?:y|ies)|"
            r"brain|skills?|settings|cookbook)\s+(?:panel|sidebar|tab|view)\s+open"
            r"(?:\s+for\s+me)?[.!?]*",
            text,
            re.I,
        )
    ):
        return frozenset({"ui_control"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:cool[,!]?\s+)?(?:flip|switch|change|set|move|put)\s+"
        r"(?:it|that|this|the\s+(?:calendar|panel))\s+(?:over\s+|back\s+)?to\s+"
        r"(?:the\s+)?(?:day|week|month|year|agenda)(?:\s+view)?[.!?]*",
        text,
        re.I,
    ):
        # A named panel view is itself a UI operation. It does not depend on
        # history serialization retaining the preceding open-panel event.
        return frozenset({"ui_control"})
    if (
        re.search(r"\b(?:compare|check|match)\b", text, re.I)
        and re.search(r"\b(?:cached|cache)\b[^.;\n]{0,80}\b(?:locally|local|models?)\b", text, re.I)
    ):
        return frozenset({"list_cached_models"})
    if (
        re.search(r"\b(?:grab|download)\b", text, re.I)
        and re.search(r"\b[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\b", text)
        and re.search(r"\b(?:locally|local|download)\b", text, re.I)
        and re.search(
            r"\b(?:models?|qwen|llama|gemma|mistral|safetensors|gguf|"
            r"hugging\s*face|huggingface|hf\s+hub|model\s+hub)\b",
            text,
            re.I,
        )
    ):
        return frozenset({"download_model"})
    if (
        re.search(r"\b(?:hugging\s*face|huggingface|hf)\s+(?:model\s+)?search\b", text, re.I)
        and re.search(r"\b(?:find|search|look\s+for|show|list)\b", text, re.I)
    ):
        return frozenset({"search_hf_models"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:is\s+there\s+|do\s+i\s+have\s+|have\s+i\s+got\s+)?"
        r"any\s+skills?\s+in\s+(?:my|the)\s+(?:skills?\s+)?library\s+"
        r"(?:about|for|that\s+(?:handles?|covers?))\s+[^?!.]{2,160}[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"manage_skills"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:what(?:['’]?s|\s+is)|wats)\s+in\s+"
        r"(?:my|the)\s+skills?\s+library\b[^\n]*",
        text,
        re.I,
    ):
        return frozenset({"manage_skills"})
    if (
        re.match(
            r"^\s*" + _REQUEST_PREFIX + r"(?:open|fetch|read|visit|check|pull\s+up)\b",
            text,
            re.I,
        )
        and re.search(
            r"\b(?:that|this|the|its?)\s+"
            r"(?:(?:official|original|result|source)\s+)?(?:page|link|url|source)\b",
            text,
            re.I,
        )
        and not re.search(r"\b(?:another|different|second|other)\s+source\b", text, re.I)
    ):
        # A concrete page continuation consumes the URL established by prior
        # typed web evidence.  It is a fetch operation, not a new broad search
        # and not a browser-automation request merely because the user says
        # "open".
        return frozenset({"web_fetch"})
    if requests_independent_web_source(text):
        # Asking for independent corroboration requires discovery of a source;
        # replaying the previous query or fetching the same page cannot satisfy
        # the operation.
        return frozenset({"web_search"})
    if requests_supporting_web_source(text):
        # The previous answer may have come from model knowledge and therefore
        # have no concrete URL to fetch. Discover a supporting source instead
        # of letting the model claim that citations are unavailable.
        return frozenset({"web_search"})
    if (
        re.match(r"^\s*" + _REQUEST_PREFIX + r"(?:launch|start|run|serve)\b", text, re.I)
        and re.search(r"\b(?:serve\s+)?preset\b", text, re.I)
    ):
        # A named saved preset is an executable Cookbook object, not a prompt
        # template or a generic model question.
        return frozenset({"serve_preset"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:great[,!]?\s+)?(?:open|read|fetch|visit|check)\s+(?:up\s+)?"
        r"(?:one\s+of\s+)?(?:the\s+)?sources?(?:\s+(?:you|u)\s+(?:used|found|gave))?"
        r"[.!?]*",
        text, re.I,
    ):
        return frozenset({"web_fetch"})
    if (
        re.search(r"\b(?:internal\s+)?app\s+api\b", text, re.I)
        and re.search(r"\bgallery\b", text, re.I)
        and re.search(r"\b(?:list|show|view|look|browse|images?|library)\b", text, re.I)
    ):
        return frozenset({"app_api"})
    if re.fullmatch(
        _REQUEST_PREFIX
        + r"(?:confi?rm|confrim|verify|check)\s+(?:one\s+of\s+)?"
          r"(?:those|them|that|it)\s+(?:with|against|from|on)\s+(?:the\s+)?"
          r"(?:(?:original|official)\s+)?(?:source\s+)?(?:page|source|site|link)[?!.]*",
        text, re.I,
    ):
        # The source was discovered on the preceding turn; this turn asks to
        # read that source, not repeat the broad search.
        return frozenset({"web_fetch"})
    if (
        re.match(r"^\s*" + _REQUEST_PREFIX + r"(?:find|look\s*up|search)\b", text, re.I)
        and re.search(r"\bofficial\b[^.;\n]{0,100}\b(?:source|link|url|page|site)\b", text, re.I)
        and not re.search(r"\b(?:hugging\s*face|huggingface|hf\s+hub|model\s+hub|repository|repo)\b", text, re.I)
    ):
        return frozenset({"web_search"})
    if (
        re.match(
            r"^\s*" + _REQUEST_PREFIX + r"i\s+need\s+an?\s+official\b",
            text,
            re.I,
        )
        and re.search(r"\b(?:reference|source|link|url|page|site)\b", text, re.I)
    ):
        return frozenset({"web_search"})
    if (
        re.search(r"\b(?:swap|switch|change|move|use)\b[^.;\n]{0,100}\bmodels?\b", text, re.I)
        or re.search(r"\bmodels?\b[^.;\n]{0,100}\b(?:swap|switch|change|move|use)\b", text, re.I)
    ):
        # A vague target such as "a lighter model" needs discovery before the
        # same explicit UI switch. Offering both keeps the model inside the
        # intended control plane without granting unrelated admin actions.
        return frozenset({"list_models", "ui_control"})
    if (
        re.match(
            r"^\s*" + _REQUEST_PREFIX
            + r"(?:(?:which|wich)\s+(?:mail|email|emial)\s+accounts?\s+"
              r"(?:(?:do\s+(?:i|we)\s+have\s+)?(?:hooked\s+up|connected|configured)|"
              r"(?:are\s+)?(?:hooked\s+up|connected|configured)(?:\s+here)?)"
              r"|(?:tell\s+me\s+)?what\s+(?:mailboxes|(?:mail|email)\s+accounts?)\s+"
              r"(?:i(?:['’]?ve|\s+have)|we(?:['’]?ve|\s+have))\s+(?:connected|configured)"
              r"|what\s+(?:mail|email|emial)\s+accounts?\s+(?:are\s+)?"
              r"(?:hooked\s+up|connected|configured)(?:\s+here)?)\b",
            text,
            re.I,
        )
        and not re.search(r"\b(?:add|remove|delete|disable|change|update)\b", text, re.I)
    ):
        return frozenset({"list_email_accounts"})
    pattern = (
        _REQUEST_PREFIX
        + r"(?:(?:list|show)\s+(?:me\s+)?my\s+email\s+accounts?"
        r"|what(?:['’]?s|\s+is)\s+my\s+email"
        r"|what(?:['’]?s|\s+is)\s+my\s+email\s+address"
        r"|what\s+are\s+my\s+email\s+(?:accounts|addresses))"
        r"(?:\s*,?\s+please)?[.!?]*"
    )
    if re.fullmatch(pattern, text, re.I):
        return frozenset({"list_email_accounts"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"do\s+(?:i|we)\s+(?:even\s+)?have\s+any\s+"
        r"(?:mail|email|emial)\s+accounts?\s+(?:hooked\s+up|connected|configured)"
        r"(?:\s+here)?[.!?]*",
        text,
        re.I,
    ):
        return frozenset({"list_email_accounts"})
    if _has_cookbook_server_reference(text) and re.search(
        r"\b(?:show|list|configured|available|current|right\s+now)\b", text, re.I
    ):
        return frozenset({"list_cookbook_servers"})
    if re.search(r"\btool\s+toggles?\b", text, re.I) and re.search(
        r"\b(?:show|list|check|eyeball|inspect|view|what)\b", text, re.I
    ):
        return frozenset({"manage_settings"})
    if (
        re.search(r"\b(?:check|show|list|look\s+at|find)\b[^.;\n]{0,100}\bcalendar\b", text, re.I)
        and re.search(
            r"\b(?:check|find|search|look\s+for|read)\b[^.;\n]{0,100}"
            r"\b(?:email|message|mail)\b",
            text,
            re.I,
        )
        and re.search(
            r"\b(?:update|change|move|reschedule|edit)\b[^.;\n]{0,100}"
            r"\b(?:calendar|event|meeting|appointment|call|review)\b",
            text,
            re.I,
        )
    ):
        # Tool schemas are request-scoped, so a causal cross-store workflow
        # needs its complete executable path before the first calendar read.
        return frozenset({"manage_calendar", "search_emails", "read_email"})
    if (
        re.search(r"\bread\s+(?:me\s+)?(?:the\s+)?(?:latest|newest)\s+(?:one|email|message)\s+from\s+(?:them|that\s+sender)\b", text, re.I)
        and re.search(r"\b(?:check|show|list|look\s+at)\b[^.;\n]{0,60}\bcalendar\b", text, re.I)
    ):
        return frozenset({"search_emails", "read_email", "manage_calendar"})
    _email_read_only_text = re.sub(
        r"\b(?:do\s+not|don't|without)\s+(?:draft|send|reply|respond|modify|change)\b[^.;\n]*",
        "",
        text,
        flags=re.I,
    )
    if (
        re.search(r"\b(?:email|message|mail)\b", text, re.I)
        and re.search(r"\b(?:find|search|look\s+for|locate)\b", text, re.I)
        and re.search(r"\b(?:read|open)\b", text, re.I)
        and not re.search(
            r"\b(?:draft|send|reply|respond|forward|archive|delete|modify)\b",
            _email_read_only_text,
            re.I,
        )
    ):
        return frozenset({"search_emails", "read_email"})
    if (
        re.match(r"^\s*" + _REQUEST_PREFIX + r"(?:what|which|show|list|check|any)\b", text, re.I)
        and re.search(
            r"\bcached\s+(?:models?|modles?|weights?)\b|\b(?:models?|modles?)\s+(?:are\s+)?(?:already\s+)?cached\b",
            text, re.I,
        )
    ):
        return frozenset({"list_cached_models"})
    if (
        re.match(r"^\s*" + _REQUEST_PREFIX + r"(?:run|use|execute|build)\b", text, re.I)
        and re.search(r"\b(?:model\s+)?pipeline\b|\btwo[- ]step\b", text, re.I)
    ):
        return frozenset({"pipeline"})
    if (
        re.match(r"^\s*" + _REQUEST_PREFIX + r"(?:ask|have)\b", text, re.I)
        and re.search(r"\b[A-Za-z0-9._-]+/[A-Za-z0-9._-]+\b", text)
    ):
        return frozenset({"chat_with_model"})
    if re.match(
        r"^\s*" + _REQUEST_PREFIX + r"(?:show|list|check|review|inspect|look\s+at)\b",
        text,
        re.I,
    ):
        admin_targets = (
            (r"\b(?:model\s+)?endpoints?\b|\bendpoint\s+configurations?\b", "manage_endpoints"),
            (r"\bmcp\b.{0,80}\b(?:servers?|connections?|tools?)\b", "manage_mcp"),
            (r"\b(?:api|access)\s+tokens?\b", "manage_tokens"),
            (r"\bwebh(?:ooks?|oks?)\b", "manage_webhooks"),
        )
        matched_admin = [tool for target, tool in admin_targets if re.search(target, text, re.I)]
        if len(matched_admin) == 1:
            return frozenset(matched_admin)
    session_noun = r"(?:chats?|sessions?|conversations?)"
    if (
        re.match(r"^\s*" + _REQUEST_PREFIX + r"(?:create|start|open|make)\b", text, re.I)
        and re.search(r"\b(?:new|temporary|scratch)?\s*" + session_noun + r"\b", text, re.I)
    ):
        return frozenset({"create_session"})
    if (
        re.match(r"^\s*" + _REQUEST_PREFIX + r"(?:send|message)\b", text, re.I)
        and re.search(r"\b" + session_noun + r"\b", text, re.I)
    ):
        return frozenset({"send_to_session"})
    if (
        re.search(r"\b(?:search|find|look\s+through)\b", text, re.I)
        and re.search(
            r"\b(?:my\s+(?:(?:prior|past|previous|old(?:er)?)\s+)?(?:chats?|conversations?|chat\s+transcripts?)|"
            r"(?:prior|past|previous|old(?:er)?)\s+(?:chats?|conversations?|chat\s+transcripts?))\b",
            text, re.I,
        )
    ):
        return frozenset({"search_chats"})
    if (
        re.match(r"^\s*" + _REQUEST_PREFIX + r"(?:delete|remove|archive|rename)\b", text, re.I)
        and re.search(r"\b" + session_noun + r"\b", text, re.I)
    ):
        return frozenset({"list_sessions", "manage_session"})
    if (
        re.match(r"^\s*" + _REQUEST_PREFIX + r"(?:read|open|show)\b", text, re.I)
        and re.search(r"\b(?:email|message)?\s*uid\s*[:#]?\s*[A-Za-z0-9._-]+", text, re.I)
    ):
        return frozenset({"read_email"})
    if (
        re.match(r"^\s*" + _REQUEST_PREFIX + r"(?:reply|respond)\b", text, re.I)
        and re.search(r"\b(?:email\s+)?(?:uid|message[- ]?id)\s*[:#]?\s*[A-Za-z0-9._@<>-]+", text, re.I)
    ):
        return frozenset({"reply_to_email"})
    if (
        re.match(r"^\s*" + _REQUEST_PREFIX + r"(?:send|email)\b", text, re.I)
        and re.search(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", text, re.I)
    ):
        return frozenset({"send_email"})
    urls = re.findall(r"\bhttps?://[^\s<>\"']+", text, re.I)
    if (
        len(urls) == 1
        and not re.match(
            r"https?://(?:www\.)?(?:youtube\.com|youtu\.be)(?:/|$)",
            urls[0],
            re.I,
        )
        and not re.search(r"(?:\.pdf(?:[?#]|$)|/pdf/)", urls[0], re.I)
        and not re.search(r"(?:file://)?/(?:tmp_)?workspace/", text, re.I)
        and not re.search(
            r"\b(?:browse|navigate|click|fill|submit|private[_ -]?browser|"
            r"save|write|create|export|render|generate|send|email)\b",
            text,
            re.I,
        )
    ):
        # A single concrete HTML target is a complete operation. Narrowing it
        # avoids sending unrelated family schemas (notably union-root PDF
        # schemas rejected by some OpenAI-compatible providers).
        return frozenset({"web_fetch"})
    if _ORDINAL_EMAIL_FOLLOWUP.fullmatch(text):
        return frozenset({"read_email"})
    if (_ORDINAL_SKILL_FOLLOWUP.fullmatch(text)
            and re.search(r"\bskills?\b", text, re.I)):
        # Bare "open the first one" is product-neutral. The conversation-
        # aware required-read resolver binds it to the prior successful
        # collection; treating every ordinal as Skills causes the selected-
        # tool intersection to erase Calendar/Notes/Documents tools.
        return frozenset({"manage_skills"})
    native_names = (
        "get_workspace", "read_file", "write_file", "python", "ls",
    )
    named = {
        name for name in native_names
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text, re.I)
    }
    if (
        len(named) == 1
        and re.match(_REQUEST_PREFIX + r"(?:use|call|run)\s+(?:the\s+)?", text, re.I)
        and not re.search(r"[;\n]|\b(?:and\s+then|then\s+use|and\s+use)\b", text, re.I)
    ):
        return frozenset(named)
    # Generic freshness/review language must not outrank a concrete operation
    # above or turn a lookup in the user's own store into a public web search.
    if web_lookup_fallback and not names_personal_store(text):
        return frozenset({"web_search"})
    return None


def requires_external_web_verification(message: str) -> bool:
    """Recognize dated status checks that cannot be answered from local data."""
    return bool(_EXTERNAL_WEB_VERIFICATION.search(str(message or '')))


# Only declared read actions and their read-only arguments may be sealed.
# In particular, exclude manage_memory.command and all mutation parameters.
_SAFE_READ_ARGS = {
    ("manage_notes", "list"): {"archived": bool, "pinned": bool, "label": str},
    ("manage_notes", "view"): {"id": str},
    ("manage_calendar", "list_events"): {
        "start": str, "end": str, "query": str, "calendar": str,
    },
    ("manage_calendar", "list_calendars"): {},
    ("list_email_accounts", None): {},
    ("list_emails", None): {
        "max_results": int, "folder": str, "unread_only": bool, "account": str,
    },
    ("search_emails", None): {"query": str, "folder": str, "max_results": int, "days_back": int, "account": str},
    ("read_email", None): {"uid": str, "message_id": str, "account": str, "folder": str},
    ("download_attachment", None): {
        "uid": str, "index": int, "folder": str, "account": str,
    },
    ("manage_email_state", "list_blocked"): {},
    ("scan_email_unsubscribes", None): {
        "folder": str, "limit": int, "max_scan": int, "account": str,
    },
    ("scan_spam", None): {
        "folder": str, "limit": int, "max_scan": int, "account": str,
    },
    ("manage_tasks", "list"): {},
    ("manage_documents", "list"): {"search": str, "language": str, "limit": int},
    ("manage_documents", "read"): {"document_id": str},
    ("manage_memory", "list"): {},
    ("manage_memory", "search"): {"text": str},
    ("manage_skills", "list"): {},
    ("manage_skills", "search"): {"query": str},
    ("manage_skills", "view"): {"name": str},
    ("list_models", None): {},
    ("list_served_models", None): {},
    ("list_downloads", None): {},
    ("list_serve_presets", None): {},
    ("list_cached_models", None): {},
    ("list_cookbook_servers", None): {},
    ("manage_research", "list"): {"search": str},
    ("manage_settings", "get"): {"key": str},
    ("manage_settings", "list"): {},
    ("manage_settings", "list_tools"): {},
    ("manage_mcp", "list_tools"): {},
    ("list_sessions", None): {},
    ("manage_contact", "list"): {},
    ("manage_contact", "search"): {"query": str},
    ("app_api", "call"): {"method": str, "path": str},
}

_SAFE_APP_API_READ_PATHS = frozenset({
    "/api/hwfit/models?fit_only=true&limit=10&sort=fit",
    "/api/hwfit/system",
    "/api/gallery/library",
})


@dataclass(frozen=True)
class RequiredReadOperation:
    """An exact safe read, never a tool-family or mutation authorization.

    args is a copied, immutable mapping of scalar schema arguments. max_items
    is an optional result-presentation bound, not an invented tool argument.
    """

    tool: str
    args: Mapping[str, object] = field(default_factory=dict)
    max_items: int | None = None

    def __post_init__(self):
        if not isinstance(self.tool, str) or not self.tool:
            raise ValueError("Read tool must be a nonempty name")
        args = dict(self.args)
        action = args.get("action")
        if "action" in args and not isinstance(action, str):
            raise ValueError("Read action must be a string")
        allowed = _SAFE_READ_ARGS.get((canonical_tool(self.tool), action))
        if allowed is None:
            raise ValueError("Tool/action is not a declared safe read")
        for key, value in args.items():
            if key == "action":
                continue
            if key not in allowed or type(value) is not allowed[key]:
                raise ValueError("Unsupported read argument or type")
        if canonical_tool(self.tool) == "app_api" and (
            args.get("method") != "GET"
            or args.get("path") not in _SAFE_APP_API_READ_PATHS
        ):
            raise ValueError("app_api required reads are limited to declared GET endpoints")
        if action in {"view", "read"} and any(
            not isinstance(args.get(key), str) or not args[key].strip() for key in allowed
        ):
            raise ValueError("An exact read requires its explicit identifier")
        if canonical_tool(self.tool) == "read_email" and not any(
            isinstance(args.get(key), str) and args[key].strip()
            for key in ("uid", "message_id")
        ):
            raise ValueError("An exact email read requires uid or message_id")
        if self.max_items is not None and (type(self.max_items) is not int or self.max_items <= 0):
            raise ValueError("max_items must be a positive integer")
        object.__setattr__(self, "args", MappingProxyType(args))

    def audit(self) -> dict:
        return {"tool": self.tool, "args": dict(self.args), "max_items": self.max_items}


_READ_LIST_TARGETS = {
    "notes": ("manage_notes", "list"),
    "saved notes": ("manage_notes", "list"),
    "calendar": ("manage_calendar", "list_events"),
    "calendar events": ("manage_calendar", "list_events"),
    "events": ("manage_calendar", "list_events"),
    "calendars": ("manage_calendar", "list_calendars"),
    "email accounts": ("list_email_accounts", None),
    "configured email accounts": ("list_email_accounts", None),
    "tasks": ("manage_tasks", "list"),
    "scheduled tasks": ("manage_tasks", "list"),
    "automations": ("manage_tasks", "list"),
    "documents": ("manage_documents", "list"),
    "document": ("manage_documents", "list"),
    "doc": ("manage_documents", "list"),
    "docs": ("manage_documents", "list"),
    "memories": ("manage_memory", "list"),
    "saved memories": ("manage_memory", "list"),
    "memory": ("manage_memory", "list"),
    "skills": ("manage_skills", "list"),
    "saved skills": ("manage_skills", "list"),
    "models": ("list_models", None),
    "available models": ("list_models", None),
    "cookbook models": ("list_models", None),
    "cached models": ("list_cached_models", None),
    "locally cached models": ("list_cached_models", None),
    "served models": ("list_served_models", None),
    "downloads": ("list_downloads", None),
    "serve presets": ("list_serve_presets", None),
    "cookbook servers": ("list_cookbook_servers", None),
    "configured cookbook servers": ("list_cookbook_servers", None),
    "saved research reports": ("manage_research", "list"),
    "research reports": ("manage_research", "list"),
    "chat sessions": ("list_sessions", None),
    "sessions": ("list_sessions", None),
    "chats": ("list_sessions", None),
    "contacts": ("manage_contact", "list"),
}
_FUZZY_SAFE_READS = {
    "notes": ("manage_notes", "list"),
    "calendar": ("manage_calendar", "list_events"),
    "tasks": ("manage_tasks", "list"),
    "documents": ("manage_documents", "list"),
    "memory": ("manage_memory", "list"),
    "skills": ("manage_skills", "list"),
    "email": ("list_email_accounts", None),
    "cookbook_admin": ("list_cookbook_servers", None),
}
_EXACT_READ_REPEAT = re.compile(
    r"(?:and[\s,]+)?" + _REQUEST_PREFIX + r"(?:(?:do|repeat|show|list|read)\s+(?:it|that|them|those|the same (?:short\s+)?(?:list|titles?|items?|names?))"
    r"(?:\s+ag(?:ain|ian|en))?|refresh\s+(?:(?:it|that|them|those)"
    r"|(?:(?:that|the)\s+)?same(?:\s+[A-Za-z][A-Za-z-]*){0,4}\s+list"
    r"|that(?:\s+[A-Za-z][A-Za-z-]*){0,4}\s+list)"
    r"|(?:the\s+)?same\s+(?:short\s+)?(?:list|ones?|items?|results?|names?)\s+again"
    r"|(?:just\s+)?(?:[1-9]\d*|one|two|three|four|five|six|seven|eight|nine|ten)"
    r"\s+(?:titles?|names?|items?|entries?)\s+(?:like|as)\s+before"
    r"|(?:those|them)\s+ag(?:ain|ian|en)"
    r"|same\s+(?:again|as\s+before)|again)"
    r"(?:\s+for\s+me)?(?:\s*[,;]\s*same\s+(?:limit|cap))?"
    r"(?:\s+(?:pls|please))?[.!?]*", re.I,
)

_READ_COUNT_WORDS = {word: index for index, word in enumerate(
    ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"), 1)}
_READ_ORDINAL_WORDS = {word: index for index, word in enumerate(
    ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth"), 1)}
_READ_COUNT = r"(?:[1-9]\d*|" + "|".join(_READ_COUNT_WORDS) + r")"
_READ_PRESENTATION_SUFFIX = re.compile(
    r"[,.;?]\s*(?:read[- ]only(?:\s+inspection)?(?:\s+(?:please|pls))?"
    r"(?:\s*;\s*do\s+not\s+change\s+data\s+or\s+send\s+messages)?"
    r"|do\s+not\s+change\s+data\s+or\s+send\s+messages"
    r"|keep\s+the\s+answer\s+concise"
    r"|just\s+(?:the\s+)?short\s+(?:versions?|forms?|ones?)"
    r"|return\s+only\s+(?:their\s+)?(?:titles?|items?|results?|entries?|"
    r"names?(?:\s*(?:and|\+)\s+status(?:es)?)?|things?|accounts?)"
    r"|(?:return\s+)?(?:just\s+)?(?:at\s+most|up\s+to)\s+(?P<count>" + _READ_COUNT + r")"
    r"(?:\s+(?:short\s+)?(?:titles?|items?|results?|entries?|"
    r"names?(?:\s*(?:and|\+)\s+status(?:es)?)?|things?|accounts?))?)"
    r"[.!?]*\s*$", re.I,
)


def _terminal_clause_match(text: str, core_pattern: str) -> re.Match[str] | None:
    """Match a terminal clause after removing its ambiguous punctuation tail."""
    end = len(text)
    while end and text[end - 1].isspace():
        end -= 1
    while end and text[end - 1] in ".!?":
        end -= 1
    possessive = core_pattern.replace(r"\s+", r"\s++").replace(r"\s*", r"\s*+")
    # An optional leading " and" branch must start at a whitespace boundary;
    # otherwise search retries the whole possessive run at every suffix.
    possessive = possessive.replace(r"|\s++and", r"|(?<!\s)\s++and")
    return re.search(possessive + r"$", text[:end], re.I)


def _strip_terminal_but(text: str) -> str:
    end = len(text)
    while end and text[end - 1].isspace():
        end -= 1
    if end < 3 or text[end - 3:end].casefold() != "but":
        return text
    start = end - 3
    if start == 0 or not text[start - 1].isspace():
        return text
    while start and text[start - 1].isspace():
        start -= 1
    return text[:start]


def _read_request_and_limit(message: str) -> tuple[str, int | None]:
    """Strip only whole, known presentation/safety suffixes, never actions."""
    text = _normalize_request_lead(message)
    if lead := _CONVERSATIONAL_ACTION_LEAD.fullmatch(text):
        text = lead["request"].strip()
    maximum = None
    short_few_suffix = re.search(
        r"[?.,;]\s*keep\s+it\s+short\s*[—–-]\s*(?:a\s+)?few\s+"
        r"(?:titles?|names?|items?|entries?)[.!?]*\s*$",
        text,
        re.I,
    )
    if short_few_suffix:
        maximum = 3
        text = text[:short_few_suffix.start()].strip()
    tops_suffix = re.search(
        r"[,;]\s*(?P<count>" + _READ_COUNT + r")\s+tops+s?[.!?]*\s*$",
        text,
        re.I,
    )
    if tops_suffix:
        raw = tops_suffix["count"].casefold()
        parsed = int(raw) if raw.isdecimal() else _READ_COUNT_WORDS[raw]
        maximum = parsed if maximum is None else min(maximum, parsed)
        text = text[:tops_suffix.start()].strip()
    handful_suffix = re.search(
        r"[,;]\s*(?:only|just)\s+(?:a\s+)?(?:handful|few)\s+of\s+"
        r"(?:titles?|names?|items?|entries?)(?:\s+(?:please|pls|plz))?[.!?]*\s*$",
        text,
        re.I,
    )
    if handful_suffix:
        maximum = 3 if maximum is None else min(maximum, 3)
        text = text[:handful_suffix.start()].strip()
    text = re.sub(
        r"[?.,;]\s*(?:short|brief|quick)\s+(?:answer|version)(?:\s+(?:please|pls|plz))?[.!?]*\s*$",
        "",
        text,
        flags=re.I,
    ).strip()
    keep_few_suffix = re.search(
        r"[,.;?]\s*keep\s+(?:it|them|the\s+(?:answer|list))\s+to\s+a\s+few"
        r"(?:\s+(?:titles?|names?|items?|entries?))?[.!?]*\s*$",
        text,
        re.I,
    )
    if keep_few_suffix:
        maximum = 3
        text = text[:keep_few_suffix.start()].strip()
    few_suffix = _terminal_clause_match(
        text,
        r"[,.;?]\s*(?:(?:only|just)\s+)?(?:(?:list|show)\s+(?:me\s+)?)?a\s+few"
        r"(?:\s+(?:task\s+)?(?:names?|items?|results?|entries?))?"
        r"(?:\s+and\s+(?:whether|if)\s+[^.;\n]+)?",
    )
    if few_suffix:
        maximum = 3
        text = text[:few_suffix.start()].strip()
    text = re.sub(
        r"[,.;]\s*read[- ]only\s+and\s+(?:keep\s+it\s+)?(?:short|brief|concise)"
        r"(?:\s+(?:please|pls|plz))?[.!?]*\s*$",
        "",
        text,
        flags=re.I,
    ).strip()
    terminal_read_only = _terminal_clause_match(
        text,
        r"[.;]\s*read[- ]only(?:\s+(?:please|pls|plz))?\s*,?\s*"
        r"(?:(?:and\s+)?(?:do\s+not|don['’]?t|dont)\s+"
        r"(?:change|edit|modify)(?:\s+or\s+send)?\s+(?:anything|data))?",
    )
    if terminal_read_only:
        text = text[:terminal_read_only.start()].strip()
    # Explanatory/safety tails do not alter a preceding exact read request.
    safety_tail = _terminal_clause_match(
        text,
        r"(?:(?:[,;]\s*(?:and\s+)?|\s+and\s+))?(?:do\s+not|don['’]?t|dont)\s+"
        r"(?:touch|change|edit|modify)(?:\s+(?:anything|data|them))?(?:\s+yet)?",
    )
    if safety_tail:
        text = text[:safety_tail.start()].strip()
    text = re.sub(
        r"[.!?]\s*+read[- ]only(?:\s++(?:please|pls|plz))?\s*+(?:,\s*+)?"
        r"(?:do\s+not|don['’]?t|dont)\s+(?:change|edit|modify)\s+"
        r"(?:or\s+send\s+)?anything[.!?]*\s*$",
        "", text, flags=re.I,
    ).strip()
    for terminal_core in (
        r"(?:(?:[,;]\s*(?:and\s+)?|\s+and\s+))?no\s+changes?",
        r"(?:(?:[,;]\s*(?:and\s+)?|\s+and\s+))?no\s+edits?",
    ):
        terminal_match = _terminal_clause_match(text, terminal_core)
        if terminal_match:
            text = text[:terminal_match.start()].strip()
    text = re.sub(
        r"[,;]\s*no\s+edits?[.!?]*\s*$", "", text, flags=re.I,
    ).strip()
    terminal_no_write = _terminal_clause_match(
        text, r"(?:(?:[,;]\s*(?:and\s+)?|\s+and\s+))?no\s+writes?"
    )
    if terminal_no_write:
        text = text[:terminal_no_write.start()].strip()
    text = re.sub(
        r"[.!?]\s*(?:i['’]?m|i\s+am)\s+(?:just\s+)?checking\b[^\n]*$",
        "", text, flags=re.I,
    ).strip()
    text = re.sub(
        r"[.!?]\s*i\s+(?:just\s+)?(?:want|wanted)\s+to\s+"
        r"(?:verify|confirm|check)\b[^\n]*$",
        "", text, flags=re.I,
    ).strip()
    text = re.sub(
        r"[.;]\s*just\s+(?:the\s+)?(?:server\s+)?names?\s+and\s+"
        r"(?:if|whether)\s+(?:they(?:['’]?re|\s+are)|each\s+is)\s+"
        r"(?:up|running|available)[.!?]*\s*$",
        "",
        text,
        flags=re.I,
    ).strip()
    short_lines = _terminal_clause_match(
        text, r"[,;]\s*(?:keep\s+(?:them|it)\s+)?short\s+lines?\s*,?"
    )
    if short_lines:
        text = text[:short_lines.start()].strip()
    text = re.sub(
        r"[,.;]\s*keep\s+(?:the\s+answer|it|them)\s+short[.!?]*\s*$",
        "",
        text,
        flags=re.I,
    ).strip()
    while match := _READ_PRESENTATION_SUFFIX.search(text):
        if match["count"]:
            raw = match["count"].lower()
            count = int(raw) if raw.isdecimal() else _READ_COUNT_WORDS[raw]
            maximum = count if maximum is None else min(maximum, count)
        text = text[:match.start()].strip()
    approximate_limit = re.search(
        r"[,.;?]\s*(?:show\s+me\s+)?like\s+(" + _READ_COUNT + r")\s+"
        r"(?:short\s+)?(?:things?|items?|entries?|names?)\s+"
        r"(?:max(?:imum)?|at\s+most|tops?)[.!?]*\s*$",
        text,
        re.I,
    )
    if approximate_limit:
        raw = approximate_limit.group(1)
        maximum = int(raw) if raw.isdecimal() else _READ_COUNT_WORDS[raw.lower()]
        text = text[:approximate_limit.start()].strip()
    natural_limit = re.search(
        r"(?:[,.;?]|[—–-]|(?<!\s)\s++but\s++|(?<!\s)\s++)\s*+(?:"
        r"(?:i\s++)?only\s++(?:need|want|show(?:\s++me)?)?\s*+(?:(?:the\s++)?first\s++)?"
        r"|just\s++(?:(?:the\s++)?first\s++)?|(?:show\s++me\s++)?like\s++|no\s++more\s++than\s++"
        r"|cap(?:\s++(?:it|them|the\s++(?:answer|list)))?\s++at\s++"
        r"|(?:maybe\s++)?(?:first|same)\s++)"
        r"(" + _READ_COUNT + r")"
        r"(?:\s+(?:short\s+)?(?:titles?|items?|results?|entries?|names?|ones?|bits?|things?))?"
        r"(?:\s*(?:and|\+)\s+(?:their\s+)?(?:status(?:es)?|states?))?"
        r"(?:\s*(?:\+|and)\s+(?:whether|if)\s+[^.;\n]+)?[.!?]*\s*$",
        text, re.I,
    )
    if not natural_limit:
        natural_limit = re.search(
            r"(?:[,.;?]|[—–-])\s*(" + _READ_COUNT + r")\s+"
        r"(?:short\s+)?(?:titles?|items?|results?|entries?|names?|ones?|bits?|things?)"
        r"(?:\s*(?:and|\+)\s+(?:their\s+)?(?:status(?:es)?|states?))?"
            r"(?:\s*(?:\+|and)\s+(?:whether|if)\s+[^.;\n]+)?[.!?]*\s*$",
            text, re.I,
        )
    if natural_limit:
        base_request = text[:natural_limit.start()].rstrip(' ,.;?—–-')
        if re.fullmatch(_REQUEST_PREFIX + r"repeat\s+it", base_request, re.I):
            natural_limit = None
    if natural_limit:
        raw = natural_limit.group(1)
        value = int(raw) if raw.isdecimal() else _READ_COUNT_WORDS[raw.lower()]
        maximum = value if maximum is None else min(maximum, value)
        text = base_request
    compact_limit = re.search(
        r"(?:[,.;?]|[—–-])\s*(?:(?:just|only|max(?:imum)?(?:\s+of)?)\s+(" + _READ_COUNT + r")"
        r"(?:\s+(?:short\s+)?(?:titles?|items?|results?|entries?|names?|ones?|things?))?"
        r"(?:\s*(?:and|\+)\s+(?:their\s+)?(?:status(?:es)?|states?))?"
        r"|(" + _READ_COUNT + r")\s+(?:short\s+)?(?:titles?|items?|results?|entries?|names?|ones?|things?)?"
        r"(?:\s*(?:and|\+)\s+(?:their\s+)?(?:status(?:es)?|states?))?\s*"
        r"(?:max(?:imum)?|only|at\s+most|tops?))(?:\s+and\s+keep\s+(?:it|them)\s+short)?[.!?]*\s*$",
        text, re.I,
    )
    if compact_limit:
        raw = next(group for group in compact_limit.groups() if group)
        maximum = int(raw) if raw.isdecimal() else _READ_COUNT_WORDS[raw.lower()]
        text = text[:compact_limit.start()].strip()
    conversational_limit = re.search(
        r"(?:[,.;?]\s*|(?<!\s)\s++)(?:maybe\s+)?(?:keep\s+it\s+to\s+|stick\s+to\s+|(?:first|top)\s+)"
        r"(" + _READ_COUNT + r")(?:\s+(?:short\s+)?(?:titles?|items?|results?|entries?|names?|ones?))?"
        r"[.!?]*\s*$",
        text, re.I,
    )
    if conversational_limit:
        raw = conversational_limit.group(1)
        value = int(raw) if raw.isdecimal() else _READ_COUNT_WORDS[raw.lower()]
        maximum = value if maximum is None else min(maximum, value)
        text = text[:conversational_limit.start()].rstrip(' ,.;?')
        text = _strip_terminal_but(text)
    need_limit = re.search(
        r"[.!?]\s*(?:i\s+)?only\s+need\s+(" + _READ_COUNT + r")"
        r"(?:\s+(?:short\s+)?(?:titles?|items?|results?|entries?|names?|ones?))?"
        r"[.!?]*\s*$",
        text, re.I,
    )
    if need_limit:
        raw = need_limit.group(1)
        value = int(raw) if raw.isdecimal() else _READ_COUNT_WORDS[raw.lower()]
        maximum = value if maximum is None else min(maximum, value)
        text = text[:need_limit.start()].strip()
    short_count_limit = re.search(
        r"[,;]\s*(" + _READ_COUNT + r")\s+short\s+(?:ones?|items?|entries?)"
        r"[.!?]*\s*$",
        text, re.I,
    )
    if short_count_limit:
        raw = short_count_limit.group(1)
        value = int(raw) if raw.isdecimal() else _READ_COUNT_WORDS[raw.lower()]
        maximum = value if maximum is None else min(maximum, value)
        text = text[:short_count_limit.start()].strip()
    bare_repeat_limit = re.search(r"[,;]\s*(" + _READ_COUNT + r")[.!?]*\s*$", text, re.I)
    if bare_repeat_limit:
        raw = bare_repeat_limit.group(1)
        value = int(raw) if raw.isdecimal() else _READ_COUNT_WORDS[raw.lower()]
        maximum = value if maximum is None else min(maximum, value)
        text = text[:bare_repeat_limit.start()].strip()
    field_limit = re.search(
        r"[?,;]\s*(?:(?:up\s+to|at\s+most|no\s+more\s+than)\s+)?"
        r"(" + _READ_COUNT + r")\s+(?:short\s+)?(?:titles?|names?|items?|entries?)"
        r"(?:\s*(?:\+|and|with)\s+(?:status(?:es)?|times?))?"
        r"(?:\s+(?:max(?:imum)?|at\s+most|tops?))?[.!?]*\s*$",
        text, re.I,
    )
    if field_limit:
        raw = field_limit.group(1)
        value = int(raw) if raw.isdecimal() else _READ_COUNT_WORDS[raw.lower()]
        maximum = value if maximum is None else min(maximum, value)
        text = text[:field_limit.start()].strip()
    compact_field_limit = re.search(
        r"[,?;]\s*(?:max(?:imum)?|up\s+to|at\s+most)\s+"
        r"(" + _READ_COUNT + r")\s+(?:with\s+(?:times?|status(?:es)?))?"
        r"[.!?]*\s*$",
        text, re.I,
    )
    if compact_field_limit:
        raw = compact_field_limit.group(1)
        value = int(raw) if raw.isdecimal() else _READ_COUNT_WORDS[raw.lower()]
        maximum = value if maximum is None else min(maximum, value)
        text = text[:compact_field_limit.start()].strip()
    text = re.sub(
        r"[?.,;]\s*(?:short|brief|quick)\s+(?:answer|version)"
        r"(?:\s+(?:please|pls|plz))?[.!?]*\s*$",
        "",
        text,
        flags=re.I,
    ).strip()
    return text, maximum


def _exact_id_read(text: str, maximum: int | None) -> RequiredReadOperation | None:
    match = re.fullmatch(
        _REQUEST_PREFIX + r"(?:read|view|open)\s+(?:the\s+)?(?P<kind>note|document|skill)\s+"
        r"(?:(?:with\s+)?id\s+)(?P<id>[A-Za-z0-9][A-Za-z0-9_-]*)[.!?]*", text, re.I,
    )
    if not match:
        return None
    tool, action, key = {
        "note": ("manage_notes", "view", "id"),
        "document": ("manage_documents", "read", "document_id"),
        "skill": ("manage_skills", "view", "name"),
    }[match["kind"].lower()]
    return RequiredReadOperation(tool, {"action": action, key: match["id"]}, maximum)


def _prior_email_rows(history: Iterable) -> list[dict[str, str]]:
    """Extract identifiers from the latest successful server-owned email list."""
    rows = list(history)
    # Inside the clean loop, prior calls/results are already reconstructed as
    # OpenAI assistant/tool messages rather than wrapped in persisted metadata.
    # Treat that complete message sequence as one clean_v3_turn candidate.
    scan_rows = rows + [{"role": "assistant", "metadata": {"clean_v3_turn": rows}}]
    for row in reversed(scan_rows):
        metadata = row.get("metadata") if isinstance(row, dict) else getattr(row, "metadata", None)
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (TypeError, json.JSONDecodeError):
                metadata = {}
        outputs = []
        for event in reversed((metadata or {}).get("tool_events") or []):
            if (canonical_tool(event.get("tool", "")) != "list_emails"
                    or event.get("error") is True
                    or event.get("exit_code") not in (None, 0)):
                continue
            outputs.append(event.get("output"))
        saved = (metadata or {}).get("clean_v3_turn") or []
        call_names = {}
        for message in saved:
            if message.get("role") != "assistant":
                continue
            for call in message.get("tool_calls") or []:
                call_names[call.get("id")] = canonical_tool(
                    ((call.get("function") or {}).get("name") or "")
                )
        for message in reversed(saved):
            if (message.get("role") == "tool"
                    and call_names.get(message.get("tool_call_id")) == "list_emails"):
                outputs.append(message.get("content"))
        for raw_output in outputs:
            output = str(raw_output or "")
            try:
                output = str((json.loads(output) or {}).get("stdout") or output)
            except (TypeError, json.JSONDecodeError):
                pass
            found = []
            current = None
            for line in output.splitlines():
                uid = re.match(r"\s*UID:\s*(\S+)", line, re.I)
                if uid:
                    current = {"uid": uid[1]}
                    found.append(current)
                    continue
                account = re.match(r"\s*Account:\s*(.*?)\s*(?:<([^>]+)>)?\s*$", line, re.I)
                if account and current:
                    current["account"] = (account[2] or account[1]).strip()
            if found:
                return found
    return []


def _ordinal_email_read(text: str, history: Iterable, maximum: int | None) -> RequiredReadOperation | None:
    match = _ORDINAL_EMAIL_FOLLOWUP.fullmatch(text)
    if not match:
        return None
    raw = match["ordinal"].lower()
    index = _READ_ORDINAL_WORDS.get(raw)
    if index is None:
        index = int(re.match(r"\d+", raw)[0])
    rows = _prior_email_rows(history)
    if index < 1 or index > len(rows):
        return None
    return RequiredReadOperation("read_email", rows[index - 1], maximum)


def _prior_visible_collection_ids(history: Iterable) -> tuple[str, list[str]]:
    """Read entity IDs only from the latest assistant list visible to the user."""
    for row in reversed(tuple(history)):
        role = row.get("role") if isinstance(row, dict) else getattr(row, "role", "")
        if role != "assistant":
            continue
        content = row.get("content", "") if isinstance(row, dict) else getattr(row, "content", "")
        text = str(content or "")
        for family, prefix in (("notes", "note"), ("documents", "document")):
            ids = re.findall(rf"\]\(#(?:{prefix})-([A-Za-z0-9_-]+)\)", text, re.I)
            if ids:
                return family, ids
    return "", []


def _ordinal_visible_collection_read(
    text: str, history: Iterable, maximum: int | None,
) -> RequiredReadOperation | None:
    """Bind 'open the second/top one' to the latest visible Notes/Documents list."""
    match = re.fullmatch(
        _REQUEST_PREFIX
        + r"(?:show|open|read|view)(?:\s+me)?\s+(?:the\s+)?"
          r"(?P<ordinal>top|last|first|second|third|fourth|fifth|sixth|seventh|"
          r"eighth|ninth|tenth|[1-9]\d*(?:st|nd|rd|th))\s+one"
          r"(?:\s+(?:again|you\s+listed))?[.!?]*",
        text,
        re.I,
    )
    if not match:
        return None
    family, identifiers = _prior_visible_collection_ids(history)
    if not identifiers:
        return None
    raw = match["ordinal"].lower()
    if raw == "top":
        index = 1
    elif raw == "last":
        index = len(identifiers)
    else:
        index = _READ_ORDINAL_WORDS.get(raw)
        if index is None:
            index = int(re.match(r"\d+", raw)[0])
    if index < 1 or index > len(identifiers):
        return None
    if family == "notes":
        return RequiredReadOperation(
            "manage_notes", {"action": "view", "id": identifiers[index - 1]}, maximum,
        )
    return RequiredReadOperation(
        "manage_documents",
        {"action": "read", "document_id": identifiers[index - 1]},
        maximum,
    )


def _prior_skill_names(history: Iterable) -> list[str]:
    rows = list(history)
    scan_rows = rows + [{"role": "assistant", "metadata": {"clean_v3_turn": rows}}]
    for row in reversed(scan_rows):
        metadata = row.get("metadata") if isinstance(row, dict) else getattr(row, "metadata", None)
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (TypeError, json.JSONDecodeError):
                metadata = {}
        outputs = [
            event.get("output") for event in reversed((metadata or {}).get("tool_events") or [])
            if canonical_tool(event.get("tool", "")) == "manage_skills"
            and event.get("error") is not True and event.get("exit_code") in (None, 0)
            and '"action": "list"' in str(event.get("command") or "")
        ]
        saved = (metadata or {}).get("clean_v3_turn") or []
        list_calls = set()
        for message in saved:
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                try:
                    args = json.loads(function.get("arguments") or "{}")
                except (TypeError, json.JSONDecodeError):
                    args = {}
                if (canonical_tool(function.get("name", "")) == "manage_skills"
                        and args.get("action") in {"list", "index"}):
                    list_calls.add(call.get("id"))
        outputs.extend(
            message.get("content") for message in reversed(saved)
            if message.get("role") == "tool" and message.get("tool_call_id") in list_calls
        )
        for raw_output in outputs:
            output = str(raw_output or "")
            try:
                parsed = json.loads(output)
                output = str(parsed.get("results") or parsed.get("response") or parsed.get("output") or output)
            except (TypeError, json.JSONDecodeError):
                pass
            names = [match[1].strip() for match in re.finditer(r"^- \*\*([^*]+)\*\*", output, re.M)]
            if names:
                return names
    return []


def _ordinal_skill_view(text: str, history: Iterable, maximum: int | None) -> RequiredReadOperation | None:
    match = _ORDINAL_SKILL_FOLLOWUP.fullmatch(text)
    if not match:
        return None
    raw = match["ordinal"].lower()
    index = _READ_ORDINAL_WORDS.get(raw)
    if index is None:
        index = int(re.match(r"\d+", raw)[0])
    names = _prior_skill_names(history)
    if index < 1 or index > len(names):
        return None
    return RequiredReadOperation("manage_skills", {"action": "view", "name": names[index - 1]}, maximum)


def _complete_fuzzy_read_family(text: str) -> str | None:
    """Accept a typo only when the whole read target is accounted for."""
    match = re.fullmatch(
        _REQUEST_PREFIX + r"(?P<action>[A-Za-z]+)\s+(?:me\s+)?(?:(?:my|the|all)\s+)?"
        r"(?P<target>[A-Za-z]+(?:\s+[A-Za-z]+){0,4}?)(?:\s+again)?[.!?]*", text, re.I,
    )
    if not match:
        return None
    action = match["action"].lower()
    if action not in {"list", "show", "read"}:
        matches = {verb for verb in ("list", "show", "read")
                   if _damerau_distance(action, verb) == 1}
        if len(action) < 3 or len(matches) != 1:
            return None
    words = match["target"].lower().split()
    wrappers = {
        "notes": {"saved"}, "tasks": {"scheduled"}, "memory": {"saved"},
        "skills": {"saved"},
        "cookbook_admin": {"configured", "servers", "server"}, "calendar": {"events", "event"},
        "email": {"configured", "accounts", "account", "addresses", "address"},
    }
    hits = set()
    for family, terms in _FUZZY_FAMILY_TERMS.items():
        core = [word for word in words if word not in wrappers.get(family, set())]
        joined = "".join(core)
        if not joined:
            continue
        for term in terms:
            distance = _damerau_distance(joined, term.replace(" ", ""))
            limit = 1 if max(len(joined), len(term)) <= 6 else 2
            if distance <= limit:
                hits.add(family)
    return next(iter(hits)) if len(hits) == 1 else None


def _fuzzy_possessive_lookup_family(text: str) -> str | None:
    """Resolve typoed family nouns in complete personal lookup questions."""
    match = re.fullmatch(
        r"\s*" + _REQUEST_PREFIX + r"(?:what|wat|wht)(?:['’]?s|\s+(?:is|are))?\s+"
        r"(?:on|in)\s+(?:my|the)\s+(?P<target>[A-Za-z]+)"
        r"(?:\s+(?:today|tomorr?ow|tomorow)(?:\s+(?:morning|afternoon|evening))?|"
        r"\s+(?:this|next)\s+(?:week|month))?"
        r"[.!?]*\s*",
        text,
        re.I,
    )
    if not match:
        return None
    family = _fuzzy_family(match["target"])
    return family if family in _FUZZY_SAFE_READS else None


def _latest_successful_read_operation(history: Iterable) -> RequiredReadOperation | None:
    """Recover the exact latest safe reader from persisted execution evidence."""
    for row in reversed(tuple(history)):
        metadata = row.get("metadata") if isinstance(row, dict) else getattr(row, "metadata", None)
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (TypeError, json.JSONDecodeError):
                metadata = {}
        for event in reversed((metadata or {}).get("tool_events") or []):
            if event.get("error") is True or event.get("exit_code") not in (None, 0):
                continue
            tool = canonical_tool(event.get("tool", ""))
            command = event.get("command") or {}
            if isinstance(command, str):
                try:
                    command = json.loads(command)
                except (TypeError, json.JSONDecodeError):
                    command = {}
            command = dict(command) if isinstance(command, dict) else {}
            action = command.get("action")
            allowed = _SAFE_READ_ARGS.get((tool, action))
            if allowed is None:
                allowed = _SAFE_READ_ARGS.get((tool, None))
                if allowed is None:
                    continue
                command.pop("action", None)
            safe_args = {
                key: value for key, value in command.items()
                if key == "action" or (key in allowed and type(value) is allowed[key])
            }
            try:
                return RequiredReadOperation(tool, safe_args)
            except ValueError:
                continue
    return None


def _natural_safe_inventory_operation(message: str, maximum: int | None = None) -> RequiredReadOperation | None:
    """Resolve natural, explicitly read-only inventory requests.

    The exact grammars below handle terse commands well, but people also say
    things such as ``glance at my calendar`` or put a result limit and safety
    constraint in separate sentences. Treat those as one bounded inventory
    intent without inferring arbitrary actions from family nouns alone.
    """
    text = _normalize_request_lead(message)
    if (
        re.match(r"^\s*how\s+do\s+(?:i|we|you)\b", text, re.I)
        or re.search(r"\bexcept\b", text, re.I)
        or re.search(r"\b(?:with\s+)?id\s+[A-Za-z0-9_-]+\b", text, re.I)
        or re.search(r"\b(?:tagged|labelled|labeled)\b", text, re.I)
        or re.search(
            r"\b(?:at\s+most|up\s+to|max(?:imum)?(?:\s+of)?|top|only|first)\s+zero\b|"
            r"\b0\s+(?:titles?|items?|entries?|events?|names?|rows?|notes?)\b",
            text,
            re.I,
        )
    ):
        return None
    count_pattern = _READ_COUNT
    limit_match = re.search(
        r"\b(?:at\s+most|up\s+to|max(?:imum)?(?:\s+of)?|top|only|first)\s+"
        r"(?P<count>" + count_pattern + r")\b|"
        r"\bcap(?:ped)?(?:\s+(?:it|them))?\s+(?:at|to)\s+(?P<capped>" + count_pattern + r")\b|"
        r"\b(?P<trailing>" + count_pattern + r")\s+"
        r"(?:titles?|items?|entries?|events?|names?|rows?|bullets?)\s+max\b|"
        r"\b(?P<plain>" + count_pattern + r")\s+bullets?\b",
        text,
        re.I,
    )
    if limit_match:
        raw = (
            limit_match["count"] or limit_match["capped"]
            or limit_match["trailing"] or limit_match["plain"]
        ).casefold()
        parsed = int(raw) if raw.isdecimal() else _READ_COUNT_WORDS[raw]
        maximum = parsed if maximum is None else min(maximum, parsed)
    elif like_limit := re.search(
        r"\blike\s+(?P<count>" + count_pattern + r")\s+"
        r"(?:titles?|items?|entries?|events?|names?|rows?|bullets?)\b",
        text,
        re.I,
    ):
        raw = like_limit["count"].casefold()
        parsed = int(raw) if raw.isdecimal() else _READ_COUNT_WORDS[raw]
        maximum = parsed if maximum is None else min(maximum, parsed)
    elif re.search(
        r"\b(?:a\s+few|few|a\s+handful|handful|a\s+couple|couple|a\s+cpl|cpl)\b",
        text,
        re.I,
    ):
        approximate = 2 if re.search(r"\b(?:a\s+cpl|cpl)\b", text, re.I) else 3
        maximum = approximate if maximum is None else min(maximum, approximate)

    # Ignore explicit prohibitions while checking for a compound read+write
    # request. A real positive mutation leaves this to the normal router.
    positive = re.sub(
        r"\b(?:do\s+not|don['’]?t|dont|no)\b[^.!?\n]*",
        "",
        text,
        flags=re.I,
    )
    if re.search(
        r"\b(?:add|create|edit|modify|delete|remove|send|message|change|write|update)\b",
        positive,
        re.I,
    ):
        return None

    read_signal = bool(re.search(
        r"\b(?:list(?:ing)?|show|see|glance|peek|look|view|rundown|relist|pull(?:\s+up)?|gimme)\b",
        positive,
        re.I,
    ))
    read_signal = read_signal or bool(re.search(
        r"\b(?:anythin(?:g)?\s+on\s+(?:my|our)|what\s+(?:have|do)\s+"
        r"(?:you|u|i|we)\b[^?!.]{0,80}\b(?:stored|saved|got))\b",
        positive,
        re.I,
    ))
    read_signal = read_signal or bool(re.search(
        r"\bwhat\s+(?:notes?|events?)\s+do\s+(?:i|we)\s+have\b",
        positive,
        re.I,
    ))
    family = None
    if re.search(r"\bcookbo{1,2}k\s+servers?\b", positive, re.I):
        family = "cookbook_admin"
        read_signal = True
    elif re.search(r"\bcalend(?:ar|er)\b", positive, re.I):
        family = "calendar"
    elif re.search(r"\bwhat\s+events?\s+do\s+(?:i|we)\s+have\b", positive, re.I):
        family = "calendar"
    elif re.search(r"\b(?:documents?|docs?|editor)\b", positive, re.I):
        family = "documents"
    elif re.search(r"\bnotes?\b", positive, re.I):
        family = "notes"
    elif re.search(r"\b(?:memory|memories|mems)\b", positive, re.I):
        family = "memory"
    elif re.search(r"\b(?:my|our)\s+noes\b", positive, re.I):
        # High-confidence typo repair, not every use of the ordinary word.
        family = "notes"
    if not family or not read_signal:
        return None

    # Do not collapse a genuine cross-family request into one inventory.
    named = {
        candidate for candidate in _FAMILY_WORDS
        if re.search(_FAMILY_WORDS[candidate], positive, re.I)
    }
    if len(named) > 1:
        return None

    tool, action = _FUZZY_SAFE_READS[family]
    return RequiredReadOperation(tool, {"action": action} if action else {}, maximum)


def required_read_operation_for_request(message: str, history: Iterable = ()) -> RequiredReadOperation | None:
    """Resolve complete list/read requests, or repeat an exact prior read.

    Do not invent identifiers, resolve relative dates, extract a read from a
    compound request, or turn a summary/search into an obligatory operation.
    """
    normalized_text = _normalize_request_lead(message)
    text, maximum = _read_request_and_limit(message)
    rows = list(history)
    if re.search(
        r"\bwithout\s+(?:using|trusting|relying\s+on)\s+(?:my\s+)?memory\b|"
        r"\b(?:do\s+not|don['’]?t|dont|never)\b[^.;\n]{0,80}\bfrom\s+memory(?:\s+alone)?\b|"
        r"\b(?:do\s+not|don['’]?t|dont|never)\s+(?:use|trust|rely\s+on)\s+(?:my\s+)?memory\b",
        text,
        re.I,
    ):
        # These are source-grounding constraints, not requests to read the
        # user's private Odysseus memory store.  Long research/artifact jobs
        # often also contain words such as "list" or "show", which must not
        # convert the evidence constraint into a sealed personal-data read.
        return None
    # A user can switch families in one conversation and then explicitly come
    # back using ordinary shorthand (including a one-edit typo):
    # ``back to emaol show 2 latest``.  This is a complete inbox inventory
    # request, not a repeat of the earlier account-address lookup and not a
    # notes continuation merely because notes was the immediately prior turn.
    return_to_latest_email = re.fullmatch(
        _REQUEST_PREFIX
        + r"(?:(?:back|return|switch)(?:\s+back)?\s+to\s+)?"
          r"(?P<family>[A-Za-z]+)\s+"
          r"(?:show|list|check|get)\s+"
          r"(?P<count>" + _READ_COUNT + r")\s+"
          r"(?:latest|newest|recent)(?:\s+(?:emails?|messages?))?[.!?]*",
        text,
        re.I,
    )
    if (
        return_to_latest_email
        and _fuzzy_family(return_to_latest_email["family"]) == "email"
    ):
        raw_count = return_to_latest_email["count"].casefold()
        count = int(raw_count) if raw_count.isdecimal() else _READ_COUNT_WORDS[raw_count]
        if maximum is not None:
            count = min(count, maximum)
        return RequiredReadOperation("list_emails", {"max_results": count}, count)
    latest_inbox_inventory = re.fullmatch(
        _REQUEST_PREFIX
        + r"(?:list|show|check|get)\s+(?:me\s+)?(?:my|our|the)?\s*"
          r"(?:latest|newest|recent)\s+"
          r"(?P<count>" + _READ_COUNT + r")\s+"
          r"(?:inbox\s+)?(?:emails?|messages?)"
          r"(?:\s+with\s+(?:sender|from)(?:\s+(?:and|,)\s+(?:subject|title))?)?"
          r"[.!?]*",
        text,
        re.I,
    )
    if latest_inbox_inventory:
        raw_count = latest_inbox_inventory["count"].casefold()
        count = int(raw_count) if raw_count.isdecimal() else _READ_COUNT_WORDS[raw_count]
        if maximum is not None:
            count = min(count, maximum)
        return RequiredReadOperation(
            "list_emails", {"folder": "INBOX", "max_results": count}, count,
        )
    if selected_tools_for_request(message) == frozenset({"web_fetch"}):
        # A bounded comparison of explicit public URLs is already a complete
        # web operation. Phrases such as "do not answer from memory" describe
        # evidence discipline and must not be parsed as a request to list the
        # user's saved Odysseus memories.
        return None
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:show\s+me\s+)?what(?:['’]?s|s|\s+is)\s+scheduled[?!.]*",
        text,
        re.I,
    ) or re.fullmatch(
        _REQUEST_PREFIX + r"what(?:['’]?s|s|\s+is|\s+have\s+i\s+got)\s+coming\s+up"
        r"(?:\s+over\s+the\s+next\s+(?:[1-9]\d*|one|two|three|four|five|six|seven)\s+days?)?"
        r"[?!.]*",
        text,
        re.I,
    ):
        return RequiredReadOperation("manage_calendar", {"action": "list_events"}, maximum)
    if re.fullmatch(
        _REQUEST_PREFIX + r"which\s+search\s+(?:backend|provider)\s+am\s+i\s+on"
        r"(?:\s+right\s+now)?[?!.]*",
        normalized_text,
        re.I,
    ):
        return RequiredReadOperation(
            "manage_settings", {"action": "get", "key": "search_provider"}, maximum,
        )
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:what\s+(?:default\s+)?time\s+filter\s+is\s+my\s+"
        r"search\s+set\s+to(?:\s+by\s+default)?|show\s+me\s+the\s+whole\s+"
        r"search\s+(?:settings?\s+)?group)[?!.]*",
        normalized_text,
        re.I,
    ):
        return RequiredReadOperation("manage_settings", {"action": "list"}, maximum)
    if selected_tools_for_request(normalized_text) == frozenset({"ui_control"}):
        # Pure surface navigation must not inherit a prior sealed data read.
        return None
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:give|show)\s+me\s+(?:my\s+)?(?:"
        r"calend(?:ar|er)\s+for\s+(?:this|next)\s+week|upcoming\s+events)"
        r"(?:\s+(?:please|pls|plz))?[?!.]*",
        normalized_text,
        re.I,
    ):
        return RequiredReadOperation("manage_calendar", {"action": "list_events"}, maximum)
    if re.fullmatch(
        _REQUEST_PREFIX + r"wat\s+(?:scheduled\s+)?ta(?:s)?ks\s+"
        r"do\s+i\s+have(?:\s+set\s+up)?(?:\s+rn)?[?!.]*",
        normalized_text,
        re.I,
    ):
        return RequiredReadOperation("manage_tasks", {"action": "list"}, maximum)
    if (
        re.search(r"\b(?:do\s+i\s+have|are\s+there)\b[^?!.]{0,80}\bskills?\b", normalized_text, re.I)
        and re.search(r"\b(?:cover|handle|handling|about|for)\b", normalized_text, re.I)
    ):
        query = re.sub(
            r"^.*?\bskills?\b\s+(?:that\s+)?(?:cover|handle|handling|about|for)\s+",
            "", normalized_text, flags=re.I,
        ).strip(" ?!.")
        return RequiredReadOperation("manage_skills", {"action": "search", "query": query}, maximum)
    inbox_summary = re.fullmatch(
        _REQUEST_PREFIX + r"summari[sz]e\s+(?:my|our|the)\s+"
        r"(?:inbox(?:es|s)?|mailbox(?:es)?)\s+(?:last|latest|newest)\s+"
        r"(?P<count>\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
        r"(?:emails?|messages?)[.!?]*",
        normalized_text,
        re.I,
    )
    if inbox_summary:
        raw_count = inbox_summary["count"].lower()
        count = int(raw_count) if raw_count.isdecimal() else _READ_COUNT_WORDS[raw_count]
        return RequiredReadOperation("list_emails", {}, count)
    if (
        re.fullmatch(
            _REQUEST_PREFIX + r"(?:quick\s+)?br(?:ie|ei)f\s+of\s+(?:my|our|the)\s+"
            r"(?:latest|newest|recent)\s+emails?[?!.]*",
            normalized_text,
            re.I,
        )
        or re.fullmatch(
            _REQUEST_PREFIX + r"find\s+anything\s+urgent\s+that\s+came\s+in\s+recently[?!.]*",
            normalized_text,
            re.I,
        )
    ):
        return RequiredReadOperation("list_emails", {"folder": "INBOX"}, maximum)
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:is\s+there\s+)?anything\s+waiting\s+in\s+"
        r"(?:my|our|the)\s+(?:inbox|mailbox)[?!.]*",
        normalized_text,
        re.I,
    ):
        return RequiredReadOperation("list_emails", {"folder": "INBOX"}, maximum)
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:give\s+me\s+(?:a\s+)?rundown\s+of|summari[sz]e)\s+"
        r"(?:(?:my|our|the)\s+)?(?:latest|newest|recent)\s+emails?"
        r"(?:\s+for\s+each\s+account)?[.!?]*",
        normalized_text,
        re.I,
    ):
        return RequiredReadOperation("list_emails", {}, maximum)
    # Resolve entity ordinals from the intact request.  Presentation parsing
    # deliberately treats phrases such as "first one" as a result limit, but
    # in an explicit open/read follow-up the phrase identifies the entity.
    if operation := _ordinal_visible_collection_read(normalized_text, rows, None):
        return operation
    attachment_read = _explicit_email_attachment_read(normalized_text)
    if attachment_read:
        attachment_uid, attachment_index = attachment_read
        return RequiredReadOperation(
            "download_attachment",
            {"uid": attachment_uid, "index": attachment_index},
            maximum,
        )
    calendar_abbreviation = re.fullmatch(
        _REQUEST_PREFIX + r"(?:list|show|check)\s+(?:me\s+)?(?:my|our|the)\s+"
        r"cal\s+events?(?:\s+(?:please|pls|plz))?[.!?]*",
        text,
        re.I,
    )
    if calendar_abbreviation:
        return RequiredReadOperation("manage_calendar", {"action": "list_events"}, maximum)
    if re.fullmatch(
        _REQUEST_PREFIX + r"what\s+do\s+(?:i|we)\s+have\s+on\s+today[?!.]*",
        normalized_text,
        re.I,
    ):
        return RequiredReadOperation("manage_calendar", {"action": "list_events"}, maximum)
    if (
        re.fullmatch(
            _REQUEST_PREFIX + r"what(?:['’]?s|\s+is)\s+coming\s+up\s+"
            r"(?:this|next)\s+(?:week|month)[?!.]*",
            normalized_text,
            re.I,
        )
        or re.fullmatch(
            _REQUEST_PREFIX + r"what\s+events?\s+do\s+(?:i|we)\s+"
            r"(?:have|got)\s+coming\s+up\s+soon[?!.]*",
            normalized_text,
            re.I,
        )
        or re.fullmatch(
            _REQUEST_PREFIX + r"what(?:['’]?s|\s+is)\s+on\s+(?:my|our)\s+plate\s+"
            r"(?:this|next)\s+(?:week|month)(?:[?!.]\s*anything\s+"
            r"(?:i|we)\s+should\s+know\s+about)?[?!.]*",
            normalized_text,
            re.I,
        )
    ):
        return RequiredReadOperation("manage_calendar", {"action": "list_events"}, maximum)
    contact_resolution = re.fullmatch(
        _REQUEST_PREFIX + r"resolve\s+(?P<query>[^?!.]{2,120}?)\s+in\s+"
        r"(?:my|our|the)\s+(?:contacts?|address\s*book)[.!?]*",
        text,
        re.I,
    )
    if contact_resolution:
        return RequiredReadOperation(
            "manage_contact",
            {"action": "search", "query": contact_resolution["query"].strip()},
            maximum,
        )
    named_contact_lookup = re.fullmatch(
        _REQUEST_PREFIX + r"(?:who\s+is|look\s*up|find|search\s+for)\s+"
        r"(?P<query>[^?!.]{2,120}?)\s+in\s+(?:my|our|the)\s+"
        r"(?:contacts?|address\s*book)(?:\s+again)?[?!.]*",
        normalized_text,
        re.I,
    )
    if named_contact_lookup:
        return RequiredReadOperation(
            "manage_contact",
            {"action": "search", "query": named_contact_lookup["query"].strip()},
            maximum,
        )
    named_skill_section = re.fullmatch(
        _REQUEST_PREFIX + r"(?:show|read|view)\s+(?:me\s+)?(?:the\s+)?"
        r"[^?!.]{2,100}?\s+section\s+(?:of|from|in)\s+(?:the\s+)?"
        r"(?P<name>[A-Za-z0-9][A-Za-z0-9_-]{1,100})\s+skill[.!?]*",
        normalized_text,
        re.I,
    )
    if named_skill_section:
        return RequiredReadOperation(
            "manage_skills",
            {"action": "view", "name": named_skill_section["name"]},
            maximum,
        )
    named_skill_view = re.fullmatch(
        r"(?:" + _REQUEST_PREFIX + r"(?:read|view|open|load)|"
        r"(?:can|could)\s+i\s+(?:see|view|open))\s+"
        r"(?:(?:my|the|a)\s+)?"
        r"(?P<name>[A-Za-z0-9][A-Za-z0-9_-]{1,100})\s+skill"
        r"(?:\s+(?:please|pls|plz))?[.!?]*",
        normalized_text,
        re.I,
    )
    if named_skill_view:
        return RequiredReadOperation(
            "manage_skills",
            {"action": "view", "name": named_skill_view["name"]},
            maximum,
        )
    notes_are_there = re.fullmatch(
        _REQUEST_PREFIX + r"what\s+notes?\s+are\s+there(?:[?!.]\s*"
        r"(?P<count>" + _READ_COUNT + r")\s+titles?\s+max,?\s*"
        r"(?:just\s+)?read(?:ing|[- ]only))?[?!.]*",
        normalized_text,
        re.I,
    )
    if notes_are_there:
        raw_count = notes_are_there["count"]
        count = maximum
        if raw_count:
            parsed_count = int(raw_count) if raw_count.isdecimal() else _READ_COUNT_WORDS[raw_count.lower()]
            count = parsed_count if count is None else min(count, parsed_count)
        return RequiredReadOperation("manage_notes", {"action": "list"}, count)
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:which|what)\s+models?\s+(?:can|could|should)\s+"
        r"(?:i|we)\s+(?:hand|delegate|pass)\s+(?:work|tasks?|jobs?)\s+"
        r"(?:off\s+to|to)[?!.]*",
        normalized_text,
        re.I,
    ):
        return RequiredReadOperation("list_models", max_items=maximum)
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:narrow|filter)\s+(?:it|that|the\s+(?:list|catalog))"
        r"(?:\s+down)?\s+(?:to|for|by)\s+[^?!.]{2,120}[?!.]*",
        normalized_text,
        re.I,
    ):
        prior = _latest_successful_read_operation(rows)
        if prior is not None and canonical_tool(prior.tool) == "list_models":
            # Re-read the live catalog so the model narrows current evidence;
            # do not manufacture a literal ID substring from qualitative
            # terms such as "small fast".
            return RequiredReadOperation("list_models", max_items=maximum)
    if re.search(r"\b(?:saved\s+)?cookbo{1,2}k\s+serve\s+presets?\b", normalized_text, re.I):
        return RequiredReadOperation("list_serve_presets", {}, maximum)
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:list|show)\s+(?:me\s+)?(?:my|our|the|saved)\s+"
        r"serve\s+presets?(?:\s+then)?[.!?]*",
        normalized_text,
        re.I,
    ):
        return RequiredReadOperation("list_serve_presets", {}, maximum)
    skill_subject = re.search(
        r"\bski(?:ll|l)s?\b[^?!.]{0,100}?\b(?:about|covers?|covering|for)\s+"
        r"(?P<query>[^?!.]{2,140})",
        normalized_text,
        re.I,
    )
    if skill_subject and re.search(
        r"\b(?:find|search|look|show|list|anything|something)\b",
        normalized_text,
        re.I,
    ):
        query = re.split(
            r"\s*,\s*(?:however|whatever|regardless)\b",
            skill_subject["query"],
            maxsplit=1,
            flags=re.I,
        )[0].strip(" ,—–-")
        query = re.sub(
            r"^(?:anything|something)\s+(?:about|covering|for)\s+",
            "",
            query,
            flags=re.I,
        ).strip()
        if query:
            return RequiredReadOperation(
                "manage_skills", {"action": "search", "query": query}, maximum,
            )
    if (
        re.search(r"\bwho\s+(?:am\s+i|are\s+we)\s+blocking\s+in\s+(?:email|mail)\b", normalized_text, re.I)
        or (
            re.search(r"\bblocked\s+senders?\b", normalized_text, re.I)
            and re.search(r"\b(?:show|list|who|what|check)\b", normalized_text, re.I)
        )
    ):
        return RequiredReadOperation("manage_email_state", {"action": "list_blocked"}, maximum)
    if (
        re.search(r"\b(?:inbox|mailbox|email)\b", normalized_text, re.I)
        and re.search(r"\b(?:check|scan|look)\b[^.!?]{0,80}\bspam\b", normalized_text, re.I)
    ):
        return RequiredReadOperation("scan_spam", {}, maximum)
    if (
        re.search(r"\bcookbo{1,2}k\s+model\s+servers?\b", normalized_text, re.I)
        and re.search(r"\b(?:state|status|served|running|crashed|stuck|error(?:ing|ed)?|dead)\b", normalized_text, re.I)
    ):
        return RequiredReadOperation("list_served_models", {}, maximum)
    titled_editor_document = re.fullmatch(
        _REQUEST_PREFIX
        + r"(?:i\s+had\s+)?(?:a\s+)?doc(?:ument)?\s+[^.!?\n]{0,100}?"
          r"(?:called|named|titled)\s+['\"](?P<title>[^'\"\n]{2,160})['\"]"
          r"[^.!?\n]{0,120}\b(?:pull|open|bring|show)\b[^.!?\n]{0,80}"
          r"\b(?:editor|documents?\s+(?:panel|view))(?:\s+for\s+me)?[.!?]*",
        normalized_text,
        re.I,
    )
    if titled_editor_document:
        return RequiredReadOperation(
            "manage_documents",
            {"action": "list", "search": titled_editor_document["title"].strip()},
            maximum,
        )
    bounded_calendar_inventory = re.fullmatch(
        _REQUEST_PREFIX
        + r"(?:list|show|check)\s+(?:me\s+)?(?:my|our|the)?\s*calendar\s+events?\s+"
          r"from\s+(?P<start>\d{4}-\d{2}-\d{2})\s+"
          r"(?:through|to|until|-|–|—)\s+(?P<end>\d{4}-\d{2}-\d{2})"
          r"(?:\s+(?:containing|matching|about|named)\s+(?P<query>[^.!?\n]{1,160}))?"
          r"[.!?]*",
        normalized_text,
        re.I,
    )
    if bounded_calendar_inventory:
        args = {
            "action": "list_events",
            "start": bounded_calendar_inventory["start"],
            "end": bounded_calendar_inventory["end"],
        }
        if bounded_calendar_inventory["query"]:
            args["query"] = bounded_calendar_inventory["query"].strip()
        return RequiredReadOperation("manage_calendar", args, maximum)
    if operation := _natural_safe_inventory_operation(message, maximum):
        return operation
    document_prefix_search = re.fullmatch(
        _REQUEST_PREFIX
        + r"(?:i(?:['’]?m|\s+am)\s+trying\s+to\s+)?find\s+(?:a\s+)?doc(?:ument)?\s+"
          r"(?:i\s+(?:made|wrote|created)\s+(?:earlier|before),?\s*)?"
          r"(?:whose\s+)?title\s+(?:starts?\s+with|begins?\s+with)\s+"
          r"(?P<query>['\"]?[^'\"\n]{2,160}['\"]?)[.!?]*",
        normalized_text,
        re.I,
    )
    if document_prefix_search:
        query = document_prefix_search["query"].strip().strip("'\"")
        return RequiredReadOperation(
            "manage_documents", {"action": "list", "search": query}, maximum,
        )
    unsubscribe_scan = re.fullmatch(
        _REQUEST_PREFIX
        + r"(?:go\s+through|scan|check)\s+(?:my|our|the)\s+"
          r"(?:(?P<account>primary|secondary|work|personal)\s+)?(?:inbox|mailbox)\s+"
          r"(?:and\s+)?(?:(?:flag|find|show|list)\s+|for\s+)"
          r"(?:newsletters?|mailing\s+lists?|messages?|emails?)[^.!?]{0,180}"
          r"\bunsubscribe\b[^.!?]*[.!?]*"
          r"(?:\s*(?:do\s+not|don['’]?t|dont)\s+change\s+anything\s+yet[.!?]*)?",
        normalized_text,
        re.I,
    )
    if unsubscribe_scan:
        args = {"folder": "INBOX"}
        if unsubscribe_scan["account"]:
            args["account"] = unsubscribe_scan["account"].title() + " Inbox"
        return RequiredReadOperation("scan_email_unsubscribes", args, maximum)
    contextual_repeat = re.fullmatch(
        _REQUEST_PREFIX
        + r"(?:"
          r"(?:list|show)(?:\s+me)?\s+(?:those|them|the\s+list)(?:\s+again)?(?:\s+then)?"
          r"|(?:those|them)\s+again"
          r")"
          r"(?:\s*(?:but|and|[—–-])\s*"
          r"(?:tell\s+me\s+(?:if|whether)|is|are|do|does|which|what)\b[^.;\n]{0,160})?"
          r"[.!?]*",
        text,
        re.I,
    )
    if contextual_repeat:
        # Bind an explicit re-list plus a harmless question to the immediately
        # preceding read operation. This keeps "the list" as account names,
        # for example, instead of letting the model switch to inbox messages.
        for index in range(len(rows) - 1, -1, -1):
            row = rows[index]
            role = row.get("role") if isinstance(row, dict) else getattr(row, "role", "")
            if role != "user":
                continue
            content = row.get("content", "") if isinstance(row, dict) else getattr(row, "content", "")
            prior = required_read_operation_for_request(content, rows[:index])
            if prior is not None:
                combined_maximum = prior.max_items
                if maximum is not None:
                    combined_maximum = (
                        maximum if combined_maximum is None
                        else min(maximum, combined_maximum)
                    )
                return replace(prior, max_items=combined_maximum)
            break
        if prior := _latest_successful_read_operation(rows):
            return replace(
                prior,
                max_items=maximum if maximum is not None else prior.max_items,
            )
    if (
        recently_executed_families(rows, maximum=1) == ("calendar",)
        and re.fullmatch(
            _REQUEST_PREFIX + r"(?:now\s+)?(?:do\s+)?(?:that|it|those|them|the\s+same)\s+"
            r"again\s+but\s+(?:from|for)\s+(?:my\s+)?(?:next|upcoming)\s+events[.!?]*",
            text,
            re.I,
        )
    ):
        inherited_maximum = maximum
        if inherited_maximum is None:
            for index in range(len(rows) - 1, -1, -1):
                row = rows[index]
                role = row.get("role") if isinstance(row, dict) else getattr(row, "role", "")
                if role != "user":
                    continue
                content = row.get("content", "") if isinstance(row, dict) else getattr(row, "content", "")
                prior = required_read_operation_for_request(content, rows[:index])
                if prior is not None and canonical_tool(prior.tool) == "manage_calendar":
                    inherited_maximum = prior.max_items
                    break
        return RequiredReadOperation(
            "manage_calendar", {"action": "list_events"}, inherited_maximum,
        )
    if (
        recently_executed_families(rows, maximum=1) == ("calendar",)
        and re.search(r"\b(?:same|again|those|them)\b", text, re.I)
        and re.search(r"\btomorrow(?:['’]?s)?\b", text, re.I)
    ):
        return RequiredReadOperation("manage_calendar", {"action": "list_events"}, maximum)
    quick_calendar_peek = re.fullmatch(
        _REQUEST_PREFIX + r"(?:can\s+i\s+)?(?:get|give\s+me|show\s+me)?\s*"
        r"(?:a\s+)?(?:quick\s+)?(?:peek|look|rundown|overview)\s+"
        r"(?:at|of)\s+(?:my|our|the)\s+(?:cal|calend(?:ar|er))[.!?]*",
        text,
        re.I,
    )
    if quick_calendar_peek:
        return RequiredReadOperation("manage_calendar", {"action": "list_events"}, maximum)
    skill_library_search = re.fullmatch(
        _REQUEST_PREFIX + r"(?:look\s+thr(?:u|ough)|search|find\s+in)\s+"
        r"(?:my|our|the)\s+(?:skills?|skill\s+library)\s+"
        r"(?:for(?:\s+anything)?\s+about|for|about)\s+(?P<query>[^?!.]{2,160})[.!?]*",
        text,
        re.I,
    )
    if skill_library_search:
        return RequiredReadOperation(
            "manage_skills",
            {"action": "search", "query": skill_library_search["query"].strip()},
            maximum,
        )
    natural_skill_library_search = re.fullmatch(
        _REQUEST_PREFIX + r"(?:is\s+there\s+|do\s+i\s+have\s+|have\s+i\s+got\s+)?"
        r"any\s+skills?\s+in\s+(?:my|the)\s+(?:skills?\s+)?library\s+"
        r"(?:about|for|that\s+(?:handles?|covers?))\s+(?P<query>[^?!.]{2,160})[?!.]*",
        text,
        re.I,
    )
    if natural_skill_library_search:
        return RequiredReadOperation(
            "manage_skills",
            {"action": "search", "query": natural_skill_library_search["query"].strip()},
            maximum,
        )
    if (
        re.fullmatch(
            _REQUEST_PREFIX + r"(?:i\s+(?:need|want)\s+)?(?:a\s+)?(?:quick\s+)?"
            r"(?:rundown|overview|look)\s+of\s+(?:my|our|the)\s+calend(?:ar|er)[.!?]*",
            text,
            re.I,
        )
        or re.fullmatch(
            _REQUEST_PREFIX + r"what\s+does\s+(?:my|our)\s+week\s+look\s+like[.!?]*",
            text,
            re.I,
        )
    ):
        return RequiredReadOperation("manage_calendar", {"action": "list_events"}, maximum)
    personal_store_contents = re.fullmatch(
        _REQUEST_PREFIX
        + r"(?:what|wat|wht|wuts)(?:['’]?s|\s+(?:is|are))?\s+"
          r"(?:in|inside)\s+(?:my|our|the)\s+"
          r"(?P<target>notes|skills|tasks|documents|docs|memory|memories)"
          r"(?:\s+(?:library|list))?[.!?]*",
        text,
        re.I,
    )
    if personal_store_contents:
        tool, action = _READ_LIST_TARGETS[personal_store_contents["target"].lower()]
        return RequiredReadOperation(tool, {"action": action} if action else {}, maximum)
    possessive_store_titles = re.fullmatch(
        _REQUEST_PREFIX
        + r"(?:give|show)\s+(?:me\s+)?(?:my|our)\s+"
          r"(?P<target>notes?|skills?|tasks?|documents?|docs?|memories|memory)\s+"
          r"(?:titles?|names?|entries?|items?)[.!?]*",
        text,
        re.I,
    )
    if possessive_store_titles:
        target = possessive_store_titles["target"].lower()
        target = {
            "note": "notes", "skill": "skills", "task": "tasks",
            "document": "documents", "doc": "docs", "memories": "memory",
        }.get(target, target)
        tool, action = _READ_LIST_TARGETS[target]
        return RequiredReadOperation(tool, {"action": action} if action else {}, maximum)
    noun_first_inventory = re.fullmatch(
        _REQUEST_PREFIX
        + r"(?:my\s+|our\s+)?(?P<target>notes|skills|tasks|documents|docs|memory|memories)"
          r"(?:\s+list)?"
          r"(?:\s+(?:please|pls|plz))?"
          r"(?:\s*[-—,:]\s*(?:names?|titles?|entries?|items?)\s+only)?[.!?]*",
        text,
        re.I,
    )
    if noun_first_inventory:
        tool, action = _READ_LIST_TARGETS[noun_first_inventory["target"].lower()]
        return RequiredReadOperation(tool, {"action": action} if action else {}, maximum)
    note_label_lookup = re.fullmatch(
        _REQUEST_PREFIX
        + r"(?:i\s+need\s+[^,.;!?]{1,100},?\s+)?(?:show|list|find)\s+"
          r"(?:my\s+)?notes?\s+(?:tagged|labelled|labeled)\s+(?P<label>[^,.;!?]{1,80})[.!?]*",
        text,
        re.I,
    )
    if note_label_lookup:
        return RequiredReadOperation(
            "manage_notes",
            {"action": "list", "label": note_label_lookup["label"].strip()},
            maximum,
        )
    if re.fullmatch(
        _REQUEST_PREFIX
        + r"(?:while\s+(?:that|it)(?:['’]?s|\s+is)\s+open,?\s+)?"
          r"(?:bring\s+up|show|list)\s+(?:my|our|the)\s+calend(?:ar|er)"
          r"(?:\s+for\s+(?:this|next)\s+(?:week|month))?[.!?]*",
        text,
        re.I,
    ):
        return RequiredReadOperation("manage_calendar", {"action": "list_events"}, maximum)
    if (
        recently_executed_families(rows, maximum=1) == ("email",)
        and re.fullmatch(
            _REQUEST_PREFIX
            + r"which\s+(?:one|account|inbox|mailbox)\s+should\s+i\s+check\s+first\s+"
              r"for\s+unread(?:\s+(?:mail|emails?|messages?))?[?!.]*",
            text,
            re.I,
        )
    ):
        return RequiredReadOperation(
            "list_emails", {"folder": "INBOX", "unread_only": True}, maximum,
        )
    if selected_tools_for_request(text) == {"list_cached_models"}:
        return RequiredReadOperation("list_cached_models", max_items=maximum)
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:my|our)\s+calend(?:ar|er)\s+events?"
        r"(?:\s+(?:please|pls|plz))?[.!?]*",
        text, re.I,
    ):
        return RequiredReadOperation("manage_calendar", {"action": "list_events"}, maximum)
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:list|show)\s+(?:my|our)\s+calendars?"
        r"(?:\s+(?:please|pls|plz))?[.!?]*",
        text, re.I,
    ):
        # A calendar is a container; calendar events are its contents.  Keep
        # the explicit plural-container request ahead of fuzzy inventory
        # routing, which otherwise collapses both concepts to list_events.
        return RequiredReadOperation("manage_calendar", {"action": "list_calendars"}, maximum)
    if (
        re.search(r"\b(?:documents?|docs?)\b", text, re.I)
        and re.search(r"\b(?:list|show)\s+(?:them|em)\b", text, re.I)
        and not re.search(r"\b(?:edit|change|delete|remove|write|create)\b", text, re.I)
    ):
        return RequiredReadOperation("manage_documents", {"action": "list"}, maximum)
    if (
        re.search(r"\bmcp\b", text, re.I)
        and re.search(r"\btools?\b", text, re.I)
        and re.search(r"\b(?:what|which|wich|show|list|check|available|expose[ds]?)\b", text, re.I)
        and not re.search(r"\b(?:add|delete|remove|enable|disable|reconnect|change)\b", text, re.I)
    ):
        return RequiredReadOperation("manage_mcp", {"action": "list_tools"}, maximum)
    tool_inventory_clauses = re.split(r"[\n.!?;]+", text)
    if any(
        re.search(r"\b(?:agent\s+)?tools?\b", clause, re.I)
        and re.search(
            r"\b(?:disabled|enabled|available|unavailable|toggles?|"
            r"switched\s+(?:off|on)|turned\s+(?:off|on))\b",
            clause,
            re.I,
        )
        and re.search(r"\b(?:what|which|wich|show|list|check)\b", clause, re.I)
        for clause in tool_inventory_clauses
    ):
        return RequiredReadOperation("manage_settings", {"action": "list_tools"}, maximum)
    if re.match(
        r"^\s*" + _REQUEST_PREFIX
        + r"what(?:['’]?s|\s+is)\s+on\s+(?:my|our|the)\s+agenda\s+today\b",
        normalized_text,
        re.I,
    ):
        return RequiredReadOperation("manage_calendar", {"action": "list_events"}, maximum)
    if (
        re.search(
            r"\b(?:blocked\s+senders?|senders?\s+(?:i(?:['’]?ve|\s+have)\s+)?blocked)\b",
            text,
            re.I,
        )
        and re.search(r"\b(?:show|list|who|which|what|check)\b", text, re.I)
    ):
        return RequiredReadOperation("manage_email_state", {"action": "list_blocked"}, maximum)
    if (
        re.search(r"\b(?:internal\s+)?app\s+api\b", text, re.I)
        and re.search(r"\bgallery\b", text, re.I)
        and re.search(r"\b(?:list|show|view|look|browse|images?|library)\b", text, re.I)
    ):
        return RequiredReadOperation("app_api", {
            "action": "call", "method": "GET", "path": "/api/gallery/library",
        }, maximum)
    if (
        re.search(r"\b(?:my|our|the)\s+gallery\b", text, re.I)
        and re.search(r"\b(?:list|show|browse|look\s+thr(?:u|ough)|what(?:['’]?s|\s+is)\s+in)\b", text, re.I)
        and not re.search(r"\b(?:upscale|remove\s+(?:the\s+)?background|delete|generate)\b", text, re.I)
    ):
        # Gallery inventory is a declared owner-scoped GET. Seal the exact
        # endpoint so natural wording cannot drift into a fabricated prose
        # list or an unrelated Cookbook operation.
        return RequiredReadOperation("app_api", {
            "action": "call", "method": "GET", "path": "/api/gallery/library",
        }, maximum)
    if (
        re.search(r"\b(?:my|the)\s+gallery\b", text, re.I)
        and re.search(r"\b(?:list|show|view|look\s+thr(?:u|ough)|re-?check)\b", text, re.I)
        and not re.search(r"\b(?:delete|remove|upscale|edit|change)\b", text, re.I)
    ):
        return RequiredReadOperation("app_api", {
            "action": "call", "method": "GET", "path": "/api/gallery/library",
        }, maximum)
    bare_personal_inventory = re.fullmatch(
        _REQUEST_PREFIX + r"(?:my|our)\s+"
        r"(?P<target>notes|skills|tasks|documents|docs|memory|memories)"
        r"(?:\s+(?:please|pls|plz))?[.!?]*",
        text, re.I,
    )
    if bare_personal_inventory:
        target = bare_personal_inventory["target"].lower()
        tool, action = _READ_LIST_TARGETS[target]
        return RequiredReadOperation(tool, {"action": action} if action else {}, maximum)
    top_document_titles = re.fullmatch(
        _REQUEST_PREFIX + r"(?:give|show)\s+me\s+(?:the\s+)?(?:top|first)\s+"
        r"(?P<count>" + _READ_COUNT + r")\s+titles?\s+in\s+(?:my|our|the)\s+"
        r"(?:documents?|docs?)[.!?]*",
        text, re.I,
    )
    if top_document_titles:
        raw = top_document_titles["count"].lower()
        count = int(raw) if raw.isdecimal() else _READ_COUNT_WORDS[raw]
        return RequiredReadOperation("manage_documents", {"action": "list"}, count)
    if re.fullmatch(
        _REQUEST_PREFIX + r"what(?:['’]?s|\s+is)\s+coming\s+up\s+on\s+"
        r"(?:my|our|the)\s+(?:calendar|calender)[?!.]*",
        text, re.I,
    ):
        return RequiredReadOperation("manage_calendar", {"action": "list_events"}, maximum)
    fuzzy_saved_inventory = re.fullmatch(
        _REQUEST_PREFIX + r"(?:i\s+need\s+(?:a\s+)?(?:quick\s+)?read[- ]only\s+peek\s+at|"
        r"(?:pull\s+up|show|list)\s+(?:my|our))\s+"
        r"(?:(?:my|our)\s+)?(?:saved\s+)?(?P<target>[A-Za-z]+)"
        r"(?:\s+(?:please|pls|plz))?[.!?]*",
        text, re.I,
    )
    if fuzzy_saved_inventory:
        family = _fuzzy_family(fuzzy_saved_inventory["target"])
        if family in _FUZZY_SAFE_READS:
            tool, action = _FUZZY_SAFE_READS[family]
            return RequiredReadOperation(tool, {"action": action} if action else {}, maximum)
    if re.fullmatch(
        _REQUEST_PREFIX + r"what\s+(?:mail|email)\s+accounts?\s+(?:are|r)\s+"
        r"(?:hooked\s+up|connected|configured)(?:\s+to\s+odysseus)?[?!.]*",
        text,
        re.I,
    ):
        return RequiredReadOperation("list_email_accounts", max_items=maximum)
    document_titles = re.fullmatch(
        _REQUEST_PREFIX + r"(?:give|show)\s+me\s+(?:up\s+to\s+)?"
        r"(?P<count>" + _READ_COUNT + r")\s+(?:document|doc)\s+titles?\s+"
        r"from\s+(?:my|the)\s+library[?!.]*",
        text,
        re.I,
    )
    if document_titles:
        raw_count = document_titles["count"].casefold()
        count = int(raw_count) if raw_count.isdecimal() else _READ_COUNT_WORDS[raw_count]
        if maximum is not None:
            count = min(count, maximum)
        return RequiredReadOperation("manage_documents", {"action": "list"}, count)
    if re.fullmatch(
        r"(?:quick\s+)?memory\s+dump\s*[-—–:]\s*"
        r"what(?:['’]?s|\s+is)\s+saved[?!.]*",
        text,
        re.I,
    ):
        return RequiredReadOperation("manage_memory", {"action": "list"}, maximum)
    if (
        re.search(r"\bskills?\s+check\b", text, re.I)
        and re.search(r"\b(?:names?|list|show)\b", text, re.I)
    ):
        return RequiredReadOperation("manage_skills", {"action": "list"}, maximum)
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:what(?:['’]?s|\s+is)|wats)\s+in\s+"
        r"(?:my|the)\s+skills?\s+library\b[^\n]*",
        text,
        re.I,
    ):
        return RequiredReadOperation("manage_skills", {"action": "list"}, maximum)
    research_history_lookup = re.fullmatch(
        _REQUEST_PREFIX
        + r"(?:when\s+it(?:['’]?s|\s+is)\s+done,?\s*)?how\s+do\s+i\s+"
          r"(?:find|open|read|see|get\s+to)\s+(?:it|that|the\s+report)\s+again[?!.]*",
        text,
        re.I,
    )
    if research_history_lookup and recently_executed_families(rows, maximum=1) == ("research",):
        return RequiredReadOperation("manage_research", {"action": "list"}, maximum)
    research_filter = re.fullmatch(
        _REQUEST_PREFIX
        + r"(?:are\s+)?(?:any|which)\s+of\s+(?:them|those)\s+"
          r"(?:about|on|cover(?:ing)?|mention(?:ing)?)\s+(?P<query>[^?!.]{2,120})[?!.]*",
        text,
        re.I,
    )
    if research_filter and recently_executed_families(rows, maximum=1) == ("research",):
        return RequiredReadOperation(
            "manage_research",
            {"action": "list", "search": research_filter["query"].strip()},
            maximum,
        )
    inbox_named_read = re.fullmatch(
        _REQUEST_PREFIX
        + r"read\s+(?:that|the)\s+(?P<query>[^?!.]{2,120}?)\s+in\s+"
          r"(?:the\s+)?(?:(?P<account>primary|secondary|work|personal)\s+)?(?:inbox|mailbox)"
          r"[^?!.]*[?!.]*",
        text,
        re.I,
    )
    if inbox_named_read:
        query = re.sub(
            r"\s+(?:note|email|message)\s*$", "", inbox_named_read["query"].strip(), flags=re.I,
        ).strip()
        args = {"query": query, "folder": "INBOX"}
        if inbox_named_read["account"]:
            args["account"] = inbox_named_read["account"].title()
        return RequiredReadOperation("search_emails", args, maximum)
    if _has_cookbook_server_reference(text) and re.search(
        r"\b(?:show|list|configured|available|current|right\s+now)\b", text, re.I
    ):
        return RequiredReadOperation("list_cookbook_servers", max_items=maximum)
    if re.search(r"\btool\s+toggles?\b", text, re.I) and re.search(
        r"\b(?:show|list|check|eyeball|inspect|view|what)\b", text, re.I
    ):
        return RequiredReadOperation("manage_settings", {"action": "list_tools"}, maximum)
    if re.search(r"\b(?:pull\s+up|show|list)\b", text, re.I) and re.search(
        r"\bdocumets?\b", text, re.I
    ):
        return RequiredReadOperation("manage_documents", {"action": "list"}, maximum)
    if (
        re.search(r"\b(?:best|recommend(?:ed)?|suitable|compatible|fit)\b", text, re.I)
        and re.search(r"\bmodels?\b", text, re.I)
        and re.search(
            r"\b(?:my|this|the|current)\s+(?:hardware|machine|computer|pc|server|system)\b"
            r"|\b(?:gpu|vram|ram)\b",
            text,
            re.I,
        )
        and not re.search(r"[;\n]|\b(?:and\s+then|then\s+also)\b", text, re.I)
    ):
        return RequiredReadOperation("app_api", {
            "action": "call",
            "method": "GET",
            "path": "/api/hwfit/models?fit_only=true&limit=10&sort=fit",
        })
    bare_personal_inventory = re.fullmatch(
        _REQUEST_PREFIX
        + r"(?:my|our)\s+(?P<target>notes|documents|docs|memories|memory|tasks|skills)"
          r"(?:\s+(?:pls|please))?[.!?]*",
        text,
        re.I,
    )
    if bare_personal_inventory:
        tool, action = _READ_LIST_TARGETS[bare_personal_inventory["target"].lower()]
        return RequiredReadOperation(tool, {"action": action} if action else {}, maximum)
    top_document_titles = re.fullmatch(
        _REQUEST_PREFIX
        + r"(?:give|show)\s+me\s+(?:the\s+)?(?:top|first)\s+(?P<count>\d+)\s+titles?\s+"
          r"(?:in|from)\s+(?:my|our)\s+(?:documents|docs|library)[.!?]*",
        text,
        re.I,
    )
    if top_document_titles:
        inline_maximum = int(top_document_titles["count"])
        if maximum is not None:
            inline_maximum = min(inline_maximum, maximum)
        return RequiredReadOperation(
            "manage_documents", {"action": "list"}, inline_maximum,
        )
    if (
        re.search(r"\b(?:saved\s+)?memor(?:y|ies|es)\b", text, re.I)
        and re.search(r"\b(?:pull\s+up|peek|list|show|saved)\b", text, re.I)
        and not re.search(r"\b(?:add|edit|change|delete|forget)\b", text, re.I)
        and not re.search(
            r"\b(?:never|without)\s+(?:(?:using|relying\s+on)\s+)?(?:my\s+)?memory\b|"
            r"\bdo\s+not\s+(?:use|rely\s+on)\s+(?:my\s+)?memory\b",
            text,
            re.I,
        )
    ):
        return RequiredReadOperation("manage_memory", {"action": "list"}, maximum)
    if (
        re.search(r"\bcalend(?:ar|er)\b", text, re.I)
        and re.search(r"\b(?:coming\s+up|upcoming|what(?:['’]?s|\s+is)\s+on)\b", text, re.I)
        and not re.search(r"\b(?:add|create|move|edit|delete|cancel)\b", text, re.I)
    ):
        return RequiredReadOperation("manage_calendar", {"action": "list_events"}, maximum)
    readonly_inventory = re.fullmatch(
        _REQUEST_PREFIX
        + r"i\s+(?:want|need)\s+(?:a\s+)?(?:quick\s+)?read[- ]only\s+"
          r"(?:list|peek)\s+(?:at|of)?\s*(?:my|our|the)?\s*"
          r"(?P<target>notes|documents|docs|memories|memory|tasks|automations|skills)"
          r"[.!?]*",
        text,
        re.I,
    )
    if readonly_inventory:
        tool, action = _READ_LIST_TARGETS[readonly_inventory["target"].lower()]
        return RequiredReadOperation(
            tool, {"action": action} if action else {}, maximum,
        )
    natural_inventory = re.fullmatch(
        _REQUEST_PREFIX
        + r"(?:"
          r"(?:gimme|give\s+me|show\s+me)\s+(?:a\s+)?(?:quick\s+)?(?:list|peek)\s+(?:at|of)?\s*"
          r"(?:(?:my|our)\s+)?(?:(?:stored|saved)\s+)?(?P<target1>notes|documents|docs|memories|memory|tasks|automations|skills)"
          r"|what\s+(?P<target2>notes|documents|docs|memories|memory|tasks|automations|skills)\s+"
          r"(?:do\s+(?:i|we)\s+(?:have|got)|have\s+(?:i|we)\s+got)(?:\s+in\s+(?:here|there))?"
          r"|(?P<target3>memories|memory)\s+please"
          r"|what\s+(?:have\s+(?:you|u)\s+got\s+saved|do\s+(?:you|u)\s+remember)\s+about\s+me"
          r"(?:\s+in\s+(?:my\s+)?(?P<target4>memory|memories))?"
          r"|(?:just\s+)?tell\s+me\s+how\s+many\s+(?P<target5>notes|documents|docs|memories|tasks|skills)\s+"
          r"(?:i|we)\s+have(?:\s+in\s+total)?"
          r"|what(?:['’]?s|\s+is)\s+(?:on|in)\s+(?:my|our)\s+"
          r"(?P<target6>notes|documents|docs|memories|memory|tasks|automations|skills)"
          r"(?:\s+list)?(?:\s+(?:right|rite)\s+now)?"
          r"|got\s+any\s+(?P<target7>cookbo{1,2}k\s+servers|notes|documents|docs|memories|tasks|skills)"
          r"(?:\s+(?:configured|saved|set\s+up))?(?:\s+at\s+all)?"
          r"|(?:gimme|give\s+me)\s+(?:my|our)\s+"
          r"(?P<target8>notes|calendar\s+events|events|documents|docs?|memories|tasks|skills)"
          r"(?:\s+list)?"
          r"|i\s+need\s+(?:an?\s+)?read[- ]only\s+peek\s+at\s+"
          r"(?P<target9>cookbo{1,2}k\s+servers|notes|documents|docs|memories|tasks|skills)"
          r"|(?:my|our)\s+(?P<target10>notes|documents|docs|memories|tasks|skills)\s*,\s*"
          r"list\s+them(?:\s+for\s+me)?"
          r"|(?:quick\s+)?(?P<target11>documents?|docs?)\s+list(?:\s+pl[sz])?"
          r"|i\s+want\s+(?:a\s+)?(?:quick\s+)?read[- ]only\s+list\s+of\s+"
          r"(?:my|our|the)\s+(?P<target12>notes|documents|docs|memories|memory|tasks|skills)"
          r")\s*(?:,\s*(?:short|brief))?[.!?]*",
        text,
        re.I,
    )
    if natural_inventory:
        target = next(
            (value for value in natural_inventory.groupdict().values() if value),
            "memory",
        ).lower()
        target = re.sub(r"^cookbo{1,2}k\b", "cookbook", target)
        if target in {"memory", "memories"}:
            target = "memory"
        tool, action = _READ_LIST_TARGETS[target]
        return RequiredReadOperation(tool, {"action": action} if action else {}, maximum)
    inventory_question = re.fullmatch(
        _REQUEST_PREFIX + r"what\s+(?P<target>notes|(?:automated\s+|scheduled\s+)?tasks|automations|documents|docs|memories|memory|skills)\s+"
        r"do\s+(?:i|we)\s+have(?:\s+set\s+up)?(?:\s+in\s+(?:here|there))?[.!?]*", text, re.I,
    )
    if inventory_question:
        target = re.sub(r"^(?:automated|scheduled)\s+", "", inventory_question["target"].lower())
        tool, action = _READ_LIST_TARGETS[target]
        return RequiredReadOperation(tool, {"action": action} if action else {}, maximum)
    scheduled_jobs = re.fullmatch(
        _REQUEST_PREFIX
        + r"(?:give\s+me\s+)?(?:a\s+)?(?:quick\s+)?look\s+at\s+"
          r"(?:(?:my|the)\s+)?schedul(?:ed|d)\s+jobs?[.!?]*",
        text, re.I,
    )
    if scheduled_jobs:
        return RequiredReadOperation("manage_tasks", {"action": "list"}, maximum)
    if (
        re.fullmatch(_REQUEST_PREFIX + r"show\s+(?:it|that)[.!?]*", text, re.I)
        and recently_executed_families(rows, maximum=1)
    ):
        # The family contract can safely offer the most-recent successful
        # manager, but a bare pronoun does not identify a sealed read action.
        # Do not scan an intervening prose turn for a different product noun
        # and manufacture a conflicting required operation.
        return None
    while _EXACT_READ_REPEAT.fullmatch(text):
        recent = recently_executed_families(rows, maximum=1)
        if len(recent) == 1 and recent[0] in _FUZZY_SAFE_READS:
            inherited_maximum = maximum
            if inherited_maximum is None:
                for index in range(len(rows) - 1, -1, -1):
                    row = rows[index]
                    role = row.get("role") if isinstance(row, dict) else getattr(row, "role", "")
                    content = row.get("content", "") if isinstance(row, dict) else getattr(row, "content", "")
                    if role != "user":
                        continue
                    prior = required_read_operation_for_request(content, rows[:index])
                    if prior is not None and canonical_tool(prior.tool) in {
                        canonical_tool(name) for name in FAMILY_TOOLS[recent[0]]
                    }:
                        inherited_maximum = prior.max_items
                        break
            tool, action = _FUZZY_SAFE_READS[recent[0]]
            executed = _latest_successful_read_operation(rows)
            if executed is not None and canonical_tool(executed.tool) == canonical_tool(tool):
                return replace(executed, max_items=inherited_maximum)
            return RequiredReadOperation(
                tool, {"action": action} if action else {}, inherited_maximum,
            )
        for index in range(len(rows) - 1, -1, -1):
            row = rows[index]
            role = row.get("role") if isinstance(row, dict) else getattr(row, "role", "")
            content = row.get("content", "") if isinstance(row, dict) else getattr(row, "content", "")
            if role == "user" and content != text:
                text, inherited_maximum = _read_request_and_limit(content)
                if maximum is None:
                    maximum = inherited_maximum
                prior_operation = required_read_operation_for_request(text, rows[:index])
                if prior_operation is not None:
                    prior_maximum = prior_operation.max_items
                    combined_maximum = maximum if prior_maximum is None else (
                        prior_maximum if maximum is None else min(prior_maximum, maximum)
                    )
                    return replace(prior_operation, max_items=combined_maximum)
                all_exact = {
                    family for family, pattern in _FAMILY_WORDS.items()
                    if re.search(pattern, text, re.I)
                }
                exact = all_exact & _FUZZY_SAFE_READS.keys()
                fuzzy = _fuzzy_family(text)
                hinted = exact | ({fuzzy} if fuzzy in _FUZZY_SAFE_READS else set())
                if len(all_exact) <= 1 and len(hinted) == 1 and re.match(
                    r"^\s*(?:what|which|where|any|do\s+i\s+have|have\s+i\s+got|list|show|read)\b",
                    text, re.I,
                ):
                    family = next(iter(hinted))
                    tool, action = _FUZZY_SAFE_READS[family]
                    return RequiredReadOperation(tool, {"action": action} if action else {}, maximum)
                if (fuzzy == "email" and re.search(r"\baccounts?|addresses?\b", text, re.I)):
                    return RequiredReadOperation("list_email_accounts", max_items=maximum)
                rows = rows[:index]
                break
        else:
            return None
    if selected_tools_for_request(text) == {"list_email_accounts"}:
        return RequiredReadOperation("list_email_accounts", max_items=maximum)
    if fuzzy_lookup_family := _fuzzy_possessive_lookup_family(text):
        tool, action = _FUZZY_SAFE_READS[fuzzy_lookup_family]
        return RequiredReadOperation(
            tool, {"action": action} if action else {}, maximum
        )
    if operation := _ordinal_email_read(text, rows, maximum):
        return operation
    if operation := _ordinal_skill_view(text, rows, maximum):
        return operation
    if re.fullmatch(
        _REQUEST_PREFIX
        + r"(?:now\s+)?(?:read|show|give\s+me|walk\s+me\s+through)?\s*"
          r"(?:its|that\s+skill(?:['’]s)?)\s+"
          r"(?:full\s+)?(?:procedure|steps?|instructions?|verification(?:\s+steps?)?)"
          r"(?:\s+and\s+(?:its\s+)?(?:procedure|steps?|instructions?|verification(?:\s+steps?)?))*"
          r"(?:[.!?]\s*(?:do\s+not|don['’]?t|dont)\s+execute(?:\s+it|\s+the\s+procedure)?)?"
          r"[.!?]*",
        text,
        re.I,
    ):
        prior = _latest_successful_read_operation(rows)
        if (
            prior is not None
            and canonical_tool(prior.tool) == "manage_skills"
            and prior.args.get("action") == "view"
            and prior.args.get("name")
        ):
            # Pronouns refer to the exact skill the server successfully read,
            # not merely the latest list item or a model-invented name.
            return replace(prior, max_items=maximum)
    contextual_email = re.fullmatch(
        _REQUEST_PREFIX
        + r"what(?:['’]?s|\s+is|\s+are)\s+(?:my\s+)?"
          r"(?:(?P<count>" + _READ_COUNT + r")\s+)?"
          r"(?:latest|newest|recent)(?:\s+emails?)?[.!?]*",
        text,
        re.I,
    )
    if contextual_email and "email" in recently_executed_families(rows):
        raw_count = contextual_email["count"]
        count = None if not raw_count else (
            int(raw_count) if raw_count.isdecimal() else _READ_COUNT_WORDS[raw_count.lower()]
        )
        if maximum is not None:
            count = maximum if count is None else min(count, maximum)
        return RequiredReadOperation(
            "list_emails", {"max_results": count} if count is not None else {}, count
        )
    memory_filter = re.fullmatch(
        _REQUEST_PREFIX
        + r"(?:(?:is\s+there\s+(?:one|any)|are\s+there\s+any|any\s+of\s+them)\s+"
          r"(?:about|mention(?:ing)?|for)|anything\s+in\s+(?:there|it)\s+"
          r"(?:about|mention(?:ing)?|for))\s+(?P<query>[^?!.]{2,120})[?!.]*",
        text,
        re.I,
    )
    if memory_filter and "memory" in recently_executed_families(rows):
        query = re.sub(r"\s+", " ", memory_filter["query"]).strip()
        return RequiredReadOperation("manage_memory", {"action": "search", "text": query}, maximum)
    skill_filter = re.fullmatch(
        _REQUEST_PREFIX
        + r"(?:(?:is|are)\s+there\s+(?:one|any)\s+|(?:is|are)\s+(?:one|any)\s+of\s+"
          r"(?:em|them|those|these)\s+|any\s+of\s+(?:em|them|those|these)\s+)"
          r"(?:about|on|cover(?:ing)?|for|handle|support(?:ing)?)\s+"
          r"(?P<query>[^?!.]{2,120})[?!.]*",
        text,
        re.I,
    )
    if skill_filter and "skills" in recently_executed_families(rows):
        query = re.sub(r"\s+", " ", skill_filter["query"]).strip()
        return RequiredReadOperation(
            "manage_skills", {"action": "search", "query": query}, maximum,
        )
    what_about = re.fullmatch(
        _REQUEST_PREFIX + r"what\s+about\s+(?:(?:my|our|the)\s+)?"
        r"(?P<target>notes|calendar|calendar\s+events|events|tasks|scheduled\s+tasks|"
        r"documents|docs|memories|memory|skills)[.!?]*",
        text,
        re.I,
    )
    if what_about:
        tool, action = _READ_LIST_TARGETS[what_about["target"].lower()]
        return RequiredReadOperation(tool, {"action": action} if action else {}, maximum)
    if operation := _exact_id_read(text, maximum):
        return operation
    targets = "|".join(re.escape(target) for target in _READ_LIST_TARGETS)
    match = re.fullmatch(
        _REQUEST_PREFIX + r"(?:list|show|read)\s+(?:me\s+)?(?:(?:my|the|all)\s+)?"
        r"(?:(?:first\s+)?(?P<count>[1-9]\d*)\s+)?(?P<target>" + targets + r")"
        r"(?:\s*,?\s+please)?[.!?]*", text, re.I,
    )
    if match:
        tool, action = _READ_LIST_TARGETS[match["target"].lower()]
        count = int(match["count"]) if match["count"] else None
        if maximum is not None:
            count = maximum if count is None else min(count, maximum)
        return RequiredReadOperation(tool, {"action": action} if action else {},
                                     count)
    # A single unambiguous misspelled family target may still seal an explicit
    # list/show/read request. Keep this narrower than capability routing: no
    # mailbox contents, web, shell, identifiers, compounds, or mutations.
    fuzzy_family = _complete_fuzzy_read_family(text)
    if fuzzy_family is not None:
        exact_families = {
            family for family, pattern in _FAMILY_WORDS.items()
            if re.search(pattern, text, re.I)
        }
        if (fuzzy_family in _FUZZY_SAFE_READS
                and len(exact_families) <= 1
                and (not exact_families or fuzzy_family in exact_families)
                and not re.search(
            r"[;\n]|\b(?:and\s+(?:send|delete|edit|create|add|remove|change|update)|"
            r"send|delete|edit|create|add|remove|change|update)\b", text, re.I,
        )):
            tool, action = _FUZZY_SAFE_READS[fuzzy_family]
            return RequiredReadOperation(tool, {"action": action} if action else {}, maximum)
    return None


def _families_for_tool(tool: str) -> frozenset[str]:
    """Resolve overlapping helper tools to their dedicated product family."""
    bare = str(tool or "")
    if bare.startswith("mcp__email__"):
        bare = bare[len("mcp__email__"):]
    if bare in {"manage_contact", "resolve_contact"}:
        return frozenset({"contacts"})
    if bare in {"list_sessions", "manage_session", "create_session", "send_to_session",
                "chat_with_model", "pipeline"}:
        return frozenset({"sessions"})
    return frozenset(family for family, tools in FAMILY_TOOLS.items() if bare in tools)


def _clause_capabilities(text: str) -> set[str]:
    # A prohibition constrains authority; it must never grant the family named
    # only as the forbidden side effect (for example, "do not create a file").
    if _is_pure_action_prohibition(text):
        return set()
    container_tool = creation_container_tool(text)
    if container_tool:
        return {'tasks' if container_tool == 'manage_tasks' else 'notes'}
    if re.fullmatch(
        r"\s*(?:please\s+)?solve\s+(?:the|this)\s+task\s+efficiently\s+"
        r"before\s+(?:the\s+)?timeout(?:\s*\([^)]*\))?\s*",
        text,
        re.I,
    ):
        # Execution boilerplate describes the current turn; it is not a
        # request to operate on the user's background-task scheduler.
        return set()
    if delegated := re.match(
        r"^\s*(?:your|the)\s+task\s+is\s+to\s+(?P<request>[\s\S]+)$",
        text,
        re.I,
    ):
        # ``task`` labels the current instruction here; route the actual
        # request body instead of granting background-scheduler authority.
        return _clause_capabilities(delegated["request"])
    if conditional := _CONDITIONAL_ACTION.fullmatch(text):
        # The premise supplies context; the post-condition clause owns the
        # requested side effect and therefore its product family.
        return _clause_capabilities(conditional["action"])
    if (
        re.search(r"\b(?:tasks?|jobs?|automations?)\b(?!\s+ids?\b)", text, re.I)
        and re.search(
            r"\b(?:recurring|repeating|every|daily|weekly|monthly|scheduled|"
            r"pause|resume|restart|run|delete|remove)\b",
            text,
            re.I,
        )
        and not re.search(
            r"\b(?:calendar|events?|meetings?|appointments?|reservations?)\b",
            text,
            re.I,
        )
    ):
        # A background automation may mention a weekday and personal data it
        # will process. Those are schedule/input details, not authorization to
        # substitute a calendar event or Notes mutation.
        return {"tasks"}
    if _CONTEXTUAL_CALENDAR_ACTION.search(text):
        # Blocking or reserving a dated/time-bounded slot is intrinsically a
        # calendar operation even when the user does not repeat "calendar".
        return {"calendar"}
    if re.match(
        r"^\s*" + _REQUEST_PREFIX
        + r"(?:add|create|write|save)\s+(?:(?:a|the|my|new|quick|short|freeform|temporary)\s+)*note\b",
        text, re.I,
    ):
        # The created note owns all following title/body text. Product words
        # inside that content are data, not additional tool authority.
        return {"notes"}
    if (
        re.match(r"^\s*" + _REQUEST_PREFIX + r"(?:also\s+)?save\b", text, re.I)
        and re.search(r"\b(?:to|as|in)\s+(?:a\s+|my\s+)?note\b", text, re.I)
    ):
        return {"notes"}
    if (
        re.match(r"^\s*" + _REQUEST_PREFIX + r"(?:open|show|read|view)\b", text, re.I)
        and re.search(r"\bnotes?\b", text, re.I)
        and not re.search(r"\b(?:panel|sidebar|tab|screen)\b", text, re.I)
    ):
        # The direct object owns a read. Words such as Settings, Calendar,
        # Email, or Model may be part of a note title and must not broaden
        # the offered family.
        return {"notes"}
    if (
        re.search(r"\b(?:delete|remove)\b", text, re.I)
        and re.search(r"\bnotes?\b", text, re.I)
    ):
        # The object being deleted owns the operation. Incidental words in a
        # note title or condition must not add UI/calendar authority.
        return {"notes"}
    if re.match(
        r"^\s*" + _REQUEST_PREFIX + r"(?:pull\s+up|bring\s+up|retrieve|get)\b",
        text,
        re.I,
    ):
        named = {
            family for family, pattern in _FAMILY_WORDS.items()
            if re.search(pattern, text, re.I)
        }
        if named:
            return named
    if re.match(
        r"^\s*" + _REQUEST_PREFIX + r"pull\b[^?!.]{0,140}\bup\b",
        text,
        re.I,
    ):
        # Natural phrasal verbs may place the object between "pull" and
        # "up" ("pull my calendar events up again"). Resolve the named
        # product exactly as the contiguous "pull up" form does.
        named = {
            family for family, pattern in _FAMILY_WORDS.items()
            if re.search(pattern, text, re.I)
        }
        if named:
            return named
    if _MISSPELLED_RESEARCH_ACTION.search(text):
        return {"research"}
    if (
        re.match(r"^\s*" + _REQUEST_PREFIX + r"(?:show|list|check)\b", text, re.I)
        and re.search(r"\bcalendar\b", text, re.I)
        and not re.search(r"\b(?:panel|view|sidebar|screen|tab)\b", text, re.I)
        and not re.search(
            r"\b(?:notes?|tasks?|skills?|memories|memory|documents?|docs?|emails?|inbox)\b",
            text,
            re.I,
        )
    ):
        # Showing calendar records is a data read. Only explicit surface
        # nouns such as panel/view authorize client navigation.
        return {"calendar"}
    if (
        re.match(r"^\s*what(?:['’]?s|\s+is)\s+on\s+(?:(?:my|our|the)\s+)?calendar\b", text, re.I)
    ):
        return {"calendar"}
    intent = classify_tool_intent(text)
    # A named personal-store switch such as "what about my notes" is a
    # lookup, not a question about what the Notes feature is.  The legacy
    # intent classifier labels both as explanatory, so let the stricter
    # personal lookup grammar below resolve the former.
    personal_lookup = bool(_LOOKUP.search(text) or _PERSONAL_STORE_LOOKUP.search(text))
    if intent.reason == "explanatory feature question" and not personal_lookup:
        return set()
    operation = required_read_operation_for_request(text)
    if operation is not None:
        return set(_families_for_tool(operation.tool))
    if selected := selected_tools_for_request(text):
        return set().union(*(_families_for_tool(tool) for tool in selected))
    for family, pattern in _MEDIA_REQUESTS:
        if pattern.search(text):
            return {family}
    if _PERSONAL_CALENDAR_SCHEDULE.search(text):
        return {"calendar"}
    # This legacy routing hint assumes any terse action refers to a calendar.
    # A contract must resolve the actual antecedent instead.
    if intent.reason == "terse calendar follow-up action":
        return set()
    words = {f for f, pattern in _FAMILY_WORDS.items() if re.search(pattern, text, re.I)}
    personal_stores = words & {
        "calendar", "notes", "tasks", "skills", "memory", "documents", "email",
    }
    if personal_lookup and len(personal_stores) == 1:
        # A named personal store outranks typo heuristics over incidental
        # prose (for example, "sitting in my Primary Inbox").
        return personal_stores
    fuzzy_near_action = _fuzzy_family(" ".join(re.findall(r"[a-z]+", text.lower())[:5]))
    first_token_family = _fuzzy_family(" ".join(re.findall(r"[a-z]+", text.lower())[:1]))
    fuzzy_gate = (
        intent.reason != "explanatory feature question"
        and not re.match(r"^\s*what\s+(?:is|are)\s+(?:an?\s+|the\s+)?", text, re.I)
        and (_has_action_signal(text) or personal_lookup or _CONVERSATIONAL_FOLLOWUP.search(text)
             or re.match(r"^\s*(?:what|which|where|any|do|have)\b", text, re.I)
             or _fuzzy_family(" ".join(re.findall(r"[a-z]+", text.lower())[:2])) == "search_browser"
             or first_token_family == "memory")
    )
    fuzzy = (fuzzy_near_action or _fuzzy_family(text)) if fuzzy_gate else None
    if fuzzy and not words:
        return {fuzzy}
    if fuzzy and fuzzy == fuzzy_near_action and fuzzy not in words:
        return {fuzzy}
    if (fuzzy_near_action == "search_browser"
            and (first_token_family == "search_browser"
                 or re.match(r"^\s*(?:navigate|browse)\b", text, re.I))):
        return {"search_browser"}
    if fuzzy == "search_browser" and words == {"search_browser"}:
        return {fuzzy}
    if words == {"search_browser"} and re.search(
        r"\b(?:best|recommend(?:ed|ation)?|which|what|where)\b", text, re.I,
    ):
        # Read-only web recommendations are often phrased declaratively
        # ("I want X; what's the best website") rather than as an imperative
        # search verb. Keep them out of typo-based shell/file routing and let
        # the normal Web permission policy decide whether lookup can execute.
        return {"search_browser"}
    if "memory" in words:
        words.discard("sessions")  # Historical chat retrieval uses search_chats.
    if words == {"skills"} and _has_action_signal(text):
        return {"skills"}
    if "research" in words and re.match(
        r"^\s*" + _REQUEST_PREFIX + r"(?:start|begin|launch|run|research|kick\s+off)\b",
        text,
        re.I,
    ):
        # The research report is the requested artifact. A returned task/job
        # identifier is metadata for that background run, not a Tasks object.
        return {"research"}
    if (
        "research" in words and "ui" in words
        and re.search(r"\bresearch\s+(?:panel|sidebar)\b", text, re.I)
    ):
        # Saved research has a dedicated open/read surface; research is not a
        # valid ui_control panel enum, so the generic word "panel" must not
        # offer an impossible UI operation.
        words.discard("ui")
    # The legacy action-intent classifier treats scheduling language as a
    # calendar operation. An explicit task/todo noun is the stronger product
    # contract unless the user also names the calendar family.
    if "tasks" in words and "calendar" not in words:
        return {"tasks"}
    # In a personal-store lookup, one explicitly named store owns the read;
    # words describing its labels/content are filters, not second products.
    # A noun conjunction requests both domains even when action_intents only
    # returns its first match ("list notes and calendar").
    mentions = sorted((m.start(), m.end(), f) for f in words
                      for m in re.finditer(_FAMILY_WORDS[f], text, re.I))
    combined = set()
    for left, right in zip(mentions, mentions[1:]):
        if re.fullmatch(r"\s*(?:,\s*(?:and\s+)?|and\s+|&\s*)(?:(?:my|the)\s+)?",
                        text[left[1]:right[0]], re.I):
            combined.update({left[2], right[2]})
    if combined:
        return combined
    if "email" in words and re.search(
        r"\b(?:show|list|check|open)\s+(?:me\s+)?(?:my\s+)?inbox\b|"
        r"^\s*any\s+emails?\b|^\s*what(?:['’]s|\s+is|\s+are)\s+today['’]?s\s+emails?\b",
        text, re.I,
    ):
        return {"email"}
    if intent.reason == "bare shell command request" and words and "shell_files" not in words:
        # Natural-language "find my contacts" is not the Unix find command.
        return words
    if (
        intent.reason == "bare shell command request"
        and re.match(r"^\s*find\b", text, re.I)
        and not re.match(r"^\s*find\s+(?:[./~]|-[A-Za-z])", text, re.I)
    ):
        # In a coordinated natural-language request, `find` is commonly a
        # goal rather than the Unix command. Require shell-shaped arguments
        # before granting filesystem authority.
        return words
    if intent.needs_tools:
        mapped = {"web": "search_browser",
                  "workspace": "shell_files", "shell": "shell_files"}.get(intent.category, intent.category)
        if mapped in {"shell_files", "search_browser"} and words and mapped not in words:
            # Broad legacy classifiers treat verbs such as "find" and
            # "search" as shell/web requests. Explicit product nouns are
            # stronger evidence: "search my memories/calendar" stays inside
            # that private product family unless web/shell was also named.
            return words
        # Explicit task requests belong to the scheduler, although the older
        # action router groups tasks with notes and reminders.
        if mapped == "notes" and "tasks" in words and "notes" not in words:
            mapped = "tasks"
        if mapped in FAMILY_TOOLS:
            return {mapped}
    # Families absent from action_intents still need a generic action gate;
    # merely discussing a domain must not offer its mutation tools.
    return words if _has_action_signal(text) or personal_lookup else set()


def canonical_tool(name: str) -> str:
    name = str(name or "")
    return name.rsplit("__", 1)[-1] if name.startswith("mcp__email__") else name


def recently_executed_families(history: Iterable, *, user_turns: int = 6,
                               maximum: int = 3, include_failed_attempts: bool = False) -> tuple[str, ...]:
    """Return bounded, most-recent families proven by persisted tool events."""
    found: list[str] = []
    turns = 0
    for row in reversed(tuple(history)):
        role = row.get("role") if isinstance(row, dict) else getattr(row, "role", "")
        if role == "user":
            turns += 1
            if turns > user_turns:
                break
        metadata = row.get("metadata") if isinstance(row, dict) else getattr(row, "metadata", None)
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (TypeError, json.JSONDecodeError):
                metadata = {}
        for event in reversed((metadata or {}).get("tool_events") or []):
            if event.get("error") is True or event.get("exit_code") not in (None, 0):
                if not (include_failed_attempts
                        and event.get("execution_attempted") is True
                        and event.get("blocked") is False):
                    continue
            tool = canonical_tool(event.get("tool", ""))
            family = None
            if tool == "ui_control":
                command = event.get("command") or {}
                if isinstance(command, str):
                    try:
                        command = json.loads(command)
                    except (TypeError, json.JSONDecodeError):
                        command = {}
                panel = str((command or {}).get("name") or (command or {}).get("panel") or "").lower()
                if (command or {}).get("action") == "open_panel":
                    family = {
                        "calendar": "calendar", "notes": "notes", "email": "email",
                        "documents": "documents", "sessions": "sessions",
                        "skills": "skills", "memory": "memory", "memories": "memory",
                        "brain": "memory", "cookbook": "cookbook_admin",
                    }.get(panel)
            family = family or next(iter(_families_for_tool(tool)), None)
            if family and family not in found:
                found.append(family)
                if len(found) >= maximum:
                    return tuple(found)
    return tuple(found)


_CONTINUITY_STOP_WORDS = frozenset({
    "a", "about", "an", "and", "are", "at", "be", "but", "can", "could",
    "did", "do", "does", "for", "from", "get", "have", "how", "i", "in",
    "is", "it", "look", "me", "my", "not", "of", "on", "or", "please",
    "search", "searched", "searching", "see", "show", "that", "the", "them",
    "there", "these", "this", "those", "to", "u", "was", "what", "when",
    "where", "which", "why", "with", "you", "your", "whats", "what's",
    "cant", "can't", "cannot", "dont", "don't", "doesnt", "doesn't",
})


def _subject_tokens(value: object) -> frozenset[str]:
    """Return content-bearing tokens for conversation-subject continuity."""
    return frozenset(
        token for token in re.findall(r"[\w'-]+", str(value or "").casefold())
        if len(token) > 2 and token not in _CONTINUITY_STOP_WORDS
    )


def _immediate_prior_user_subject_tokens(history: Iterable) -> frozenset[str]:
    rows = tuple(history or ())
    seen_assistant = False
    for row in reversed(rows):
        role = row.get("role") if isinstance(row, dict) else getattr(row, "role", "")
        if role == "assistant" and not seen_assistant:
            seen_assistant = True
            continue
        if seen_assistant and role == "user":
            content = row.get("content", "") if isinstance(row, dict) else getattr(row, "content", "")
            return _subject_tokens(content)
    return frozenset()


def result_reference_followup(message: str) -> bool:
    """Recognize subject-less result references, not new subjects or actions."""
    return bool(re.fullmatch(
        r"\s*(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?(?:"
        r"(?:links?|sources?|urls?)(?:\s+(?:for|to))?(?:\s+more\s+(?:info(?:rmation)?|details?))?"
        r"|(?:more\s+)?(?:info(?:rmation)?|details?)(?:\s+(?:on|about)\s+(?:that|this|it))?"
        r"|(?:give|show|send)\s+(?:me\s+)?(?:the\s+)?(?:links?|sources?|urls?)(?:\s+(?:for|to)\s+(?:that|this|it|those|these))?"
        r"|(?:open|read|expand)\s+(?:that|this|it|the\s+(?:first|second|third|last)\s+(?:one|result|link|source))"
        r")(?:\s+(?:please|pls))?[.!?]*\s*", str(message or ''), re.I,
    ))


def immediately_established_family(message: str, history: Iterable) -> str | None:
    """Resolve an elliptical follow-up against the immediately proven domain.

    Tool events provide the typed domain; subject-token overlap only determines
    whether the new sentence continues that turn. This deliberately does not
    infer authority from older turns or from model prose.
    """
    rows = tuple(history or ())
    assistant_index = None
    families: set[str] = set()
    for index in range(len(rows) - 1, -1, -1):
        row = rows[index]
        role = row.get("role") if isinstance(row, dict) else getattr(row, "role", "")
        if role != "assistant":
            continue
        assistant_index = index
        metadata = row.get("metadata") if isinstance(row, dict) else getattr(row, "metadata", None)
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (TypeError, json.JSONDecodeError):
                metadata = {}
        for event in (metadata or {}).get("tool_events") or ():
            if event.get("error") is True or event.get("exit_code") not in (None, 0):
                continue
            families.update(_families_for_tool(canonical_tool(event.get("tool", ""))))
        break
    if assistant_index is None or len(families) != 1:
        return None

    prior_user_text = ""
    for row in reversed(rows[:assistant_index]):
        role = row.get("role") if isinstance(row, dict) else getattr(row, "role", "")
        if role == "user":
            prior_user_text = row.get("content", "") if isinstance(row, dict) else getattr(row, "content", "")
            break
    if not prior_user_text:
        return None
    if result_reference_followup(message) or _subject_tokens(message) & _subject_tokens(prior_user_text):
        return next(iter(families))
    return None


def recently_read_gallery(history: Iterable, *, user_turns: int = 6) -> bool:
    """Whether a recent successful app_api call established gallery context."""
    turns = 0
    for row in reversed(tuple(history)):
        role = row.get("role") if isinstance(row, dict) else getattr(row, "role", "")
        if role == "user":
            turns += 1
            if turns > user_turns:
                break
        metadata = row.get("metadata") if isinstance(row, dict) else getattr(row, "metadata", None)
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (TypeError, json.JSONDecodeError):
                metadata = {}
        for event in reversed((metadata or {}).get("tool_events") or []):
            if event.get("error") is True or event.get("exit_code") not in (None, 0):
                continue
            if canonical_tool(event.get("tool", "")) != "app_api":
                continue
            command = event.get("command") or {}
            if isinstance(command, str):
                try:
                    command = json.loads(command)
                except (TypeError, json.JSONDecodeError):
                    command = {}
            if str((command or {}).get("path") or "").split("?", 1)[0] == "/api/gallery/library":
                return True
    return False


def recently_read_gallery(history: Iterable, *, user_turns: int = 4) -> bool:
    """Whether recent successful typed evidence came from the owned gallery."""
    turns = 0
    for row in reversed(tuple(history)):
        role = row.get("role") if isinstance(row, dict) else getattr(row, "role", "")
        if role == "user":
            turns += 1
            if turns > user_turns:
                break
        metadata = row.get("metadata") if isinstance(row, dict) else getattr(row, "metadata", None)
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (TypeError, json.JSONDecodeError):
                metadata = {}
        for event in reversed((metadata or {}).get("tool_events") or []):
            if (canonical_tool(event.get("tool", "")) != "app_api"
                    or event.get("error") is True
                    or event.get("exit_code") not in (None, 0)):
                continue
            command = event.get("command") or {}
            if isinstance(command, str):
                try:
                    command = json.loads(command)
                except (TypeError, json.JSONDecodeError):
                    command = {}
            if str((command or {}).get("path") or "").startswith("/api/gallery/"):
                return True
    return False


# Personal-data product nouns. A broad-briefing phrase ("what's new",
# "give me an update", "news") must not out-rank these: the user is asking
# about their own store, not the open Web. First-person possessives,
# explicit mailbox nouns, and concrete email references identify the store;
# open-web subjects such as "the latest events in Kyiv" keep their Web route.
_PERSONAL_STORE_NOUNS = (
    r"(?:e?mails?|inbox|mailbox|calendar|calender|events?|appointments?|"
    r"meetings?|agenda|notes?|checklists?|tasks?|todos?|documents?|docs?|"
    r"memor(?:y|ies)|contacts?|skills?|sessions?|chats?|conversations?)"
)
_PERSONAL_STORE_SUBJECT = re.compile(
    rf"\b(?:my|our)\b(?:\s+\w+){{0,2}}\s+{_PERSONAL_STORE_NOUNS}\b|"
    rf"\b(?:inbox|mailbox)\b|"
    r"\b(?:the|this|that)\s+(?:(?:latest|last|newest|recent)\s+)?"
    r"email\s+(?:from|about|regarding|sent|received)\b",
    re.I,
)


_PERSONAL_STORE_FAMILY = (
    (("email", "emails", "mail", "mails", "inbox", "mailbox"), "email"),
    (("calendar", "calender", "event", "events", "appointment", "appointments",
      "meeting", "meetings", "agenda"), "calendar"),
    (("note", "notes", "checklist", "checklists"), "notes"),
    (("task", "tasks", "todo", "todos"), "tasks"),
    (("document", "documents", "doc", "docs"), "documents"),
    (("memory", "memories"), "memory"),
    (("contact", "contacts"), "contacts"),
    (("skill", "skills"), "skills"),
    (("session", "sessions", "chat", "chats", "conversation", "conversations"),
     "sessions"),
)


def names_personal_store(message: str) -> bool:
    """True when the request names the user's own data store."""
    return bool(_PERSONAL_STORE_SUBJECT.search(str(message or "")))


def personal_store_families(message: str) -> frozenset[str]:
    """Families for the user's own stores named in a broad-briefing request.

    A briefing phrase must resolve to the named store rather than falling
    through to an empty inventory, which would offer no tools at all.
    """
    families: set[str] = set()
    for match in _PERSONAL_STORE_SUBJECT.finditer(str(message or "")):
        matched = match.group(0).lower()
        for nouns, family in _PERSONAL_STORE_FAMILY:
            if any(re.search(rf"\b{noun}\b", matched) for noun in nouns):
                families.add(family)
    return frozenset(families)


def broad_web_briefing_request(message: str) -> bool:
    """Recognize requests that need broad, current, multi-source Web evidence."""
    if creation_container_tool(message):
        return False
    text = _normalize_request_lead(message)
    if re.search(
        r"\b(?:what(?:['’]?s|\s+is)\s+(?:new|happening)|anything\s+new|"
        r"catch\s+me\s+up|give\s+me\s+(?:an?\s+)?update|"
        r"what\s+should\s+i\s+know)\b",
        text,
        re.I,
    ):
        return True
    if re.search(r"\b(?:news|neews|nees|headlines?|top\s+stories|news\s+roundup)\b", text, re.I):
        return True
    if re.search(r"\b(?:research|investigate|deep[ -]?dive)\b", text, re.I):
        return True
    if re.search(r"\b(?:find|gather|look\s+for)\s+(?:supporting\s+)?evidence\b", text, re.I):
        return True
    if (
        re.search(r"\b(?:latest|recent|current|today(?:'s)?|right\s+now)\b", text, re.I)
        and re.search(
            r"\b(?:developments?|updates?|trends?|breakthroughs?|events?|stories|"
            r"recommendations?|reviews?|best|compare|comparison)\b",
            text,
            re.I,
        )
    ):
        return True
    return bool(
        re.search(r"\b(?:recommend|best)\b", text, re.I)
        and re.search(r"\b(?:current|latest|today|right\s+now|reviews?)\b", text, re.I)
    )


def corrected_browser_target(message: str, history: Iterable = ()) -> dict | None:
    """Bind a URL-only correction to a recent explicit browsing objective."""
    from urllib.parse import urlsplit

    def target(text):
        match = re.fullmatch(
            r"(?:try\s+|use\s+)?((?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?::\d+)?(?:/[^\s<>]*)?)",
            text.strip(), re.I,
        )
        if not match:
            return None
        url = match[1]
        url = url if '://' in url else 'https://' + url
        return url if urlsplit(url).hostname else None

    url = target(str(message or ''))
    if not url:
        return None
    turns = 0
    for row in reversed(tuple(history)):
        if isinstance(row, dict) and row.get('_harness_control'):
            continue
        role = row.get('role') if isinstance(row, dict) else getattr(row, 'role', '')
        if role != 'user':
            continue
        content = row.get('content', '') if isinstance(row, dict) else getattr(row, 'content', '')
        if not isinstance(content, str):
            return None
        turns += 1
        if turns > 4:
            break
        if target(content) or re.fullmatch(r'(?:please\s+)?browse (?:their|the) (?:website|site)', content.strip(), re.I):
            continue
        if re.match(r'^(?:please\s+)?(?:browse|visit|open)\s+', content.strip(), re.I) and re.search(
            r'(?:https?://|\b[a-z0-9-]+\.[a-z]{2,}\b)', content, re.I,
        ):
            return {'url': url, 'objective': content}
        # An intervening unrelated user request breaks the reference.
        return None
    return None


def requested_capabilities(message: str, history: Iterable = (), *, active_document=False, workspace=False, image_attachment=False) -> frozenset[str]:
    """Classify once; inherit a prior capability only for a referential follow-up."""
    message = _routing_email_scope(editor_request_instructions(message))
    raw_text = str(message or "").strip()
    text = _normalize_request_lead(message)
    if lead := _CONVERSATIONAL_ACTION_LEAD.fullmatch(text):
        text = lead["request"].strip()
    history = tuple(history)
    if corrected_browser_target(raw_text, history):
        return frozenset({'search_browser'})
    container_tool = creation_container_tool(raw_text)
    if container_tool:
        # The payload describes future work, not a competing operation now.
        # Keep family selection consistent with selected_tools_for_request.
        return frozenset({'tasks' if container_tool == 'manage_tasks' else 'notes'})
    if image_edit_followup(raw_text, history, image_attachment=image_attachment):
        return frozenset({'image_editing'})
    image_tools = image_creation_tools(raw_text)
    if image_tools:
        return frozenset({'image_generation'} | ({'documents'} if 'update_document' in image_tools else set()))
    if standalone_code_request(raw_text):
        return frozenset({'documents'})
    repeated_subject = _subject_tokens(text) & _immediate_prior_user_subject_tokens(history)
    scope_text = " ".join(
        token for token in re.findall(r"[\w'-]+", text)
        if token.casefold() not in repeated_subject
    )
    newly_named_families = {
        family for family, pattern in _FAMILY_WORDS.items()
        if re.search(pattern, scope_text, re.I)
    }
    established_family = immediately_established_family(text, history)
    if established_family and not newly_named_families:
        return frozenset({established_family})
    if selected_tools_for_request(raw_text) == frozenset({"manage_settings"}):
        return frozenset({"cookbook_admin"})
    concrete_urls = re.findall(r"\bhttps?://[^\s<>\"']+", raw_text, re.I)
    workspace_media = re.search(
        r"(?:file://)?/workspace/[^\s`\"']+\."
        r"(?:avif|bmp|gif|jpe?g|png|svg|tiff?|webp|mp3|m4a|ogg|wav|flac|"
        r"aac|mp4|m4v|mov|mkv|avi|webm)\b",
        raw_text,
        re.I,
    )
    media_action = re.search(
        r"\b(?:inspect|view|watch|review|study|look\s+at|analy[sz]e|read|transcribe|caption|"
        r"recreate|reproduce|identify|describe|extract)\b|"
        r"(?:浏览|查看|观看|分析|检查|识别|转录|截图)",
        raw_text,
        re.I,
    )
    if workspace_media and media_action:
        # A concrete media asset owns score "notes", timestamp ranges, and
        # other content nouns. Those details must not authorize unrelated
        # personal Notes or Calendar products. Preserve only explicit
        # downstream artifact/browser work and deliberate personal-note
        # mutations.
        if re.search(r"\bOCR\b|\bextract\b[^.\n]{0,80}\b(?:exact\s+)?(?:visible\s+)?text\b", raw_text, re.I):
            primary_media_family = "ocr"
        elif re.search(r"\b(?:transcribe|transcription|captions?|subtitles?)\b", raw_text, re.I):
            primary_media_family = "transcription"
        else:
            primary_media_family = "media_inspection"
        families = {primary_media_family}
        if (
            re.search(
                r"\b(?:create|write|save|build|implement|produce|recreate|reproduce)\b|"
                r"(?:创建|写入|保存|生成|输出|拼成|制作)",
                raw_text,
                re.I,
            )
            and _mentions_workspace_output(raw_text)
        ):
            families.add("shell_files")
        if re.search(
            r"\b(?:preview|render|open|inspect|verify)\b[^.\n]{0,120}"
            r"\b(?:page|html|browser)\b",
            raw_text,
            re.I,
        ) or (
            re.search(
                r"\b(?:preview|render|open|inspect|verify)\b[^.\n]{0,120}"
                r"\brendered\s+result\b",
                raw_text,
                re.I,
            )
            and not re.search(r"\binspect_media\b", raw_text, re.I)
        ):
            families.add("search_browser")
        if re.search(
            r"\b(?:create|add|write|save)\b[^.;\n]{0,80}\b(?:a\s+)?note\b"
            r"[^.;\n]{0,80}\b(?:my\s+)?notes\b",
            raw_text,
            re.I,
        ):
            families.add("notes")
        return frozenset(families)
    if (
        broad_web_briefing_request(text)
        and not re.search(r"\b(?:research|investigate|deep[ -]?dive)\b", text, re.I)
    ):
        selected = selected_tools_for_request(raw_text)
        if selected:
            # Keep family scope consistent with the concrete operation. A
            # subject such as "latest design review" is not a web directive.
            return frozenset().union(*(_families_for_tool(tool) for tool in selected))
        _personal = personal_store_families(text)
        if _personal:
            return _personal
        return frozenset({"search_browser"})
    if re.search(r"\b(?:web_search|web_fetch)\b", raw_text, re.I):
        # Explicit native-tool requests are stronger than incidental domain
        # words in the research subject (for example, Git ``pull`` must not
        # route to scheduled tasks). Keep the whole read-only web family so a
        # weak search can recover through fetch/browser. A compound artifact
        # workflow may also have been deliberately selected with a bounded
        # workspace tool surface; preserve that independent family.
        selected = selected_tools_for_request(raw_text)
        selected_families = (
            frozenset().union(*(_families_for_tool(tool) for tool in selected))
            if selected else frozenset()
        )
        return selected_families or frozenset({"search_browser"})
    if (
        re.search(r"\b(?:latest|recent|current|today(?:'s)?)\b", text, re.I)
        and re.search(r"\b(?:info(?:rmation)?|news|nees|updates?)\b", text, re.I)
        and not names_personal_store(text)
    ):
        # A named personal store out-ranks the broad-briefing route; the
        # guard above lets those fall through to the family grammar.
        # Broad current-information requests still require live Web evidence.
        # Keep the common ``nees`` typo because a missed route leaves the model
        # with no way to answer and encourages it to ask unnecessary questions.
        return frozenset({"search_browser"})
    if (
        re.search(r"\b(?:online|on\s+the\s+(?:web|internet))\b", text, re.I)
        and re.search(
            r"\b(?:find|look|search|check|locate|get|download|available|manual|guide|docs?)\b",
            text,
            re.I,
        )
    ):
        # Explicitly asking Odysseus to look online is sufficient web intent,
        # including referential follow-ups such as "Can you look online?".
        return frozenset({"search_browser"})
    if (
        len(concrete_urls) >= 2
        and re.search(r"\b(?:open|fetch|read|retrieve|check|use)\b", text, re.I)
        and re.search(
            r"\b(?:compare|contrast|synthesi[sz]e|explain|summari[sz]e|cite|citing|evidence)\b",
            text,
            re.I,
        )
        and not re.search(
            r"\b(?:click|fill|submit|login|log\s+in|screenshot|render|navigate)\b",
            text,
            re.I,
        )
    ):
        # Product words inside source titles (for example "documentation")
        # describe remote evidence, not the user's Odysseus document library.
        return frozenset({"search_browser"})
    # The browser-confirmed visible editor is a typed target, stronger than
    # incidental nouns inside the requested content or a pasted style guide.
    # Resolve it before lexical family rules can mistake words such as
    # "mailbox", "sender", or "reply" for an Email data operation.
    if active_document and targets_bound_editor_request(text):
        families = {"documents"}
        if _bound_editor_requests_web_verification(text):
            families.add("search_browser")
        return frozenset(families)
    if (
        re.search(r"\b(?:look\s+at|check|inspect|review|read|open|show|list)\b[^.;\n]{0,180}\bcalendar\b", text, re.I)
        and re.search(
            r"\b(?:draft|write|compose|create)\b[^.;\n]{0,180}\b(?:e-?mail|message)\b"
            r"|\b(?:e-?mail|message)\s+draft\b",
            text,
            re.I,
        )
    ):
        # The draft depends on calendar evidence, so both schemas must remain
        # available in one turn instead of freezing on the calendar read.
        return frozenset({"calendar", "email"})
    explicit_document_workflow = bool(
        re.search(
            r"\b(?:create|add|write|draft|edit|update|search|find|locate|suggest|delete|remove)\b"
            r"[^.;\n]{0,100}\b(?:documents?|docs?|document\s+library)\b"
            r"|\b(?:documents?|docs?)\b[^.;\n]{0,100}"
            r"\b(?:titled|named|called|library|suggest|delete|remove)\b",
            text,
            re.I,
        )
    )
    explicit_note_workflow = bool(
        re.search(
            r"\b(?:create|add|write|edit|update|search|find|list|delete|remove)\b"
            r"\s+(?:(?:a|an|the|my|our)\s+)?notes?\b",
            text,
            re.I,
        )
    )
    explicit_email_workflow = bool(re.search(
        r"\b(?:write|draft|compose|create)\s+"
        r"(?:(?:a|an|the|new|unsent)\s+)*(?:e-?mail|message)\b", text, re.I,
    ))
    if explicit_document_workflow and not explicit_note_workflow and not explicit_email_workflow:
        # Words such as "notes" and "feedback" commonly occur inside a
        # document title/body. They must not expose the Notes product beside
        # an explicit document lifecycle and tempt the model into mutating the
        # wrong store.
        if "ui_control" in (selected_tools_for_request(text) or ()):
            return frozenset({"documents", "ui"})
        return frozenset({"documents"})
    recent_family = recently_executed_families(history, maximum=1)
    if result_reference_followup(text):
        reference_family = immediately_established_family(text, history)
        if reference_family:
            return frozenset({reference_family})
    if (
        re.search(
            r"\b(?:where(?:['’]?s|\s+is)|what\s+(?:country|place|city|region)\s+has)\s+"
            r"(?:the\s+)?best\b[^?!.]{2,180}[?!.]*$",
            text,
            re.I,
        )
        or re.fullmatch(
            _REQUEST_PREFIX + r"when\s+exactly\s+did\s+[^?!.]{2,100}\b"
            r"(?:gain|regain|declare|achieve)\s+independence[?!.]*",
            text,
            re.I,
        )
    ):
        return frozenset({"search_browser"})
    if (
        re.match(
            r"^(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:quick\s+)?look\s*up\b",
            raw_text,
            re.I,
        )
        and re.search(r"\bofficial\b[^\n]{0,80}\b(?:link|url|source)\b", raw_text, re.I)
    ):
        return frozenset({"search_browser"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:give|show)\s+me\s+(?:my\s+)?(?:"
        r"calend(?:ar|er)\s+for\s+(?:this|next)\s+week|upcoming\s+events)"
        r"(?:\s+(?:please|pls|plz))?[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"calendar"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"wat\s+(?:scheduled\s+)?ta(?:s)?ks\s+"
        r"do\s+i\s+have(?:\s+set\s+up)?(?:\s+rn)?[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"tasks"})
    if (
        re.search(r"\b(?:do\s+i\s+have|are\s+there)\b[^?!.]{0,80}\bskills?\b", text, re.I)
        and re.search(r"\b(?:cover|handle|handling|about|for)\b", text, re.I)
    ):
        return frozenset({"skills"})
    if (
        not recent_family
        and _REFERENCE.search(text)
        and (
            _has_action_signal(text)
            or re.search(r"\b(?:check|chek|verify|confirm)\b", text, re.I)
        )
    ):
        prior_user_turns = 0
        for row in reversed(history):
            role = row.get("role") if isinstance(row, dict) else getattr(row, "role", "")
            if role != "user":
                continue
            prior_user_turns += 1
            prior_text = row.get("content", "") if isinstance(row, dict) else getattr(row, "content", "")
            prior_selected = selected_tools_for_request(prior_text)
            if prior_selected:
                prior_families = frozenset().union(
                    *(_families_for_tool(tool) for tool in prior_selected)
                )
                if len(prior_families) == 1:
                    return prior_families
            if prior_user_turns >= 2:
                break
    if not recent_family and (
        _REFERENCE.search(text)
        or re.search(r"\b(?:views?|likes?|duration|runtime|uploaded?|published|percent)\b", text, re.I)
        or re.search(
            r"\b(?:i\s+mean|if\s+i\s+only\s+care\s+about|vs\.?|versus)\b",
            text,
            re.I,
        )
        or re.fullmatch(
            _REQUEST_PREFIX + r"(?:please\s+)?(?:try|retry|run|do)\s+"
            r"(?:that|it)(?:\s+again)?[?!.]*",
            text,
            re.I,
        )
    ):
        # A failed execution is not evidence for an answer, but a verified,
        # unblocked attempt does establish the immediate follow-up's tool
        # family. Keep that family for one user turn so the model can correct
        # its arguments or choose a sibling tool instead of losing access.
        recent_family = recently_executed_families(
            history, user_turns=1, maximum=1, include_failed_attempts=True
        )
    if recent_family and re.fullmatch(
        _REQUEST_PREFIX + r"(?:please\s+)?(?:try|retry|run|do)\s+"
        r"(?:that|it)(?:\s+again)?[?!.]*",
        text,
        re.I,
    ):
        return frozenset({recent_family[0]})
    if recent_family == ("search_browser",) and re.search(
        r"\b(?:pull|open|fetch|read)\b[^?!.]{0,100}\bsource\s+page\b",
        text,
        re.I,
    ):
        return frozenset({"search_browser"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:what(?:['’]?s|\s+is)|wats)\s+in\s+"
        r"(?:my|the)\s+skills?\s+library\b[^\n]*",
        text,
        re.I,
    ):
        return frozenset({"skills"})
    # An explicit request to persist the prior approach as a reusable skill is
    # a real family switch. Resolve it before broad Web follow-up vocabulary:
    # generated names such as “official source lookup” legitimately contain
    # words like “official” and “like” that otherwise resemble Web context.
    if re.search(
        r"\b(?:turn|save|stash)\b[^.;\n]{0,180}\b(?:this|that|it|approach|how\s+you\s+did\s+that)\b"
        r"[^.;\n]{0,180}\b(?:into|as)\s+(?:a\s+)?(?:reusable\s+)?skill\b",
        text,
        re.I,
    ):
        return frozenset({"skills"})
    if recent_family == ("cookbook_admin",) and re.search(
        r"\b(?:put|turn|switch|set)\s+(?:it|that)\s+back\s+on\b|"
        r"\b(?:check|chek|verify|confirm)\b[^?!.]{0,100}\b(?:back\s+on|enabled|active)\b",
        text,
        re.I,
    ):
        return frozenset({"cookbook_admin"})
    if (
        re.match(r"^\s*" + _REQUEST_PREFIX + r"remember\b", text, re.I)
        and re.search(r"\b(?:url|link|website|page)\b", text, re.I)
    ):
        # “Release notes” names the URL being saved; the requested side
        # effect belongs solely to persistent memory.
        return frozenset({"memory"})
    if (
        re.match(r"^\s*" + _REQUEST_PREFIX + r"check\s+(?:my|our|the)\s+project\b", text, re.I)
        and re.search(r"\b(?:calls?|uses?|references?|leftover|code|files?)\b", text, re.I)
    ):
        return frozenset({"shell_files"})
    if (
        re.search(r"\b(?:inbox|mailbox|mail)\b", text, re.I)
        and re.search(r"\b(?:undone|unanswered|unresponded|waiting\s+on\s+me|needs?\s+(?:a\s+)?reply)\b", text, re.I)
    ):
        return frozenset({"email"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"what(?:['’]?s|s|\s+is)\s+(?:my|our)\s+"
        r"(?:busiest|quietest|lightest|heaviest)\s+day\s+"
        r"(?:this|next)\s+(?:week|month)[?!.]*",
        text,
        re.I,
    ) or re.fullmatch(
        _REQUEST_PREFIX + r"what\s+do\s+(?:i|we)\s+have\s+after\s+"
        r"\d{1,2}(?::\d{2})?\s*(?:am|pm)\s+"
        r"(?:today|tomor{1,2}ow)[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"calendar"})
    if (
        re.search(r"\b(?:add|create|book|schedule)\b", text, re.I)
        and re.search(r"\b(?:calendar|meeting|appointment|event)\b", text, re.I)
        and not re.search(r"\b(?:add|create|write|save)\b[^.!?]{0,80}\bnotes?\b", text, re.I)
        and re.search(r"\b(?:today|tomor{1,2}ow|this\s+week|next\s+week|"
                      r"mon(?:day)?|tue(?:s|sday)?|wed(?:nesday)?|thu(?:rs|rsday)?|"
                      r"fri(?:day)?|sat(?:urday)?|sun(?:day)?)\b", text, re.I)
    ):
        return frozenset({"calendar"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:mon(?:day)?|tue(?:s|sday)?|wed(?:nesday)?|"
        r"thu(?:rs|rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?|"
        r"\d+(?:\.\d+)?\s*(?:hours?|hrs?|minutes?|mins?)\s+(?:should\s+be\s+fine)?|"
        r"(?:just\s+)?give\s+me\s+(?:an?\s+)?exact\s+time\s+that\s+works)"
        r"[?!.]*",
        text,
        re.I,
    ):
        for index in range(len(history) - 1, -1, -1):
            row = history[index]
            role = row.get("role") if isinstance(row, dict) else getattr(row, "role", "")
            if role != "user":
                continue
            content = row.get("content", "") if isinstance(row, dict) else getattr(row, "content", "")
            inherited = requested_capabilities(content, history[:index])
            if inherited == frozenset({"calendar"}):
                return inherited
            break
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:what(?:['’]?s|s|\s+is)|when(?:['’]?s|s|\s+is))\s+"
        r"(?:(?:my|our)\s+)?(?:cal(?:endar)?|sched(?:ule)?)\b[^.!?]{0,100}"
        r"\b(?:today|tomor{1,2}ow|this\s+(?:week|month)|next\s+(?:week|month))\b"
        r"[^.!?]*[.!?]*",
        text,
        re.I,
    ) or re.fullmatch(
        _REQUEST_PREFIX + r"what(?:['’]?s|s|\s+is)\s+on\s+today[.!?]*",
        text,
        re.I,
    ):
        return frozenset({"calendar"})
    if (
        re.fullmatch(
            _REQUEST_PREFIX + r"what(?:['’]?s|s|\s+is)\s+on\s+"
            r"(?:this|next)\s+(?:week|weekend|month)[.!?]*",
            text,
            re.I,
        )
        or re.fullmatch(
            _REQUEST_PREFIX + r"(?:what\s+do\s+i\s+have|do\s+i\s+have\s+anything)\s+"
            r"(?:on\s+)?(?:today|tomor{1,2}ow|(?:mon|tues?|wednes|thurs?|fri|satur|sun)day"
            r"(?:\s+(?:morning|afternoon|evening))?)[.!?]*",
            text,
            re.I,
        )
        or re.fullmatch(
            _REQUEST_PREFIX + r"what(?:['’]?s|s|\s+is)\s+my\s+"
            r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)(?:tember)?\s+"
            r"look(?:ing)?\s+like[.!?]*",
            text,
            re.I,
        )
    ):
        return frozenset({"calendar"})
    if (
        re.fullmatch(
            _REQUEST_PREFIX + r"what\s+notes?\s+have\s+(?:i|we)\s+got"
            r"(?:\s+(?:right|rite)\s+now)?[.!?]*",
            text,
            re.I,
        )
        or re.fullmatch(
            _REQUEST_PREFIX + r"what(?:['’]?s|s|\s+is)\s+left\s+(?:on|in)\s+"
            r"(?:my|our|the)?\s*[^.!?]{0,100}\bchecklist[.!?]*",
            text,
            re.I,
        )
        or re.fullmatch(
            _REQUEST_PREFIX + r"didn(?:['’]?t|t)\s+(?:i|we)\s+have\s+"
            r"(?:a\s+)?notes?\b[^.!?]*[.!?]*",
            text,
            re.I,
        )
    ):
        return frozenset({"notes"})
    if re.match(
        r"^\s*(?:(?:nice|great|ok(?:ay)?)[,!]?\s+)?jo(?:t|tt)\s+"
        r"(?:that|this|it|those|these|them)\s+down\b",
        text,
        re.I,
    ):
        return frozenset({"notes"})
    if (
        recent_family == ("search_browser",)
        and (
            re.search(r"\b\d+(?:\.\d+)?\s*[-–]\s*\d+(?:\.\d+)?\s*b\b", text, re.I)
            or (
                re.search(r"\b(?:that|this)\s+(?:the\s+)?same\s+one\b", text, re.I)
                and re.search(r"\b(?:linked?|source|site|docs?|page|url)\b", text, re.I)
            )
        )
    ):
        return frozenset({"search_browser"})
    if recent_family == ("search_browser",) and (
        re.fullmatch(
            _REQUEST_PREFIX + r"how(?:['’]?s|s|\s+is)\s+old\s+is\s+(?:it|that|this)"
            r"[?!.]*",
            text,
            re.I,
        )
        or re.fullmatch(
            _REQUEST_PREFIX + r"(?:does?|did)\s+(?:it|that|this)\s+"
            r"(?:mention|say|include|cover)\b[^.!?]{1,140}[?!.]*",
            text,
            re.I,
        )
    ):
        return frozenset({"search_browser"})
    if recent_family == ("search_browser",) and re.search(
        r"\b(?:open|read|show|view)\b[^?!.]{0,100}\b"
        r"(?:top\s+pick(?:['’]s)?|that|its|the)\s+model\s+card\b",
        text,
        re.I,
    ):
        return frozenset({"search_browser"})
    if recent_family == ("email",) and re.search(
        r"\b(?:tighten|shorten|rewrite|revise|edit|change|expand|polish)\b"
        r"[^?!.]{0,100}\b(?:paragraph|draft|wording|opening|middle|ending)\b",
        text,
        re.I,
    ):
        return frozenset({"documents"})
    if recent_family == ("search_browser",) and (
        re.search(r"\b(?:latest|newest|uploaded?|video|wayland|release\s+notes?|fan\s+account)\b", text, re.I)
        and re.search(r"\b(?:what|when|how|does?|did|is|are|has|have|sure|fix(?:es|ed)?)\b", text, re.I)
    ):
        return frozenset({"search_browser"})
    if recent_family == ("search_browser",) and (
        re.search(r"\bwhich\s+settings?\s+did\s+(?:it|they|the\s+authors?)\s+use\b", text, re.I)
        or re.search(r"\bquote\b[^?!.]{0,100}\b(?:exact|verbatim|line|passage|text)\b", text, re.I)
    ):
        return frozenset({"search_browser"})
    if recent_family == ("search_browser",) and (
        re.search(r"\b(?:views?|likes?|duration|runtime|percent|positive|travel\s+time)\b", text, re.I)
        or re.search(r"\bcompare\b[^?!.]{0,100}\b(?:other|more|different)\s+sources?\b", text, re.I)
    ):
        return frozenset({"search_browser"})
    if recent_family == ("search_browser",) and (
        (
            re.search(r"\b(?:which|pick|show|open)\b", text, re.I)
            and re.search(r"\b(?:ones?|top|apps?|services?|providers?)\b", text, re.I)
            and re.search(r"\b(?:price|cheap|under|dimensions?|quote|trustworthy|app)\b", text, re.I)
        )
        or _mentions_under_budget(text)
    ):
        return frozenset({"search_browser"})
    if recent_family == ("search_browser",) and re.fullmatch(
        _REQUEST_PREFIX + r"should\s+(?:i|we)\s+upgrade\s+(?:it\s+)?today[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"search_browser", "shell_files"})
    if recent_family == ("search_browser",) and re.fullmatch(
        _REQUEST_PREFIX + r"(?:which\s+lines?\s+and\s+how\s+long\s+does\s+it\s+take|"
        r"(?:and\s+)?the\s+last\s+train\s+back\s+tonight)[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"search_browser"})
    if recent_family == ("search_browser",) and (
        re.search(r"\b(?:youtube|video|channel|comments?|newest|latest|official|fan\s+account)\b", text, re.I)
        and re.search(r"\b(?:what|when|how|does?|did|is|are|sure|like|about|old)\b", text, re.I)
    ):
        return frozenset({"search_browser"})
    if recent_family == ("search_browser",) and (
        re.search(r"\b(?:last|latest|recent)\s+\d+\s+videos?\b", text, re.I)
        or re.search(r"\b(?:common\s+)?complaints?\b", text, re.I)
    ):
        return frozenset({"search_browser"})
    if recent_family == ("email",) and (
        re.search(r"\b(?:urgent|unread|waiting\s+on|needs?\s+(?:a\s+)?reply)\b", text, re.I)
        and re.search(r"\b(?:anything|which|what|account|ones?|messages?|emails?)\b", text, re.I)
    ):
        return frozenset({"email"})
    if recent_family == ("email",) and (
        re.fullmatch(
            _REQUEST_PREFIX + r"summari[sz]e\s+what\s+(?:each|every)\s+one\s+says?[?!.]*",
            text,
            re.I,
        )
        or (
            re.search(r"\b(?:anything|something|one)\s+from\s+(?:the\s+)?[^?!.]{2,80}\b", text, re.I)
            and re.search(r"\b(?:there|in\s+(?:there|them|those)|ones?)\b", text, re.I)
            and not re.search(r"\b(?:calendar|calender|notes?|tasks?|skills?|documents?|docs?)\b", text, re.I)
        )
    ):
        return frozenset({"email"})
    if recent_family == ("email",) and (
        re.search(r"\b(?:file|attachment|attached|document)\b", text, re.I)
        and re.search(r"\b(?:newest|latest|that|this|one|it)\b", text, re.I)
        and re.search(r"\b(?:what|read|say|says|summari[sz]e|mention)\b", text, re.I)
    ):
        return frozenset({"email"})
    if recent_family == ("email",) and re.fullmatch(
        _REQUEST_PREFIX + r"open\s+(?:(?:the\s+)?attachment(?:\s+too)?|"
        r"(?:the\s+)?[A-Za-z0-9][A-Za-z0-9 ._'’-]{1,100}(?:\s+one)?)"
        r"[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"email"})
    if recent_family == ("sessions",) and re.fullmatch(
        _REQUEST_PREFIX + r"(?:now\s+)?(?:just\s+)?(?:the\s+)?(?:important\s+ones?|"
        r"which\s+model\s+is\s+it\s+on|(?:keep\s+it\s+but\s+)?mark\s+it\s+important)"
        r"[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"sessions"})
    if recent_family == ("cookbook_admin",) and (
        re.search(r"\b(?:any\s+of\s+them|those)\b", text, re.I)
        and re.search(r"\b(?:qwen|served|endpoint|where|host)\b", text, re.I)
    ):
        return frozenset({"cookbook_admin"})
    if recent_family in {("cookbook_admin",), ("sessions",)} and re.search(
        r"\b(?:which|wich)\s+one\b[^?!.]{0,100}(?:"
        r"\btouch(?:ed)?\b[^?!.]{0,60}\b(?:recent(?:ly)?|latest|last)\b|"
        r"\b(?:recent(?:ly)?|latest|last)\b[^?!.]{0,60}\btouch(?:ed)?\b)",
        text,
        re.I,
    ):
        return frozenset({"sessions"})
    if recent_family == ("cookbook_admin",) and (
        re.search(r"\b(?:what|which|show|list)\b", text, re.I)
        and re.search(r"\btools?\b", text, re.I)
        and re.search(r"\b(?:one|server|mcp|filesystem|it|that)\b", text, re.I)
    ):
        return frozenset({"cookbook_admin"})
    if recent_family == ("notes",) and re.search(
        r"\b(?:check|tick|mark)\s+(?:off\s+)?(?:the\s+)?[^.!?]{1,100}"
        r"(?:line|item|box)\b|\b(?:check|tick)\s+off\b",
        text,
        re.I,
    ):
        return frozenset({"notes"})
    if recent_family == ("email",) and re.fullmatch(
        _REQUEST_PREFIX + r"(?:what(?:['’]?s|s|\s+is)\s+left|which\s+(?:ones?|messages?))"
        r"\s+(?:are\s+)?(?:flagged|suspicious|spam)[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"email"})
    if recent_family == ("email",) and (
        re.fullmatch(
            _REQUEST_PREFIX + r"which\s+ones?\s+(?:are\s+)?waiting\s+on\s+(?:me|us)"
            r"[?!.]*",
            text,
            re.I,
        )
        or re.fullmatch(
            _REQUEST_PREFIX + r"let(?:['’]?s|s|\s+us)\s+(?:review|open|read|check)\s+"
            r"[A-Za-z][A-Za-z .'-]{0,80}(?:['’]s)?[.!?]*",
            text,
            re.I,
        )
    ):
        return frozenset({"email"})
    if recent_family == ("cookbook_admin",) and re.search(
        r"\b(?:which|what)\s+events?\b[^.!?]{0,100}\b"
        r"(?:each|every|that|it|one|webhook)\b[^.!?]{0,100}\b(?:listen|trigger)",
        text,
        re.I,
    ):
        return frozenset({"cookbook_admin"})
    if recent_family == ("cookbook_admin",) and re.fullmatch(
        _REQUEST_PREFIX + r"is\s+(?:one|any)\s+of\s+(?:them|those)\s+for\s+"
        r"[^?!.]{2,100}[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"cookbook_admin"})
    if recent_family == ("cookbook_admin",) and re.search(
        r"\b(?:downloads?|models?)\b", text, re.I,
    ) and re.search(
        r"\b(?:stuck|errored?|on\s+disk|cached|already\s+have|any\s+of\s+(?:em|them))\b",
        text,
        re.I,
    ):
        return frozenset({"cookbook_admin"})
    if recent_family == ("calendar",) and (
        re.fullmatch(
            _REQUEST_PREFIX + r"which\s+(?:day|date|week)\s+(?:is\s+)?"
            r"(?:the\s+)?(?:heaviest|busiest|lightest|quietest|most\s+busy)[?!.]*",
            text,
            re.I,
        )
        or re.fullmatch(
            _REQUEST_PREFIX + r"(?:just\s+)?(?:show|list|give)\s+(?:me\s+)?"
            r"(?:the\s+)?(?:day|week|month)\s+(?:of|around|starting)\s+"
            r"(?:the\s+)?\d{1,2}(?:st|nd|rd|th)?[?!.]*",
            text,
            re.I,
        )
        or re.fullmatch(
            _REQUEST_PREFIX + r"(?:and\s+)?(?:today|tomor{1,2}ow|next\s+(?:week|month))"
            r"(?:\s+then)?[?!.]*",
            text,
            re.I,
        )
        or re.fullmatch(
            _REQUEST_PREFIX + r"(?:k|ok(?:ay)?|cool)?[,]?\s*"
            r"(?:what(?:['’]?s|s|\s+is)\s+the\s+next\s+(?:thing|event)|"
            r"where\s+is\s+(?:that|this|the)\s+one)\b[^.!?]*[.!?]*",
            text,
            re.I,
        )
        or re.fullmatch(
            _REQUEST_PREFIX + r"anything\s+in\s+(?:the\s+)?(?:first|second|third|last)\s+"
            r"(?:day|week|month)\s+of\s+(?:it|that|the\s+month)[?!.]*",
            text,
            re.I,
        )
        or re.fullmatch(
            _REQUEST_PREFIX + r"is\s+(?:the\s+)?\d{1,2}(?:st|nd|rd|th)\s+"
            r"(?:clear|free|open|busy)[?!.]*",
            text,
            re.I,
        )
    ):
        return frozenset({"calendar"})
    if (
        recent_family and recent_family[0] in {"contacts", "email"}
        and re.search(
            r"\b(?:check|look|search|see)\b[^?!.]{0,80}\b(?:saved\s+under|"
            r"spelling|variant|maiden\s+name|mistake)\b",
            text,
            re.I,
        )
    ):
        return frozenset({"contacts"})
    if (
        recent_family and recent_family[0] in {"contacts", "email"}
        and re.search(
            r"\b(?:did\s+i\s+(?:(?:ever|actually)\s+)*(?:send|email|mail)\s+"
            r"(?:them|him|her|that\s+(?:person|contact))|is\s+(?:this|that)\s+"
            r"(?:the\s+)?same\b[^?!.]{0,80}\bi\s+(?:emailed|mailed|messaged))\b",
            text,
            re.I,
        )
    ):
        return frozenset({"email"})
    if recent_family == ("search_browser",) and (
        re.search(
            r"\b(?:today|tomor{1,2}ow|this\s+(?:week|month)|right\s+now)\b",
            text,
            re.I,
        )
        and re.search(
            r"\b(?:anything\s+else|weather|forecast|allerg(?:y|ies|ic)|"
            r"pollen|air\s+quality|bad|good|safe)\b",
            text,
            re.I,
        )
    ):
        return frozenset({"search_browser"})
    if (
        recent_family == ("skills",)
        and re.search(r"\b(?:it|that|this|the\s+skill)\b", text, re.I)
        and re.search(r"\b(?:reference|mention|say|include|cover|contain|describe)\b", text, re.I)
    ):
        # Content words such as "email" describe the loaded skill here; they
        # are not a request to switch to the Email product family.
        return frozenset({"skills"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"open(?:\s+up)?\s+(?:my\s+|the\s+)?e-?mail"
        r"(?:\s+(?:panel|sidebar|tab|view))?[.!?]*",
        text,
        re.I,
    ):
        return frozenset({"ui"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:swap|switch|change|move)\s+(?:over\s+)?to\s+"
        r"(?:my\s+|the\s+)?(?:calendar|documents?|gallery|e-?mail|inbox|notes?|skills?)"
        r"\s+(?:panel|sidebar|tab|view)[.!?]*",
        text,
        re.I,
    ):
        return frozenset({"ui"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:gimme|give\s+me|show\s+me|list)?\s*"
        r"(?:the\s+)?(?:latest\s+|current\s+|recent\s+)?headlines?\s+"
        r"(?:from|in|about)\s+[^.!?\n]{2,120}(?:,\s*(?:short|brief|concise))?[.!?]*",
        text,
        re.I,
    ):
        return frozenset({"search_browser"})
    selected_operation = selected_tools_for_request(text)
    if selected_operation:
        # A complete operation is stronger evidence than a warm prior family.
        # Resolve its owning families before referential-history inheritance;
        # otherwise a prior HF search can erase a local-cache comparison, or
        # a calendar data family can erase an explicit panel-view operation.
        selected_families = frozenset().union(
            *(_families_for_tool(tool) for tool in selected_operation)
        )
        if selected_families:
            return selected_families
    if recently_read_gallery(history) and re.fullmatch(
        _REQUEST_PREFIX + r"upscale\s+(?:that|this|the)?\s*"
        r"(?:(?:first|second|last)\s+)?(?:one|image|photo|picture)"
        r"(?:\s+by)?\s+(?:2x|two\s+times?)[.!?]*",
        text,
        re.I,
    ):
        return frozenset({"image_editing"})
    if (
        re.search(r"\bupscale\b", text, re.I)
        and re.search(r"\b(?:that|this|the)\s+(?:first\s+)?(?:image|one)\b", text, re.I)
        and recently_read_gallery(history)
    ):
        return frozenset({"image_editing"})
    gallery_read = required_read_operation_for_request(text, history)
    if (
        gallery_read is not None
        and canonical_tool(gallery_read.tool) == "app_api"
        and str(gallery_read.args.get("path") or "").split("?", 1)[0]
        == "/api/gallery/library"
    ):
        # A verification re-list may mention the prior "upscaled" result.
        # The current operation is still the owner-scoped gallery GET; do not
        # let that descriptive adjective inherit the previous edit family.
        return frozenset({"cookbook_admin"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:anything|what(?:['’]?s|\s+is))\s+"
        r"(?:important|new|happening|going\s+on)\s+(?:in\s+)?"
        r"(?:ai|artificial\s+intelligence)\s+(?:today|right\s+now)[?!.]*",
        text, re.I,
    ):
        return frozenset({"search_browser"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"what(?:['’]?s|\s+is)\s+(?:new|happening|going\s+on)\s+"
        r"in\s+(?:ai|artificial\s+intelligence)(?:\s+(?:this|past)\s+week)?[?!.]*",
        text, re.I,
    ):
        return frozenset({"search_browser"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:gimme|get|give\s+me|show\s+me|list)\s+(?:the\s+)?"
        r"(?:latest\s+|current\s+)?headlines?\s+(?:from|in|about)\s+"
        r"[^?!.]{2,120}(?:,\s*(?:short|brief|concise))?[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"search_browser"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"open(?:\s+up)?\s+(?:the\s+)?theme\s+settings"
        r"(?:\s+for\s+me)?[.!?]*",
        text,
        re.I,
    ):
        # Theme settings is a local UI surface. The generic word “settings”
        # must not inject every Cookbook administration tool.
        return frozenset({"ui"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"open(?:\s+up)?\s+(?:my\s+|the\s+)?e-?mail"
        r"(?:\s+(?:panel|sidebar|tab|view))?[.!?]*",
        text,
        re.I,
    ):
        return frozenset({"ui"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"(?:swap|switch|flip|change)\s+(?:over\s+)?to\s+"
        r"(?:my\s+|the\s+)?(?:calendar|documents?|docs?|gallery|images?|e-?mail|"
        r"inbox|notes?|memor(?:y|ies)|skills?|settings|cookbook)\s+"
        r"(?:panel|sidebar|tab|view)[.!?]*",
        text,
        re.I,
    ):
        return frozenset({"ui"})
    if (
        recent_family == ("ui",)
        and re.fullmatch(
            _REQUEST_PREFIX + r"(?:flip|switch|change|swap|set|move|put)\s+(?:it|that|this)\s+"
            r"(?:over\s+|back\s+)?to\s+(?:the\s+)?(?:day|week|month|agenda)"
            r"(?:\s+view)?[.!?]*",
            text,
            re.I,
        )
    ):
        return frozenset({"ui"})
    explicit_ui_panel = bool(
        re.search(
            r"\b(?:pop\s+)?(?:open|opne)\b[^.!?\n]{0,100}\b"
            r"(?:panel|sidebar|tab|view)\b",
            text,
            re.I,
        )
        or re.search(
            r"\bshow\b[^.!?\n]{0,100}\b(?:in|on)\s+(?:the\s+)?"
            r"(?:panel|sidebar|tab|view)\b",
            text,
            re.I,
        )
    )
    if explicit_ui_panel and not re.search(r"\bresearch\b", text, re.I):
        # Explicit surface navigation owns the turn even when the named
        # surface is also a data family (Email, Notes, Skills, and so on).
        if recent_family and re.search(
            r"\b(?:read|list|repeat|give)\b[^.!?\n]{0,140}\b"
            r"(?:those|them|same|again)\b",
            text,
            re.I,
        ):
            return frozenset({"ui", recent_family[0]})
        return frozenset({"ui"})
    if _has_cookbook_server_reference(text) and re.search(
        r"\b(?:status|check|names?|brief|concise|configured|available|current)\b",
        text,
        re.I,
    ):
        return frozenset({"cookbook_admin"})
    if re.fullmatch(
        _REQUEST_PREFIX + r"what\s+documents?\s+do\s+(?:i|we)\s+have\s+saved[?!.]*",
        text,
        re.I,
    ):
        return frozenset({"documents"})
    if (
        not re.search(r"(?:^|\s)/workspace/", text, re.I)
        and
        re.search(
            r"\b[A-Za-z0-9_.-]+\.(?:txt|md|markdown|json|jsonl|csv|tsv|ya?ml|toml|"
            r"ini|cfg|conf|log|py|js|ts|tsx|jsx|html?|css|sh|sql|xml)\b",
            text,
            re.I,
        )
        and re.search(
            r"\b(?:exists?|lines?|read|show|check|chek|append|edit|write|save|remove|delete)\b",
            text,
            re.I,
        )
    ):
        # A filename is workspace data, even when its stem is a product name
        # such as notes.txt or calendar.json.
        families = {"shell_files"}
        if concrete_urls:
            families.add("search_browser")
        elif (
            re.search(r"\barxiv\b", text, re.I)
            and re.search(
                r"\b(?:fetch|retrieve|get|download|search|find|read|inspect|prepare|digest|identify|recover)\b",
                text,
                re.I,
            )
        ):
            # Creating a local artifact does not replace the explicitly named
            # external source needed to populate it.
            families.add("search_browser")
        elif (
            re.search(r"\bgithub\b", text, re.I)
            and re.search(r"\b(?:repositor(?:y|ies)|repos?|contributors?|commits?|pushed_at)\b", text, re.I)
        ):
            families.add("search_browser")
        return frozenset(families)
    if re.match(
        r"^\s*what(?:['’]?s|\s+is)\s+happening\s+(?:in|with|around)\b"
        r"[^?!.]{1,180}\b(?:lately|recently|right\s+now)\b",
        text,
        re.I,
    ) and not any(
        re.search(_FAMILY_WORDS[family], text, re.I)
        for family in {"calendar", "notes", "tasks", "skills", "memory", "documents", "email"}
    ):
        return frozenset({"search_browser"})
    if recent_family == ("search_browser",) and re.match(
        r"^\s*(?:(?:tell|give)\s+me\s+)?more\s+(?:on|about)\b", text, re.I,
    ):
        return frozenset({"search_browser"})
    if recent_family == ("search_browser",) and re.fullmatch(
        _REQUEST_PREFIX + r"(?:great[,!]?\s+)?(?:open|read|fetch|visit|check)\s+(?:up\s+)?"
        r"(?:one\s+of\s+)?(?:the\s+)?sources?(?:\s+(?:you|u)\s+(?:used|found|gave))?"
        r"[.!?]*",
        text, re.I,
    ):
        return frozenset({"search_browser"})
    if (
        re.search(r"\b(?:verify|double[- ]?check|confirm)\b", text, re.I)
        and re.search(r"\b(?:reliable|direct|original|official)\s+source\b", text, re.I)
    ):
        return frozenset({"search_browser"})
    if (
        recent_family == ("search_browser",)
        and re.search(r"\b(?:confirm|confrim|verify|check)\b", text, re.I)
        and re.search(r"\b(?:original|source|official)\s+(?:page|site|source)\b", text, re.I)
        and re.search(r"\b(?:that|this|one\s+of\s+(?:those|them|these))\b", text, re.I)
    ):
        return frozenset({"search_browser"})
    if recent_family == ("search_browser",) and re.fullmatch(
        _REQUEST_PREFIX + r"(?:pull|get|read|check)\s+.{1,160}\b"
        r"(?:off|from)\s+(?:that|this|the)\s+(?:link|page|result)[.!?]*",
        text,
        re.I,
    ):
        return frozenset({"search_browser"})
    if recent_family == ("shell_files",) and re.search(
        r"\bwho\s+am\s+i\s+logged\s+in\s+as\b|"
        r"\bhow\s+long\s+(?:has|is)\s+(?:it|the\s+(?:system|machine|server))\s+been\s+up\b|"
        r"\buptime\b",
        text,
        re.I,
    ):
        return frozenset({"shell_files"})
    if (
        recent_family == ("search_browser",)
        and re.search(
            r"\b(?:updates?|latest|newest|recent|last\s+(?:hour|day|week|month))\b",
            text,
            re.I,
        )
        and not any(re.search(pattern, text, re.I) for pattern in _FAMILY_WORDS.values())
    ):
        return frozenset({"search_browser"})
    if (
        recent_family == ("memory",)
        and re.search(r"\bany\s+of\s+(?:em|them|those)\b", text, re.I)
    ):
        return frozenset({"memory"})
    if recent_family == ("notes",) and (
        re.search(r"\b(?:checklist|list)\s+item\b[^.;\n]{0,100}\b(?:under|in|to)\s+(?:it|that|this)\b", text, re.I)
        or re.search(r"\b(?:put|add|change|update|include)\b[^.;\n]{0,120}\b(?:note\s+)?title\b", text, re.I)
    ):
        return frozenset({"notes"})
    if re.match(r"^\s*" + _REQUEST_PREFIX + r"note\s+down\b", text, re.I):
        # “Note down …” is an explicit request to persist a note. The source
        # may come from another family, but the requested side effect is Notes.
        return frozenset({"notes"})
    if re.match(
        r"^\s*" + _REQUEST_PREFIX
        + r"(?:jot|write|save|put)\s+(?:that|this|it)\b[^.!?\n]{0,160}\b"
          r"(?:in|into|to|as)\s+(?:a\s+)?(?:quick\s+)?notes?\b",
        text,
        re.I,
    ):
        # A referential save changes the destination family even when the
        # source came from Web, Email, or another private-data manager.
        return frozenset({"notes"})
    if re.search(
        r"\bopen\s+(?:it|that|this)\b[^.;\n]{0,100}\b(?:document\s+)?editor\b",
        text,
        re.I,
    ):
        return frozenset({"documents", "ui"})
    if re.search(
        r"\bopen(?:\s+up)?\s+(?:my\s+|the\s+)?notes(?:\s+(?:panel|sidebar|tab))?\b"
        r"[^.;\n]{0,100}\b(?:and|then)\s+(?:make|create|add|write)\b"
        r"[^.;\n]{0,100}\bnotes?\b",
        text,
        re.I,
    ):
        return frozenset({"notes", "ui"})
    if (
        recent_family == ("skills",)
        and re.search(
            r"\b(?:(?:does?|is|are)\s+(?:one|any)\s+of|any\s+of)\s+"
            r"(?:em|them|those|these)\b"
            r"[^?!.]{0,120}\b(?:cover|for|about|handle|support)",
            text,
            re.I,
        )
    ):
        return frozenset({"skills"})
    if (
        (_PANEL_POP_NAVIGATION.search(text) or re.search(
            r"\bopen(?:\s+up)?\s+(?:the\s+)?(?:calendar|schedule|documents?|docs?|"
            r"gallery|images?|emails?|inbox|notes?|memor(?:y|ies)|skills?|settings|cookbook)\s+"
            r"(?:panel|sidebar|tab|view)\b",
            text,
            re.I,
        ))
        and recent_family
        and re.search(
            r"\b(?:read|show|list|repeat|give)\b[^.;\n]{0,140}"
            r"\b(?:those|them|it|that|same|again)\b",
            text,
            re.I,
        )
    ):
        # A compound follow-up can request both a fresh readback and panel
        # navigation. Preserve the successfully executed data family instead
        # of allowing the navigation clause to consume the whole turn.
        return frozenset({"ui", recent_family[0]})
    if re.search(
        r"\bopen(?:\s+up)?\s+(?:the\s+)?(?:calendar|schedule|documents?|gallery|images?|"
        r"emails?|inbox|notes?|memor(?:y|ies)|skills?|settings|cookbook)\s+"
        r"(?:panel|sidebar|tab|view)\b",
        text,
        re.I,
    ):
        # The explicitly named local UI surface owns trailing rationale such
        # as "so I can browse them"; that verb is not browser authorization.
        return frozenset({"ui"})
    if (recently_executed_families(history, maximum=1) == ("notes",)
            and re.search(r"\b(?:add|append|put)\s+(?:a\s+)?line\b", text, re.I)
            and re.search(r"\b(?:at|to)\s+(?:the\s+)?(?:end|bottom)\b", text, re.I)):
        return frozenset({"notes"})
    if re.match(
        r"^\s*(?:(?:can|could|would)\s+(?:you|u)\s+)?look\s*up\b[^\n]{0,300}"
        r"\b(?:online|web|website|official\s+(?:site|docs?|source))\b",
        text,
        re.I,
    ):
        return frozenset({"search_browser"})
    if re.match(r"^\s*(?:quick(?:ly)?\s+)?web\s+search\b", text, re.I):
        return frozenset({"search_browser"})
    if re.match(r"^\s*search\s*:\s*\S", text, re.I):
        return frozenset({"search_browser"})
    if re.search(
        r"\b(?:search\s+(?:my|our|the)\s+skills?\s+for|"
        r"find\s+(?:me\s+)?(?:whatever|the|a)\s+skills?\s+(?:that\s+)?(?:covers?|for))\b",
        text,
        re.I,
    ):
        return frozenset({"skills"})
    if re.match(
        r"^\s*" + _REQUEST_PREFIX + r"(?:small|quick|brief)\s+(?:deep\s+)?research\s+run\s+(?:on|about)\b",
        text,
        re.I,
    ):
        return frozenset({"research"})
    if re.match(
        r"^\s*" + _REQUEST_PREFIX + r"(?:dig\s+deeper|deep\s+dive|look\s+into)\b",
        text,
        re.I,
    ):
        return frozenset({"research"})
    if (
        re.search(r"\b(?:grab|find|get|collect)\b[^.;\n]{0,100}\bsources?\b", text, re.I)
        and re.search(
            r"\b(?:stick|drop|put|save)\b[\s\S]{0,180}\b(?:into|in|as)\s+"
            r"(?:an?\s+|my\s+)?(?:new\s+)?notes?\b",
            text,
            re.I,
        )
    ):
        return frozenset({"search_browser", "notes"})
    if (
        re.search(r"\b(?:make|create|write)\b[^.;\n]{0,100}\bnotes?\b", text, re.I)
        and re.search(r"\b(?:lists?|include|copy|use)\b[^.;\n]{0,120}\bcalendar\b", text, re.I)
    ):
        return frozenset({"notes", "calendar"})
    if re.search(
        r"\b(?:stick|drop|put|save)\b[\s\S]{0,180}\b(?:into|in|as)\s+"
        r"(?:an?\s+|my\s+)?(?:new\s+)?notes?\b",
        text,
        re.I,
    ):
        return frozenset({"notes"})
    if (
        re.match(r"^\s*" + _REQUEST_PREFIX + r"(?:find|look\s*up|search)\b", text, re.I)
        and re.search(r"\bofficial\b[^.;\n]{0,100}\b(?:source|link|url|page|site)\b", text, re.I)
    ):
        return frozenset({"search_browser"})
    if re.search(
        r"\banything\s+(?:scheduled\s+)?on\s+(?:my|our|the)\s+calendar\b",
        text,
        re.I,
    ):
        return frozenset({"calendar"})
    if re.match(
        r"^\s*(?:give|read|show|list)\s+(?:me\s+)?[^?!.]{0,120}"
        r"\b(?:events?|appointments?|meetings?)\b[^?!.]{0,100}\bcalendar\b",
        text,
        re.I,
    ):
        return frozenset({"calendar"})
    if re.search(
        r"\b(?:is|are)\s+there\b[^?!.]{0,100}\bcalendar\b",
        text,
        re.I,
    ):
        return frozenset({"calendar"})
    if re.search(
        r"^\s*(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:also\s+)?"
        r"check\s+what\s+(?:i|we)\s+have\s+on\s+"
        r"(?:today|tomor{1,2}ow|tmrw|mon(?:day)?|tue(?:s|sday)?|wed(?:s|nesday)?|"
        r"thu(?:rs|rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)\b",
        text,
        re.I,
    ):
        return frozenset({"calendar"})
    if (_EXACT_READ_REPEAT.fullmatch(text) or re.search(
        r"\bre-?run\b[^.;\n]{0,100}\b(?:same|again|check)\b", text, re.I
    )):
        # Repeating an explicitly requested operation retains its family even
        # when the prior execution failed. The current "re-run" is fresh user
        # authority; requiring prior success made recovery impossible.
        for index in range(len(history) - 1, -1, -1):
            row = history[index]
            role = row.get("role") if isinstance(row, dict) else getattr(row, "role", "")
            content = row.get("content", "") if isinstance(row, dict) else getattr(row, "content", "")
            if role == "user" and content != text:
                inherited = requested_capabilities(content, history[:index])
                if inherited and "unknown" not in inherited:
                    return inherited
                break
    if _SHELL_COMMAND_SEQUENCE.search(text) or _EXPLICIT_INLINE_SHELL_COMMAND.search(text):
        # Two explicit shell operations, including a system-file path, are a
        # stronger signal than conversational wording such as "quick check".
        # An explicitly introduced inline command is equally unambiguous even
        # when a follow-up does not repeat the word "bash".
        return frozenset({"shell_files"})
    if re.search(
        r"\b(?:do|calculate|compute|solve|work)\b[^?!.]{0,100}"
        r"\b(?:with|using)\s+python\b",
        text,
        re.I,
    ):
        # An explicit request to use Python is execution authority even when
        # it follows a conversational question ("what's 9x7, do it with
        # python") rather than starting the sentence.
        return frozenset({"shell_files"})
    if re.match(
        r"^\s*(?:in|inside|under)\s+(?:an?\s+|the\s+)?"
        r"(?:temp(?:orary)?|workspace|working)\s+(?:dir(?:ectory)?|folder)\b",
        text,
        re.I,
    ) and re.search(
        r"\b(?:make|create|write)\b[^?!.]{0,180}\b(?:files?|folders?)\b",
        text,
        re.I,
    ):
        # A workspace-scoped file operation remains a shell/files action when
        # the location phrase precedes the imperative verb.
        return frozenset({"shell_files"})
    if re.search(
        r"\b(?:use|run)\b[^.;\n]{0,40}\b(?:b?ssh|bashh)\b|"
        r"\b(?:b?ssh|bashh)\b[^.;\n]{0,40}\b(?:run|pwd)\b",
        text,
        re.I,
    ):
        return frozenset({"shell_files"})
    if (
        re.match(r"^\s*" + _REQUEST_PREFIX + r"(?:run|execute|use)\b", text, re.I)
        and re.search(r"```\s*(?:sh|bash)\b", text, re.I)
    ):
        return frozenset({"shell_files"})
    if re.search(
        r"\bhow\s+much\b[^?.;\n]{0,40}\b(?:disk|storage)\b[^?.;\n]{0,40}\b(?:free|available|left)\b|"
        r"\bhow\s+much\b[^?.;\n]{0,40}\b(?:free|available)\b[^?.;\n]{0,40}\b(?:disk|storage)\b",
        text,
        re.I,
    ):
        return frozenset({"shell_files"})
    if re.search(r"\b(?:pull\s+up|show|list)\b", text, re.I) and re.search(
        r"\bdocumets?\b", text, re.I
    ):
        return frozenset({"documents"})
    # A complete top-level navigation request is a UI operation even when the
    # panel name is also a data family (for example documents or calendar).
    # Keep this strict/full-string so "open document <title>" remains a data
    # lookup rather than being stolen by UI routing.
    if (_PANEL_NAVIGATION.fullmatch(text) or _PANEL_POP_NAVIGATION.search(text)
            or _THEME_CHANGE.search(text) or _PANEL_CONTROLS_NAVIGATION.search(text)
            or _CONTEXTUAL_UI_VIEW_CHANGE.search(text)):
        return frozenset({"ui"})
    if re.match(
        r"^\s*" + _REQUEST_PREFIX + r"open(?:\s+up)?\s+(?:the\s+)?(?:email|mail|inbox)\s+panel\b",
        text,
        re.I,
    ):
        return frozenset({"ui"})
    if re.search(r"\b(?:primary|inbox|mailbox)\b", text, re.I) and re.search(
        r"\b(?:read|open|show|find|search|reply|draft)\b", text, re.I
    ):
        # In "read that Priya note in the Primary inbox", note describes the
        # message; the explicitly named container determines the product.
        return frozenset({"email"})
    if not active_document and re.search(
        r"\b(?:reply|response)\s+draft\b|\bdraft(?:ing)?\s+(?:a\s+)?reply\b|"
        r"\bput\s+together\s+(?:a\s+)?(?:polite\s+)?reply\b",
        text,
        re.I,
    ):
        return frozenset({"email"})
    if re.search(r"\btool\s+toggles?\b", text, re.I) and re.search(
        r"\b(?:show|list|check|eyeball|inspect|view|what)\b", text, re.I
    ):
        return frozenset({"cookbook_admin"})
    if recently_executed_families(history, maximum=1) == ("email",) and re.search(
        r"\b(?:reply|response)\s+draft\b|\bdraft(?:ing)?\s+(?:a\s+)?reply\b",
        text,
        re.I,
    ):
        return frozenset({"email"})
    explicit_families = {
        family for family, pattern in _FAMILY_WORDS.items()
        if re.search(pattern, text, re.I)
    }
    recent = recent_family
    if (
        recent == ("search_browser",)
        and not (explicit_families - {"search_browser"})
        and not re.search(r"\b(?:no\s+tools?|without\s+tools?|do\s+not\s+(?:search|browse|use\s+tools?))\b", text, re.I)
        and (
            re.match(r"^\s*(?:and\s+)?(?:i\s+mean|what\s+about|how\s+about)\b", text, re.I)
            or re.search(r"\b(?:vs\.?|versus)\b", text, re.I)
            or re.search(r"\bif\s+i\s+only\s+care\s+about\b", text, re.I)
        )
    ):
        # A natural narrowing of the immediately preceding public-web topic
        # stays inside that investigation even when it omits words such as
        # search, web, source, or a demonstrative pronoun.
        return frozenset({"search_browser"})
    if (
        recent == ("research",)
        and (
            re.search(r"\b(?:report|research|findings?|finished|done|newest|latest|whichever)\b", text, re.I)
            or (_REFERENCE.search(text) and re.search(
                r"\b(?:find|open|read|show|check|where|when|status)\b", text, re.I,
            ))
        )
    ):
        return frozenset({"research"})
    if (
        recent == ("memory",)
        and re.search(r"\bnote\s+that\s+i\s+(?:like|prefer|want|need)\b", text, re.I)
    ):
        return frozenset({"memory"})
    if recent == ("email",) and re.search(
        r"\b(?:more\s+detail(?:ed|s)?\s+about|who(?:['’]?s|\s+is)\s+it\s+from|"
        r"who\s+sent\s+it|what(?:['’]?s|\s+is)\s+the\s+sender)\b",
        text,
        re.I,
    ):
        return frozenset({"email"})
    if (
        recent == ("skills",)
        and _REFERENCE.search(text)
        and re.search(r"\b(?:publish(?:ed)?|rename|named|call\s+it)\b", text, re.I)
    ):
        # A generated skill name may itself contain words such as "web";
        # the referenced skill lifecycle operation owns the turn.
        return frozenset({"skills"})
    if (
        recent
        and recent[0] in {"notes", "memory", "research", "tasks", "skills", "documents", "email"}
        and _CONTEXTUAL_COLLECTION_FILTER.search(text)
    ):
        # "in there" binds descriptive words (for example "python") to the
        # active collection rather than switching to another product family.
        return frozenset({recent[0]})
    if (recent == ("search_browser",)
            and not (explicit_families - {"search_browser", "cookbook_admin", "documents"})
            and re.search(r"\b(?:page|source|link|result)\b", text, re.I)
            and re.search(r"\b(?:fetch|open|pull\s+up|check|confirm|verify|read)\b", text, re.I)):
        # A referenced web result owns incidental nouns such as "model" in
        # an explicit fetch/check follow-up.
        return frozenset({"search_browser"})
    if (recent == ("search_browser",)
            and not (explicit_families - {"search_browser", "cookbook_admin"})
            and re.search(r"\b(?:source|link|url)\b", text, re.I)
            and re.search(r"\b(?:that|this|it|again|same)\b", text, re.I)
            and re.search(r"\b(?:give|show|send|drop|repeat|list)\b", text, re.I)):
        # Re-rendering a source established by the previous web result does
        # not become a shell/file request merely because the user says
        # "on its own line".
        return frozenset({"search_browser"})
    if recent == ("search_browser",) and _CONTEXTUAL_WEB_EVIDENCE.search(text):
        return frozenset({"search_browser"})
    if recent == ("search_browser",) and re.match(
        r"^\s*(?:now\s+)?(?:look|search|check|find)\s+for\b[^.;\n]{0,240}"
        r"\b(?:latest|current|newest|recent|version|changed|changes?)\b",
        text,
        re.I,
    ):
        # Continue an established public-web investigation when the user asks
        # for fresher adjacent evidence without repeating the word "web".
        return frozenset({"search_browser"})
    if not explicit_families and recent:
        if (_REFERENCE.search(text) and _REFERENTIAL_FOLLOWUP_QUESTION.search(text)
                and not _PERSONAL_CALENDAR_SCHEDULE.search(text)):
            return frozenset({recent[0]})
        if (
            recent[0] in {"notes", "memory", "research", "tasks", "skills", "documents", "email"}
            and _CONTEXTUAL_COLLECTION_FILTER.search(text)
        ):
            return frozenset({recent[0]})
        if (
            recent[0] in {"notes", "memory", "research", "tasks", "skills", "documents", "email"}
            and _CONTEXTUAL_ITEM_DETAIL.search(text)
        ):
            return frozenset({recent[0]})
        if _REFERENCE.search(text) and _has_action_signal(text):
            return frozenset({recent[0]})
        if recent[0] == "calendar" and (
            _CONTEXTUAL_STATE_LOOKUP.search(text)
            or _CONTEXTUAL_CALENDAR_ACTION.search(text)
            or _CONTEXTUAL_CALENDAR_LOOKUP.search(text)
        ):
            return frozenset({"calendar"})
        if _CONTEXTUAL_RESULT_LOOKUP.search(text):
            return frozenset({recent[0]})
        if (recent[0] == "email"
                and re.search(r"\b(?:accou?nt|accnt|invoice|sender|from\s+them|latest\s+one)\b", text, re.I)):
            return frozenset({"email"})
    if (recent and explicit_families and recent[0] in explicit_families
            and _CONTEXTUAL_RESULT_LOOKUP.search(text)):
        return frozenset(explicit_families)
    if (recent and explicit_families == {recent[0]} and _REFERENCE.search(text)
            and not _PERSONAL_CALENDAR_SCHEDULE.search(text)
            and _REFERENTIAL_FOLLOWUP_QUESTION.search(text)):
        return frozenset(explicit_families)
    if (
        _REFERENCE.search(text)
        and _REFERENTIAL_TOOL_CONTINUATION.fullmatch(text)
        and not any(re.search(pattern, text, re.I) for pattern in _FAMILY_WORDS.values())
    ):
        # Resolve a pure "show/open/read it" against actual successful tool
        # execution before the exact-read repeater scans intervening prose.
        # Product nouns explicitly present in this turn still win.
        recent = recently_executed_families(history, maximum=1)
        if recent:
            return frozenset({recent[0]})
    operation = required_read_operation_for_request(text, history)
    if operation is not None:
        return _families_for_tool(operation.tool)
    selected = selected_tools_for_request(text)
    if selected:
        # A complete exact operation owns its trailing result-presentation
        # clause (for example, search chat history and show the match). Do not
        # split that clause into a second family and fail the contract closed.
        return frozenset().union(*(_families_for_tool(tool) for tool in selected))
    # Classify independent requests separately so the first routing match
    # cannot hide a second capability. Keep noun conjunctions intact.
    clauses = re.split(r"[;\n]|[.!?]\s+|\b(?:and|then)\s+(?=" + _ACTION_REQUEST + r")",
                       text, flags=re.I)
    families = set().union(*(_clause_capabilities(clause) for clause in clauses))
    if re.search(r"(?:file://)?/tmp_workspace(?:/|\b)", text, re.I):
        families.add("shell_files")
    if (
        re.search(r"\bgithub\b", text, re.I)
        and re.search(r"\b(?:repositor(?:y|ies)|repos?|contributors?|commits?|pushed_at)\b", text, re.I)
    ):
        families.add("search_browser")
    if (
        re.search(r"\barxiv\b", text, re.I)
        and re.search(
            r"\b(?:fetch|retrieve|get|download|search|find|read|inspect|prepare|digest|identify|recover)\b",
            text,
            re.I,
        )
    ):
        # arXiv is an external paper source. Long artifact requests often put
        # the retrieval verb and ``arXiv`` in different list items, so routing
        # each clause independently can otherwise leave only local file tools.
        families.add("search_browser")
    if (recent == ("email",)
            and re.search(r"\b(?:from\s+them|latest\s+one|that\s+(?:message|email))\b", text, re.I)):
        families.add("email")
        if "shell_files" not in explicit_families:
            families.discard("shell_files")
    # Markdown prompts commonly put a requested URL on its own bullet after
    # ``Read ... at:``. Clause splitting keeps routing bounded, but must not
    # detach that URL from the explicit retrieval action and leave an artifact
    # task with only filesystem tools.
    if _EXPLICIT_URL_RETRIEVAL.search(text):
        families.add("search_browser")
    if (
        _NAMED_EXTERNAL_DOCUMENT_RETRIEVAL.search(text)
        and not _LOCAL_PDF_REFERENCE.search(text)
    ):
        # A named paper plus table/figure references is an external retrieval
        # request even when the user did not already know its URL. Without
        # this capability, clean-v3 freezes a local-file-only contract and the
        # model cannot discover the source through the native web tools.
        families.add("search_browser")
    if requires_external_web_verification(text):
        families.add("search_browser")
    # A successful tool result is the strongest antecedent for compact
    # referential continuations such as "get its transcript" or "from that
    # same PDF, extract ...". Keep this to the single most-recent successful
    # family. The one ambiguous collision we override is "search those ...
    # models", where the generic models noun otherwise steals an HF-search
    # refinement into Cookbook administration.
    if _REFERENCE.search(text) and _REFERENTIAL_TOOL_CONTINUATION.fullmatch(text):
        recent = recently_executed_families(history, maximum=1)
        if recent and not families:
            families.add(recent[0])
        elif (recent == ("search_browser",) and families == {"cookbook_admin"}
              and re.match(r"^\s*" + _REQUEST_PREFIX + r"(?:search|find)\s+(?:those|them|these)\b", text, re.I)):
            families = {"search_browser"}
    if not families and (recall := _warm_recall_parts(text)):
        target = recall[0].lower()
        family = {
            "email": "email", "emails": "email", "inbox": "email",
            "note": "notes", "notes": "notes", "task": "tasks", "tasks": "tasks",
            "skill": "skills", "skills": "skills", "memory": "memory", "memories": "memory",
            "document": "documents", "documents": "documents", "doc": "documents", "docs": "documents",
            "web": "search_browser", "browser": "search_browser", "cookbook": "cookbook_admin",
            "file": "shell_files", "files": "shell_files", "shell": "shell_files",
            "calendar": "calendar",
        }[target]
        if family in recently_executed_families(history):
            families.add(family)
    if not families and (recall := _warm_recall_parts(text, with_followup=True)):
        target = recall[0].lower()
        family = {
            "email": "email", "emails": "email", "inbox": "email",
            "note": "notes", "notes": "notes", "task": "tasks", "tasks": "tasks",
            "skill": "skills", "skills": "skills", "memory": "memory", "memories": "memory",
            "document": "documents", "documents": "documents", "doc": "documents", "docs": "documents",
            "web": "search_browser", "browser": "search_browser", "cookbook": "cookbook_admin",
            "file": "shell_files", "files": "shell_files", "shell": "shell_files",
            "calendar": "calendar",
        }[target]
        if family in recently_executed_families(history):
            families.add(family)
    if active_document and not families and _has_action_signal(text) and _REFERENCE.search(text):
        families.add("documents")
    if not families and _has_action_signal(text) and _REFERENCE.search(text):
        # Typed successful execution is a stronger antecedent than a noun in
        # an intervening prose-only user turn. After listing Skills, for
        # example, "which one is about email?" followed by "show it" still
        # refers to the selected skill, not to the Email product family.
        recent = recently_executed_families(history, maximum=1)
        if recent:
            families.add(recent[0])
        else:
            rows = list(history)
            for index in range(len(rows) - 1, -1, -1):
                row = rows[index]
                role = row.get("role") if isinstance(row, dict) else getattr(row, "role", "")
                content = row.get("content", "") if isinstance(row, dict) else getattr(row, "content", "")
                if role == "user" and content != message:
                    families.update(requested_capabilities(content, rows[:index]))
                    break
    if not families and (_has_action_signal(text) or _LOOKUP.search(text) or _CONVERSATIONAL_FOLLOWUP.search(text)):
        rows = list(history)
        for index in range(len(rows) - 1, -1, -1):
            row = rows[index]
            role = row.get("role") if isinstance(row, dict) else getattr(row, "role", "")
            content = row.get("content", "") if isinstance(row, dict) else getattr(row, "content", "")
            if role != "user" or content == message:
                continue
            inherited = set(requested_capabilities(content, rows[:index]))
            inherited.discard("unknown")
            if inherited:
                families.update(inherited)
            break
    if not families and (_has_action_signal(text) or _LOOKUP.search(text)):
        families.add("unknown")
    return frozenset(families)


@dataclass(frozen=True)
class TurnContract:
    capabilities: frozenset[str]
    required: frozenset[str]
    offered: frozenset[str]
    executable: frozenset[str]
    unavailable: frozenset[str]
    schema_json: tuple[str, ...]
    required_read_operation: RequiredReadOperation | None = None
    active_capabilities: frozenset[str] = frozenset()
    selection_mode: str = "routed"
    routing_experiment: str = "baseline"

    def __post_init__(self):
        if not self.required <= self.offered <= self.executable:
            raise ValueError("Turn contract violates required <= offered <= executable")
        names = {json.loads(s)["function"]["name"] for s in self.schema_json}
        if names != set(self.offered):
            raise ValueError("Turn contract schema inventory differs from offered tools")
        operation = self.required_read_operation
        if operation is not None:
            if not isinstance(operation, RequiredReadOperation):
                raise TypeError("required_read_operation must be a RequiredReadOperation")
            if (canonical_tool(operation.tool) not in self.unavailable
                    and operation.tool not in self.required & self.offered & self.executable):
                raise ValueError("Required read operation must be available or explicitly unavailable")

    def permits(self, name: str) -> bool:
        return canonical_tool(name) in {canonical_tool(n) for n in self.offered}

    def schemas(self) -> list[dict]:
        # Return copies: compact/full model formatting must not mutate the contract.
        return [json.loads(value) for value in self.schema_json]

    def audit(self) -> dict:
        result = {"capabilities": sorted(self.capabilities), "required": sorted(self.required),
                "offered": sorted(self.offered), "executable": sorted(self.executable),
                "unavailable": sorted(self.unavailable),
                "active_capabilities": sorted(self.active_capabilities)}
        if self.required_read_operation is not None:
            result["required_read_operation"] = self.required_read_operation.audit()
        result["selection_mode"] = self.selection_mode
        result["routing_experiment"] = self.routing_experiment
        return result


def resolve_full_inventory_contract(*, schemas: Iterable[dict], policy: ToolPolicy) -> TurnContract:
    """Experimental trained inventory: permissions filter offers; model chooses actions."""
    families = frozenset({"calendar", "notes", "tasks", "skills", "memory", "documents",
                          "email", "search_browser", "shell_files", "cookbook_admin",
                          "image_editing", "image_generation"})
    # ``ui_control`` is the executable bridge for explicit client-interface
    # requests (for example, opening the gallery).  It is not one of the ten
    # persisted-data families, but omitting it here makes the full-inventory
    # contract claim that a real backend capability does not exist.
    trained = frozenset().union(*(FAMILY_TOOLS[f] for f in families)) | {
        # Research jobs and saved reports are available to the interactive
        # model; request selection and backend permissions still apply.
        "ask_user", "update_plan", "ui_control", "manage_research", "trigger_research", "extract_text",
        # Session tools overlap Cookbook administration except pipeline. It is
        # nevertheless part of the trained/runtime contract and must survive
        # the full-inventory intersection for exact pipeline requests.
        "pipeline", "edit_image",
    }
    denied = {canonical_tool(n) for n in policy.all_disabled_names()}
    inventory = {s["function"]["name"]: s for s in schemas if isinstance(s.get("function"), dict)}
    executable = frozenset(n for n in inventory if canonical_tool(n) not in denied
                           and not policy.blocks(n))
    offered = frozenset(n for n in executable if canonical_tool(n) in trained)
    offered = frozenset(n for n in offered if n.startswith("mcp__") or "mcp__email__" + n not in offered)
    return TurnContract(families, frozenset(), offered, executable, frozenset(),
                        tuple(json.dumps(inventory[n], sort_keys=True) for n in sorted(offered)),
                        selection_mode="full_compact_experiment")


def resolve_turn_contract(*, capabilities: Iterable[str], schemas: Iterable[dict], policy: ToolPolicy,
                          required_tools: Iterable[str] = (),
                          required_capabilities: Iterable[str] | None = None,
                          selected_tools: Iterable[str] | None = None,
                          always_available_tools: Iterable[str] = (),
                          warm_tools: Iterable[str] = (),
                          required_read_operation: RequiredReadOperation | None = None,
                          message: str | None = None, history: Iterable = ()) -> TurnContract:
    """Resolve selection without substituting tools for missing requirements.

    Callers can require action-specific tools (e.g. list_models for a catalog
    request). Such requirements never expand the selected capabilities. If a
    requirement is missing or denied, unavailable names explain the failure
    and no tools are offered; integration must surface that failure.
    selected_tools optionally narrows the family inventory. warm_tools restores
    exact tools successfully used earlier in this conversation, but never grants
    permission because the result is still intersected with executable.
    always_available_tools keeps tools for a visible, owner-checked surface
    available through exact request narrowing, subject to the same policy.
    New callers may supply an exact required_read_operation, or message/history
    to resolve one. Omitting both preserves the existing family-only API.
    """
    families = frozenset(capabilities)
    required_families = families if required_capabilities is None else frozenset(required_capabilities)
    operation = required_read_operation
    if operation is None and message is not None:
        operation = required_read_operation_for_request(message, history)
    if operation is not None and not isinstance(operation, RequiredReadOperation):
        raise TypeError("required_read_operation must be a RequiredReadOperation")
    inventory = {s["function"]["name"]: s for s in schemas if isinstance(s.get("function"), dict) and s["function"].get("name")}
    # Email aliases are one permission identity in both directions, including
    # when only the legacy schema is present in the inventory.
    denied = {canonical_tool(n) for n in policy.all_disabled_names()}
    executable = frozenset(n for n in inventory
                           if not policy.blocks(n) and canonical_tool(n) not in denied
                           and not (policy.disable_mcp and n.startswith("mcp__")))
    selected = set().union(*(FAMILY_TOOLS.get(f, frozenset()) for f in families))
    if selected_tools is not None:
        requested = {canonical_tool(n) for n in selected_tools}
        if not families:
            # An exact operation selected by the request classifier is already
            # a sufficient capability declaration. Do not erase it merely
            # because the broader lexical family classifier was conservative.
            selected = requested
        else:
            selected.intersection_update(requested)
    elif operation is not None:
        # A server-sealed safe read is an operation, not merely a family hint.
        # Offer exactly that reader so the model cannot drift to a sibling
        # search/mutation tool after the router has already resolved intent.
        selected.intersection_update({canonical_tool(operation.tool)})
    elif not families:
        selected.update(CONTRACT_CORE_TOOLS)
    if (
        message is not None
        and selected_tools is not None
        and selected & {"web_search", "web_fetch"}
    ):
        # Browser is not core. It is a bounded recovery capability for a web
        # turn when static search/fetch cannot read the named site.
        selected.add("private_browser")
    # Email headers discover records; they are not a complete reading surface.
    # Keep the read-only continuation available after exact search narrowing.
    # The executable intersection below still enforces disabled tools/accounts.
    if 'search_emails' in selected:
        selected.update({'read_email', 'download_attachment', 'list_email_accounts'})
    selected.update(canonical_tool(n) for n in always_available_tools)
    selected.update(canonical_tool(n) for n in warm_tools if str(n or "").strip())
    # Controls are neutral; enabling Web is permission, never a requested family.
    if selected:
        selected.update({"ask_user", "update_plan"})
    offered = frozenset(n for n in executable if canonical_tool(n) in selected)
    # Prefer the real MCP email schema over its legacy alias when both exist.
    offered = frozenset(n for n in offered if n.startswith("mcp__") or "mcp__email__" + n not in offered)
    required_names = ({_REQUIRED_TOOLS[f] for f in required_families if f in _REQUIRED_TOOLS}
                      | {canonical_tool(n) for n in required_tools})
    if operation is not None:
        required_names.add(canonical_tool(operation.tool))
    offered_canonical = {canonical_tool(n) for n in offered}
    unavailable = frozenset((required_names - offered_canonical)
                            | {f"capability:{f}" for f in required_families
                               if f not in FAMILY_TOOLS or (
                                   f not in _REQUIRED_TOOLS
                                   and not FAMILY_TOOLS[f] & offered_canonical)})
    if unavailable:
        offered = frozenset()
        if operation is not None:
            # The contract as a whole cannot run; retain the sealed operation
            # but explicitly mark it unavailable along with the blocking tools.
            unavailable |= {canonical_tool(operation.tool)}
    elif operation is not None:
        operation = replace(operation, tool=next(n for n in offered
                            if canonical_tool(n) == canonical_tool(operation.tool)))
    required = frozenset(n for n in offered if canonical_tool(n) in required_names)
    return TurnContract(families, required, offered, executable, unavailable,
                        tuple(json.dumps(inventory[n], sort_keys=True) for n in sorted(offered)),
                        operation, required_families)


_ACTIVE_CONTRACT: ContextVar[TurnContract | None] = ContextVar("turn_contract", default=None)


def active_turn_contract() -> TurnContract | None:
    return _ACTIVE_CONTRACT.get()


@contextmanager
def bind_turn_contract(contract: TurnContract | None):
    token = _ACTIVE_CONTRACT.set(contract)
    try:
        yield
    finally:
        _ACTIVE_CONTRACT.reset(token)


def with_turn_contract(func):
    """Bind an async generator's turn_contract argument until it is closed.

    Resolve positional and keyword arguments alike. Explicitly close the inner
    generator while still bound so its cleanup observes the same authority.
    Like other context-bound streams, iteration and closing share one task.
    """
    call_signature = signature(func)

    @wraps(func)
    async def wrapped(*args, **kwargs):
        arguments = call_signature.bind(*args, **kwargs)
        arguments.apply_defaults()
        with bind_turn_contract(arguments.arguments.get("turn_contract")):
            async with aclosing(func(*args, **kwargs)) as stream:
                async for chunk in stream:
                    yield chunk

    return wrapped
