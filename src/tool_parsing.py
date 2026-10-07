"""
tool_parsing.py

Regex-based parsing of tool invocations from LLM response text.
Supports fenced code blocks, [TOOL_CALL] blocks, and XML-style <invoke> blocks.
"""

import ast
import bisect
import json
import logging
import re
import shlex
import warnings
from typing import Iterable, List, Optional, Tuple

from src.tool_types import ToolBlock, TOOL_TAGS
from src.tool_security import BUILTIN_EMAIL_TOOLS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Pattern 1: ```bash ... ``` fenced code blocks. The tag may be followed by a
# newline (classic form) or by inline JSON args on the same line
# (```list_email_accounts {}). The same-line part is captured separately
# (group 2) and judged by _fenced_tool_call below — the regex alone only
# requires it to start with { or [; anything else after the tag is a Markdown
# info string (```python title="example.py") and the fence never matches.
# (?![\w-]) keeps the alternation from prefix-matching longer fence tags:
# without it, ```python3 would match as tool "python" with content "3\n..."
# and execute as code.
_TOOL_BLOCK_RE = re.compile(
    r"```(" + "|".join(TOOL_TAGS) + r")(?![\w-])"
    r"[ \t]*([{\[][^\n]*?)?[ \t]*(?=\r?\n|```)\r?\n?([\s\S]*?)```",
    re.IGNORECASE,
)


def _tool_block_re(additional_tool_names: Optional[Iterable[str]] = None):
    if not additional_tool_names:
        return _TOOL_BLOCK_RE
    extra = {
        name
        for raw_name in additional_tool_names
        if isinstance(raw_name, str)
        and (name := raw_name.strip())
        and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", name)
    }
    if not extra:
        return _TOOL_BLOCK_RE
    # ``json`` is only executable here as an envelope naming one of the
    # request-declared tools. It remains inert for ordinary agent turns.
    tags = sorted(
        set(TOOL_TAGS) | extra | {"json", "function_name"},
        key=lambda value: (-len(value), value),
    )
    return re.compile(
        r"```(" + "|".join(re.escape(tag) for tag in tags) + r")(?![\w-])"
        r"[ \t]*([{\[][^\n]*?)?[ \t]*(?=\r?\n|```)\r?\n?([\s\S]*?)```",
        re.IGNORECASE,
    )

# Tags whose fenced content is raw code, not JSON args. Same-line text after
# these tags is Markdown fence metadata on a real language (```bash {title=
# "setup"}), never inline tool args — only the classic tag-then-newline form
# executes for them.
_CODE_FENCE_TAGS = frozenset({"bash", "python"})


def _fenced_tool_call(m) -> Optional[Tuple[str, str]]:
    """Classify a Pattern-1 fence match: (tag, content) when it is an
    executable tool call, None when the fence must stay display text.

    Shared by parse_tool_blocks and strip_tool_blocks so the execute and
    display decisions can never disagree: a fence that doesn't execute is
    never stripped, and vice versa.

    Same-line text after the tag only counts as inline tool args when the
    tag's tool takes JSON args (not a code tag) AND the text is valid
    standalone JSON. ```bash {title="setup"} and ```python {"x": 1} are
    fence attributes on real languages, and {title="x"} on any tag is
    metadata, not arguments — all of those stay visible and inert.
    """
    tag = m.group(1).lower()
    inline = (m.group(2) or "").strip()
    body = (m.group(3) or "").strip()
    if not inline:
        return tag, body
    if tag in _CODE_FENCE_TAGS:
        return None
    # Inline args may continue onto following lines (a JSON object opened on
    # the tag line); the combined text must parse as JSON or nothing runs.
    content = f"{inline}\n{body}" if body else inline
    try:
        json.loads(content)
    except (ValueError, TypeError):
        return None
    return tag, content


def _strip_executed_fence(m) -> str:
    """re.sub callback: remove only fences that parse as tool calls."""
    return "" if _fenced_tool_call(m) is not None else m.group(0)

# Pattern 2: [TOOL_CALL] ... [/TOOL_CALL] blocks (some models use this format)
# Matches: {tool => "shell", args => {--command "ls -la"}} etc.
_TOOL_CALL_RE = re.compile(
    r"\[TOOL_CALL\]\s*\{([\s\S]*?)\}\s*\[/TOOL_CALL\]",
    re.IGNORECASE,
)
# Same delimiters as _TOOL_CALL_RE, split so they can be driven by
# _iter_delimited (a forward-only scan). The closer is `}\s*[/TOOL_CALL]`, so a
# present-but-unmatched `[/TOOL_CALL]` with no inner `}` ahead simply ends the
# scan instead of triggering re.finditer's O(n^2) rescan. See _iter_delimited.
_TOOL_CALL_OPEN_RE = re.compile(r"\[TOOL_CALL\]\s*\{", re.IGNORECASE)
_TOOL_CALL_CLOSE_RE = re.compile(r"\}\s*\[/TOOL_CALL\]", re.IGNORECASE)

# Pattern 3: XML-style tool calls (minimax, some other models)
# <minimax:tool_call><invoke name="bash"><parameter name="command">...</parameter></invoke></minimax:tool_call>
# Also handles: <tool_call><invoke ...>, <function_call><invoke ...>, plain <invoke ...>
_XML_TOOL_CALL_RE = re.compile(
    r"<(?:[\w]+:)?(?:tool_call|function_call)>\s*([\s\S]*?)</(?:[\w]+:)?(?:tool_call|function_call)>",
    re.IGNORECASE,
)
_XML_OPEN_TOOL_CALL_RE = re.compile(
    r"<(?:[\w]+:)?(?:tool_call|function_call)>\s*([\s\S]*)\Z",
    re.IGNORECASE,
)
# _XML_TOOL_CALL_RE's delimiters, split for _iter_delimited's forward-only scan.
_XML_TOOL_CALL_OPEN_RE = re.compile(
    r"<(?:[\w]+:)?(?:tool_call|function_call)>\s*",
    re.IGNORECASE,
)
_XML_TOOL_CALL_CLOSE_RE = re.compile(
    r"</(?:[\w]+:)?(?:tool_call|function_call)>",
    re.IGNORECASE,
)
_XML_INVOKE_RE = re.compile(
    r'<invoke\s+name=["\'](\w+)["\']>\s*([\s\S]*?)</invoke>',
    re.IGNORECASE,
)
_XML_PARAM_RE = re.compile(
    r'<parameter\s+name=["\'](\w+)["\']>([\s\S]*?)</parameter>',
    re.IGNORECASE,
)
_XML_DIRECT_TOOL_RE = re.compile(
    r"<\s*([A-Za-z_][\w-]*)\s*>([\s\S]*?)</\s*\1\s*>",
    re.IGNORECASE,
)
# Forward-only delimiters for the lazy XML patterns above, so untrusted "many
# openers, no closer" model output can't drive finditer's O(n^2) lazy rescan
# (CodeQL py/polynomial-redos). Consumed by _iter_xml_invoke / _iter_xml_direct.
_XML_INVOKE_OPEN_RE = re.compile(r'<invoke\s+name=["\'](\w+)["\']>\s*', re.IGNORECASE)
_XML_INVOKE_CLOSE_RE = re.compile(r'</invoke>', re.IGNORECASE)
_XML_DIRECT_OPEN_RE = re.compile(r"<\s*([A-Za-z_][\w-]*)\s*>", re.IGNORECASE)
# Split <parameter ...>...</parameter> delimiters: the parameter scan inside an
# invoke body is forward-only too, so a closed invoke stuffed with unclosed
# parameter openers can't drive finditer's O(n^2) rescan. See _iter_named_blocks.
_XML_PARAM_OPEN_RE = re.compile(r'<parameter\s+name=["\'](\w+)["\']>', re.IGNORECASE)
_XML_PARAM_CLOSE_RE = re.compile(r'</parameter>', re.IGNORECASE)
# Closer tokens (any tag name) for the backref scanners, pre-indexed by name so a
# flood of distinct unclosed tag names stays near-linear. See _iter_backref_blocks.
_XML_DIRECT_CLOSE_ANY_RE = re.compile(r"</\s*([A-Za-z_][\w-]*)\s*>", re.IGNORECASE)
# `args => { ... }` opener (its closer is the last `}`, found with rfind) and the
# `<tag>` opener for tool_code XML params — both split out of greedy/backref
# patterns that finditer would otherwise rescan from every opener. See
# _parse_tool_call_block / _parse_tool_code_block.
_ARGS_BRACE_OPEN_RE = re.compile(r'args\s*(?:=>|:|=)\s*\{')
_TOOL_CODE_PARAM_OPEN_RE = re.compile(r"<(\w+)>")
_TOOL_CODE_PARAM_CLOSE_ANY_RE = re.compile(r"</(\w+)>")

# Pattern 3b: StepFun Step-3.x native tool-call tokens. The tokenizer defines:
#   <｜tool▁calls▁begin｜> ... <｜tool▁calls▁end｜>
#   <｜tool▁call▁begin｜>tool_name<｜tool▁sep｜>{...}<｜tool▁call▁end｜>
# These can leak as text through llama.cpp/Ollama-style endpoints when the
# engine does not return structured OpenAI tool_calls.
_STEPFUN_CALL_BEGIN = "<｜tool▁call▁begin｜>"
_STEPFUN_CALL_SEP = "<｜tool▁sep｜>"
_STEPFUN_CALL_END = "<｜tool▁call▁end｜>"
_STEPFUN_CALLS_BEGIN = "<｜tool▁calls▁begin｜>"
_STEPFUN_CALLS_END = "<｜tool▁calls▁end｜>"

# Pattern 4: <tool_code> blocks (MiniMax-M2.5 style)
# {tool => 'tool_name', args => '<param>value</param>'}
_TOOL_CODE_RE = re.compile(
    r"<tool_code>\s*\{([\s\S]*?)\}\s*</tool_code>",
    re.IGNORECASE,
)
# _TOOL_CODE_RE's delimiters, split for _iter_delimited's forward-only scan.
_TOOL_CODE_OPEN_RE = re.compile(r"<tool_code>\s*\{", re.IGNORECASE)
_TOOL_CODE_CLOSE_RE = re.compile(r"\}\s*</tool_code>", re.IGNORECASE)

# Pattern 4b: Gemma-style <|tool_call|> call:tool_name{args} <tool_call|>
_GEMMA_TOOL_CALL_OPEN_RE = re.compile(
    r"<\|?tool_call\|?>\s*call:([\w\d_-]+)\s*\{",
    re.IGNORECASE,
)
_GEMMA_TOOL_CALL_CLOSE_RE = re.compile(
    r"\}\s*<\|?tool_call\|?>",
    re.IGNORECASE,
)

# Native Qwen markup shares the same non-nesting delimiter grammar as the
# XML helpers. Literal closers keep whitespace and opener floods linear;
# values are stripped by the caller, as in the original regex path.
_QWEN_FUNCTION_OPEN_RE = re.compile(r"<function=([A-Za-z_][\w:.-]*)>\s*")
_QWEN_FUNCTION_CLOSE_RE = re.compile(r"</function>")
_QWEN_PARAMETER_OPEN_RE = re.compile(r"<parameter=([A-Za-z_]\w*)>\s*")
_QWEN_PARAMETER_CLOSE_RE = re.compile(r"</parameter>")
_QWEN_PYTHON_ARG_KEY_RE = re.compile(r"[A-Za-z_]\w*")
_QWEN_PYTHON_ARG_VALUE_RE = re.compile(r"\s*=\s*(['\"].*?['\"]|[^,]+)")
_ANGLE_TAG_OPEN_RE = re.compile(r"<")
_NONEMPTY_ANGLE_TAG_OPEN_RE = re.compile(r"<(?=[^>])")
_ANGLE_TAG_CLOSE_RE = re.compile(r">")
_EMAIL_LOCAL_RE = re.compile(r"[\w.+-]+")
_EMAIL_ADDRESS_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
_ASCII_EMAIL_LOCAL_RE = re.compile(r"[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+")
_ASCII_EMAIL_ADDRESS_RE = re.compile(
    r"[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)

# Pattern 4c: Open-function wrapper emitted by some local MLX/Exo models.
# Example:
#   <function_model>
#   <function_call>web_search</function_call>
#   <parameters>{"query":"Sweden news today"}</parameters>
#   </function_model>
_FUNCTION_MODEL_OPEN_RE = re.compile(r"<function_model>\s*", re.IGNORECASE)
_FUNCTION_MODEL_CLOSE_RE = re.compile(r"</function_model>", re.IGNORECASE)
_FUNCTION_MODEL_NAME_RE = re.compile(
    r"<function_call>\s*([A-Za-z_][\w-]*)\s*</function_call>",
    re.IGNORECASE,
)
_FUNCTION_MODEL_PARAMS_OPEN_RE = re.compile(r"<parameters>\s*", re.IGNORECASE)
_FUNCTION_MODEL_PARAMS_CLOSE_RE = re.compile(r"</parameters>", re.IGNORECASE)
_QWEN_ROLE_MARKER_RE = re.compile(r"</?\|(?:assistant|assistan|user|system|tool)\|>?|</\|end\|>?", re.IGNORECASE)
# At least one pipe is required around `end`. Both pipes used to be optional
# (`\|?end\|?`), which also matched a bare `end` on its own line and deleted it
# from ordinary prose and from Ruby/Lua/shell snippets that close blocks with
# one; see #5547. `|end`, `end|`, `|end|` and `/|end|` still strip as before.
_QWEN_BARE_MARKER_RE = re.compile(
    r"(?:^|[\t\r\n ])(?:/?\|end\||\|end|end\|)(?=[\t\r\n ]|$)|"
    r"(?:^|[\r\n])[ \t]*assistan(?:t)?[ \t]*(?=[\r\n]|$)",
    re.IGNORECASE,
)
_QWEN_OPEN_TOOLS_RE = re.compile(
    r"<\|open\|>\s*tools\b[\s\S]*?(?:<\|close\|>\s*message\s*<\|sep\|>|$)",
    re.IGNORECASE,
)


# Pattern 5: DeepSeek DSML markup leaking into content. When deepseek
# models can't emit structured tool_calls (e.g. we sent no tool schemas
# that round, or the API didn't parse them), they fall back to raw
# markup using fullwidth-pipe delimiters:
#   <｜｜DSML｜｜tool_calls>
#     <｜｜DSML｜｜invoke name="web_search">
#       <｜｜DSML｜｜parameter name="query" string="true">QUERY</｜｜DSML｜｜parameter>
#     </｜｜DSML｜｜invoke>
#   </｜｜DSML｜｜tool_calls>
# We normalize it into the standard <invoke>/<parameter> form so the
# existing XML parser + stripper handle it (parse → execute; strip →
# never show the garbage to the user). The pipe run is tolerant of
# fullwidth (U+FF5C) and ascii '|' in any count.
_DSML_PIPES = r"[｜|]+"


def _contains_explicit_tool_markup(text: str) -> bool:
    """Return whether text contains a non-fenced, explicit call envelope.

    If a model mixes a Markdown example with a real call envelope, the
    envelope is authoritative. Executing the first fence and then skipping
    the explicit call both runs the wrong command and loses the requested one.
    The explicit parsers below still inspect the full text when fences are
    skipped, so real leaked markup inside a response remains recoverable.
    """
    return bool(re.search(
        r"(?:\[TOOL_CALL\]|<\s*(?:[\w]+:)?(?:tool_call|function_call|invoke|tool_code)\b|"
        r"<\|?tool_call\|?>|<｜tool▁call▁begin｜>|<function_model>)",
        str(text or ""),
        re.IGNORECASE,
    ))


def _normalize_dsml(text: str) -> str:
    if not isinstance(text, str):
        return ""
    if "DSML" not in text:
        return text
    t = text
    # Hosted DeepSeek variants use both ``tool_calls`` and the shorter
    # ``calls`` wrapper. Treat them identically; otherwise the inner invoke is
    # parsed but the outer DSML tags leak into the user-visible response.
    t = re.sub(rf"<\s*{_DSML_PIPES}\s*DSML\s*{_DSML_PIPES}\s*(?:tool_calls|calls)\s*>", "<tool_call>", t, flags=re.IGNORECASE)
    t = re.sub(rf"<\s*/\s*{_DSML_PIPES}\s*DSML\s*{_DSML_PIPES}\s*(?:tool_calls|calls)\s*>", "</tool_call>", t, flags=re.IGNORECASE)
    t = re.sub(rf"<\s*{_DSML_PIPES}\s*DSML\s*{_DSML_PIPES}\s*invoke\s+name=", "<invoke name=", t, flags=re.IGNORECASE)
    t = re.sub(rf"<\s*/\s*{_DSML_PIPES}\s*DSML\s*{_DSML_PIPES}\s*invoke\s*>", "</invoke>", t, flags=re.IGNORECASE)
    # parameter open tag — drop any extra attrs (e.g. string="true").
    t = re.sub(rf'<\s*{_DSML_PIPES}\s*DSML\s*{_DSML_PIPES}\s*parameter\s+name=(["\'][^"\']+["\'])[^>]*>',
               r"<parameter name=\1>", t, flags=re.IGNORECASE)
    t = re.sub(rf"<\s*/\s*{_DSML_PIPES}\s*DSML\s*{_DSML_PIPES}\s*parameter\s*>", "</parameter>", t, flags=re.IGNORECASE)
    return t

# Map model tool names to our tool types
_TOOL_NAME_MAP = {
    "shell": "bash",
    "bash": "bash",
    "terminal": "bash",
    "command": "bash",
    "execute": "bash",
    "run": "bash",
    "python": "python",
    "code": "python",
    "search": "web_search",
    "web_search": "web_search",
    "websearch": "web_search",
    "google_search": "web_search",
    "google_search_retrieval": "web_search",
    "google_search_grounding": "web_search",
    "web_fetch": "web_fetch",
    "webfetch": "web_fetch",
    "fetch_url": "web_fetch",
    "fetch": "web_fetch",
    "read": "read_file",
    "read_file": "read_file",
    "cat": "read_file",
    "list_files": "ls",
    "list_directory": "ls",
    "write": "write_file",
    "write_file": "write_file",
    "save": "write_file",
    "apply_patch": "apply_patch",
    "patch": "apply_patch",
    "todowrite": "todowrite",
    "todo_write": "todowrite",
    "todo_update": "todowrite",
    "document": "update_document",
    "update_document": "update_document",
    "create_document": "create_document",
    "edit": "edit_document",
    "edit_document": "edit_document",
    "search_chats": "search_chats",
    "search_conversations": "search_chats",
    "find_chat": "search_chats",
    "chat_with_model": "chat_with_model",
    "ask_model": "chat_with_model",
    "chat_model": "chat_with_model",
    "create_session": "create_session",
    "new_session": "create_session",
    "list_sessions": "list_sessions",
    "send_to_session": "send_to_session",
    "message_session": "send_to_session",
    "pipeline": "pipeline",
    "chain": "pipeline",
    "manage_session": "manage_session",
    "session_control": "manage_session",
    "manage_memory": "manage_memory",
    "memory": "manage_memory",
    "manage_tasks": "manage_tasks",
    "tasks": "manage_tasks",
    "schedule": "manage_tasks",
    "list_models": "list_models",
    "models": "list_models",
    "available_models": "list_models",
    "ui_control": "ui_control",
    "ui": "ui_control",
    "control": "ui_control",
    "api_call": "api_call",
    "api": "api_call",
    "integration": "api_call",
    "ask_teacher": "ask_teacher",
    "teacher": "ask_teacher",
    "manage_skills": "manage_skills",
    "skills": "manage_skills",
    "skill": "manage_skills",
    "suggest_document": "suggest_document",
    "suggest": "suggest_document",
    "review_document": "suggest_document",
    "manage_endpoints": "manage_endpoints",
    "endpoints": "manage_endpoints",
    "manage_mcp": "manage_mcp",
    "mcp_servers": "manage_mcp",
    "manage_webhooks": "manage_webhooks",
    "webhooks": "manage_webhooks",
    "manage_tokens": "manage_tokens",
    "tokens": "manage_tokens",
    "manage_documents": "manage_documents",
    "documents": "manage_documents",
    "manage_research": "manage_research",
    "list_research": "manage_research",
    "read_research": "manage_research",
    "open_research": "manage_research",
    "delete_research": "manage_research",
    "manage_settings": "manage_settings",
    "settings": "manage_settings",
    "preferences": "manage_settings",
    "manage_notes": "manage_notes",
    "notes": "manage_notes",
    "todo": "manage_notes",
    "todos": "manage_notes",
    "manage_bg_jobs": "manage_bg_jobs",
    "bg_jobs": "manage_bg_jobs",
    "background_jobs": "manage_bg_jobs",
}

_MISFENCED_WEB_TOOL_NAMES = {
    "web_search": "web_search",
    "websearch": "web_search",
    "google_search": "web_search",
    "google_search_retrieval": "web_search",
    "google_search_grounding": "web_search",
    "web_fetch": "web_fetch",
    "webfetch": "web_fetch",
    "fetch_url": "web_fetch",
}

# Media-capable local models often fall back from structured tool calls to a
# language fence, e.g. `````python\ninspect_media('/workspace/a.mp4')````` or
# `````bash\ninspect_media /workspace/a.mp4`````.  These are unambiguous calls,
# but treating them as Python/bash leaves the media evidence gate permanently
# unsatisfied.  Keep the rescue exact: one media command only, with literal
# arguments, never an arbitrary script.
_MISFENCED_MEDIA_TOOL_NAMES = {
    "inspect_media": "inspect_media",
    "extract_text": "extract_text",
    "transcribe_media": "transcribe_media",
    # A common local-model alias; Odysseus has no separate translate-media
    # tool, and inspection is the only safe semantic target.
    "translate_media": "inspect_media",
}
_MISFENCED_MEDIA_ALLOWED_KEYS = {
    "inspect_media": {
        "path", "file", "filename", "input", "start", "end", "duration",
        "frames", "frame_count", "sampling", "max_dimension", "query", "page", "pages",
        "timestamp", "output_path", "speed", "segments", "exports", "caption",
        "crop", "timestamp_path",
    },
    "extract_text": {"path", "file", "filename", "input", "mode", "include_layout", "min_confidence", "max_results"},
    "transcribe_media": {
        "path", "file", "filename", "input", "language", "force_language",
        "start", "end", "model", "output_path", "timestamp_precision",
        # Some local models describe the requested media operation rather
        # than using the native schema.  It is harmless metadata; the native
        # transcriber still receives the validated path/options.
        "transcription_type",
    },
}


def _parse_misfenced_media_lookup(content: str) -> Optional[ToolBlock]:
    """Recover one literal media-tool call from a python/bash fence.

    This intentionally does not execute or interpret general code.  It only
    accepts a single function call or a single shell-style command whose name
    is an explicit media tool (or its narrow alias), then serializes literal
    arguments for the normal tool validation/execution path.
    """
    stripped = str(content or "").strip()
    if not stripped:
        return None

    # Bash models sometimes prepend a harmless background marker.  Remove
    # only shebang/comment lines; any other extra statement remains rejected.
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    lines = [line for line in lines if not line.startswith("#!")]
    if len(lines) != 1:
        return None
    candidate = lines[0]

    try:
        module = _parse_python_like_content(candidate)
    except SyntaxError:
        module = None
    if module is not None and len(module.body) == 1 and isinstance(module.body[0], ast.Expr):
        call = module.body[0].value
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
            raw_name = call.func.id.lower()
            tool_type = _MISFENCED_MEDIA_TOOL_NAMES.get(raw_name)
            if tool_type and len(call.args) <= 1:
                args = {}
                if call.args:
                    try:
                        args["path"] = ast.literal_eval(call.args[0])
                    except (ValueError, SyntaxError, TypeError):
                        return None
                    if not isinstance(args["path"], str) or not args["path"].strip():
                        return None
                for keyword in call.keywords:
                    if keyword.arg is None:
                        return None
                    key = keyword.arg
                    if key not in _MISFENCED_MEDIA_ALLOWED_KEYS[tool_type]:
                        return None
                    try:
                        value = ast.literal_eval(keyword.value)
                    except (ValueError, SyntaxError, TypeError):
                        return None
                    normalized_key = {
                        "file": "path",
                        "filename": "path",
                        "input": "path",
                        # ``frame_count`` is a common textual spelling of the
                        # native ``frames`` field.  Normalize only this
                        # unambiguous scalar alias; conflicting duplicate
                        # fields remain invalid below.
                        "frame_count": "frames",
                    }.get(key, key)
                    if normalized_key in args:
                        return None
                    args[normalized_key] = value
                if args.get("path"):
                    return ToolBlock(tool_type, json.dumps(args, ensure_ascii=False))

    # Shell-style JSON fallback: exactly ``tool {object}``.  This is common
    # when a model knows the native JSON contract but emits it inside a bash
    # fence.  Decode one object only, validate every key, and send it through
    # the normal tool validator; never execute the surrounding shell.
    name, separator, tail = candidate.partition(" ")
    tool_type = _MISFENCED_MEDIA_TOOL_NAMES.get(name.lower()) if separator else None
    if tool_type and tail.lstrip().startswith("{"):
        try:
            arguments, consumed = json.JSONDecoder().raw_decode(tail.lstrip())
        except (TypeError, ValueError):
            arguments, consumed = None, 0
        remainder = tail.lstrip()[consumed:].strip() if consumed else tail
        if isinstance(arguments, dict) and not remainder:
            normalized = {}
            for key, value in arguments.items():
                if key not in _MISFENCED_MEDIA_ALLOWED_KEYS[tool_type]:
                    return None
                normalized["path" if key in {"file", "filename", "input"} else key] = value
            if isinstance(normalized.get("path"), str) and normalized["path"].strip():
                return ToolBlock(tool_type, json.dumps(normalized, ensure_ascii=False))

    # Shell-style fallback: exactly ``tool path`` (optionally quoted).  Keep
    # options out of this rescue; callers needing them can use JSON/function
    # syntax and the normal parser will retain the strict boundary.
    try:
        tokens = shlex.split(candidate)
    except ValueError:
        return None
    if len(tokens) == 2:
        tool_type = _MISFENCED_MEDIA_TOOL_NAMES.get(tokens[0].lower())
        if tool_type and tokens[1].strip() and not tokens[1].startswith("-"):
            return ToolBlock(tool_type, json.dumps({"path": tokens[1]}, ensure_ascii=False))
    return None

_RAW_WEB_JSON_TOOL_RE = re.compile(
    r"\b(?:web_search|websearch|google_search|google_search_retrieval|google_search_grounding)\b",
    re.IGNORECASE,
)
_RAW_WEB_JSON_ALLOWED_KEYS = {"query", "queries", "time_filter", "freshness", "max_pages"}

# Narrow rescue for models that ignore native tool calling and print the UI
# command as plain text. Keep this intentionally tiny: open-panel is a harmless
# frontend event, while broad plain-text parsing of shell/doc/email tools would
# be unsafe.
_PLAIN_UI_OPEN_PANEL_RE = re.compile(
    r"(?im)^\s*(?:`{1,3})?\s*ui_control\s+open_panel\s+"
    r"(documents?|library|gallery|images?|calendar|schedule|email|inbox|mail|sessions?|chats?|history|"
    r"notes?|brain|memor(?:y|ies)|skills?|settings|preferences|themes?|appearance|cookbook|models?)"
    r"((?:\s+(?:day|week|month|year|agenda)(?:\s+view)?(?:\s+\d{4}-\d{2}(?:-\d{2})?)?)?)"
    r"\s*(?:`{1,3})?\s*$"
)
_PLAIN_UI_CANDIDATE_RE = re.compile(r"ui_control", re.IGNORECASE)


def _iter_plain_ui_open_panel(text: str):
    """Yield line-anchored UI commands without retrying every blank line."""
    pos = 0
    while candidate := _PLAIN_UI_CANDIDATE_RE.search(text, pos):
        line_start = text.rfind("\n", 0, candidate.start()) + 1
        match = _PLAIN_UI_OPEN_PANEL_RE.match(text, line_start)
        if match is not None:
            yield match
            pos = match.end()
            continue
        newline = text.find("\n", candidate.end())
        pos = len(text) if newline < 0 else newline + 1


def _strip_plain_ui_open_panel(text: str) -> str:
    matches = list(_iter_plain_ui_open_panel(text))
    if not matches:
        return text
    out = []
    pos = 0
    for match in matches:
        out.append(text[pos:match.start()])
        pos = match.end()
    out.append(text[pos:])
    return "".join(out)


# ---------------------------------------------------------------------------
# Parsing functions
# ---------------------------------------------------------------------------

def _parse_python_like_content(content: str):
    """Parse fallback syntax without leaking invalid-escape warnings to traces."""

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        return ast.parse(content, mode="exec")

def _literal_string(value) -> Optional[str]:
    """Return a string from a small literal AST node, or None."""
    try:
        parsed = ast.literal_eval(value)
    except (ValueError, SyntaxError, TypeError):
        return None
    if isinstance(parsed, str):
        return parsed.strip()
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, str) and item.strip():
                return item.strip()
    return None


def _parse_misfenced_web_lookup(content: str) -> Optional[ToolBlock]:
    """Recover simple web_search/web_fetch calls wrapped in python/bash fences.

    Some local fenced-tool models write:

        ```python
        web_search("latest python release")
        ```

    That is an intended tool call, not Python code. Keep this intentionally
    narrow: only a single bare function call to a known web tool alias converts.
    """
    try:
        module = _parse_python_like_content(content.strip())
    except SyntaxError:
        return None
    if len(module.body) != 1 or not isinstance(module.body[0], ast.Expr):
        return None
    call = module.body[0].value
    if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
        return None

    mapped = _MISFENCED_WEB_TOOL_NAMES.get(call.func.id.lower())
    if mapped not in ("web_search", "web_fetch"):
        return None
    if len(call.args) > 1:
        return None

    args = {}
    if call.args:
        key = "url" if mapped == "web_fetch" else "query"
        value = _literal_string(call.args[0])
        if not value:
            return None
        args[key] = value

    allowed = {"query", "queries", "url", "time_filter", "freshness", "max_pages"}
    for keyword in call.keywords:
        if keyword.arg not in allowed:
            return None
        key = "query" if keyword.arg == "queries" else keyword.arg
        value = _literal_string(keyword.value)
        if value is not None:
            args[key] = value
            continue
        try:
            parsed = ast.literal_eval(keyword.value)
        except (ValueError, SyntaxError, TypeError):
            return None
        if key == "max_pages" and isinstance(parsed, int):
            args[key] = parsed
            continue
        return None

    if mapped == "web_search":
        query = args.get("query")
        if not query:
            return None
        payload = {"query": query}
        for key in ("time_filter", "freshness", "max_pages"):
            if key in args:
                payload[key] = args[key]
        if len(payload) == 1:
            return ToolBlock("web_search", query)
        return ToolBlock("web_search", json.dumps(payload))

    url = args.get("url")
    if not url:
        return None
    return ToolBlock("web_fetch", url)



def _parse_misfenced_read_file_lookup(content: str, *, allow_shell_style: bool = False) -> Optional[ToolBlock]:
    """Recover simple read_file calls wrapped in python/bash fences."""
    stripped = content.strip()
    if not stripped:
        return None

    try:
        module = _parse_python_like_content(stripped)
    except SyntaxError:
        module = None
    if module and len(module.body) == 1 and isinstance(module.body[0], ast.Expr):
        call = module.body[0].value
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
            if call.func.id.lower() != "read_file" or len(call.args) > 1:
                return None
            args = {}
            if call.args:
                path = _literal_string(call.args[0])
                if not path:
                    return None
                args["path"] = path
            allowed = {"path", "file", "file_path", "offset", "limit"}
            for keyword in call.keywords:
                if keyword.arg not in allowed:
                    return None
                key = "path" if keyword.arg in ("file", "file_path") else keyword.arg
                if key == "path":
                    path = _literal_string(keyword.value)
                    if not path:
                        return None
                    args["path"] = path
                    continue
                try:
                    value = ast.literal_eval(keyword.value)
                except (ValueError, SyntaxError, TypeError):
                    return None
                if not isinstance(value, int) or value < 0:
                    return None
                args[key] = value
            if not args.get("path"):
                return None
            from src.tool_schemas import function_call_to_tool_block
            return function_call_to_tool_block("read_file", json.dumps(args))

    if not allow_shell_style:
        return None
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if len(lines) != 1:
        return None
    match = re.fullmatch(r"read_file\s+(.+)", lines[0], re.IGNORECASE)
    if not match:
        return None
    path = match.group(1).strip()
    if not path:
        return None
    if path.startswith("{"):
        try:
            args = json.loads(path)
        except json.JSONDecodeError:
            return None
        if not isinstance(args, dict):
            return None
        normalized = {}
        raw_path = args.get("path") or args.get("file") or args.get("file_path")
        if isinstance(raw_path, str) and raw_path.strip():
            normalized["path"] = raw_path.strip()
        for key in ("offset", "limit"):
            value = args.get(key)
            if isinstance(value, int) and value >= 0:
                normalized[key] = value
        if not normalized.get("path"):
            return None
        from src.tool_schemas import function_call_to_tool_block
        return function_call_to_tool_block("read_file", json.dumps(normalized))
    if len(path) >= 2 and path[0] == path[-1] and path[0] in "'\"":
        path = path[1:-1].strip()
    if not path:
        return None
    return ToolBlock("read_file", path)


def _coerce_raw_web_query(value) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                return item.strip()
    return None


def _raw_web_json_to_tool_block(payload) -> Optional[ToolBlock]:
    if not isinstance(payload, dict):
        return None
    if set(payload) - _RAW_WEB_JSON_ALLOWED_KEYS:
        return None

    query = _coerce_raw_web_query(payload.get("query"))
    if not query:
        query = _coerce_raw_web_query(payload.get("queries"))
    if not query:
        return None

    content = {"query": query}
    for key in ("time_filter", "freshness"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip().lower() in ("day", "week", "month", "year"):
            content[key] = value.strip().lower()

    max_pages = payload.get("max_pages")
    if isinstance(max_pages, int) and 1 <= max_pages <= 10:
        content["max_pages"] = max_pages

    if len(content) == 1:
        return ToolBlock("web_search", query)
    return ToolBlock("web_search", json.dumps(content))


def _parse_raw_web_json_lookup(text: str) -> Optional[tuple[ToolBlock, tuple[int, int]]]:
    """Recover local text-model web_search calls emitted as prose + bare JSON.

    Some non-native tool models leak the intended call as:

        Need to do web_search for ...
        {"query": "...", "time_filter": "week"}

    Keep this narrower than fenced/tool markup: it only runs when a known web
    tool name appears shortly before a JSON object shaped like web_search args.
    """
    if not isinstance(text, str):
        return None

    decoder = json.JSONDecoder()
    for mention in _RAW_WEB_JSON_TOOL_RE.finditer(text):
        search_start = mention.end()
        search_end = min(len(text), search_start + 1200)
        for brace in re.finditer(r"\{", text[search_start:search_end]):
            start = search_start + brace.start()
            try:
                parsed, end = decoder.raw_decode(text[start:])
            except json.JSONDecodeError:
                continue
            block = _raw_web_json_to_tool_block(parsed)
            if block:
                return block, (start, start + end)
    return None


def _looks_like_openai_tool_call_blob(value) -> bool:
    """Return True for raw OpenAI-style tool-call JSON leaked as text."""
    if isinstance(value, list):
        return bool(value) and all(_looks_like_openai_tool_call_blob(item) for item in value)
    if not isinstance(value, dict):
        return False
    fn = value.get("function")
    if isinstance(fn, dict) and isinstance(fn.get("name"), str):
        return True
    return False


def _raw_openai_tool_call_to_block(value) -> Optional[ToolBlock]:
    if isinstance(value, list):
        for item in value:
            block = _raw_openai_tool_call_to_block(item)
            if block:
                return block
        return None
    if not isinstance(value, dict):
        return None
    fn = value.get("function")
    if not isinstance(fn, dict):
        return None
    name = str(fn.get("name") or "").strip()
    if not name:
        return None
    tool_type = _TOOL_NAME_MAP.get(name, name)
    raw_args = fn.get("arguments") or {}
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    except (json.JSONDecodeError, TypeError):
        args = {}
    if not isinstance(args, dict):
        args = {}
    # Common local-model typo seen in raw OpenAI JSON leaks.
    if "text" not in args and "tex" in args:
        args["text"] = args.get("tex")

    if tool_type.startswith("mcp__"):
        return ToolBlock(tool_type, json.dumps(args) if args else "{}")
    if name in BUILTIN_EMAIL_TOOLS:
        return ToolBlock(f"mcp__email__{name}", json.dumps(args) if args else "{}")
    if tool_type not in TOOL_TAGS:
        return None

    if tool_type == "bash":
        content = args.get("command", "")
    elif tool_type == "python":
        content = args.get("code", "")
    elif tool_type == "web_search":
        content = args.get("query", "")
        queries = args.get("queries")
        if not content and isinstance(queries, list) and queries:
            content = str(queries[0])
        elif not content and queries:
            content = str(queries)
        tf = args.get("time_filter")
        if content and isinstance(tf, str) and tf in ("day", "week", "month", "year"):
            content = json.dumps({"query": content, "time_filter": tf})
    elif tool_type == "web_fetch":
        content = args.get("url") or args.get("domain") or ""
    elif tool_type == "read_file":
        content = json.dumps(args) if (args.get("offset") or args.get("limit")) else args.get("path", "")
    elif tool_type in ("grep", "glob", "ls", "edit_file"):
        content = json.dumps(args) if args else "{}"
    elif tool_type == "write_file":
        # Keep raw OpenAI JSON on the canonical path so explicit empty content
        # remains distinguishable from a missing body.
        from src.tool_schemas import function_call_to_tool_block
        return function_call_to_tool_block(name, json.dumps(args))
    elif tool_type == "create_document":
        parts = [args.get("title", "Untitled")]
        if args.get("language"):
            parts.append(args["language"])
        parts.append(args.get("content", ""))
        content = "\n".join(parts)
    elif tool_type == "update_document":
        content = args.get("content", "")
    elif tool_type in ("edit_document", "suggest_document"):
        marker = "SUGGEST" if tool_type == "suggest_document" else "REPLACE"
        blocks = []
        for edit in args.get("suggestions" if tool_type == "suggest_document" else "edits", []) or []:
            if not isinstance(edit, dict):
                continue
            block = f'<<<FIND>>>\n{edit.get("find", "")}\n<<<{marker}>>>\n{edit.get("replace", "")}'
            if tool_type == "suggest_document":
                block += f'\n<<<REASON>>>\n{edit.get("reason", "")}'
            blocks.append(block + "\n<<<END>>>")
        content = "\n".join(blocks)
    elif tool_type == "search_chats":
        content = args.get("query", "")
    elif tool_type == "chat_with_model":
        content = args.get("model", "") + "\n" + args.get("message", "")
    elif tool_type == "create_session":
        content = args.get("name", "Untitled") + "\n" + args.get("model", "")
    elif tool_type == "list_sessions":
        content = args.get("filter", "")
    elif tool_type == "send_to_session":
        content = args.get("session_id", "") + "\n" + args.get("message", "")
    elif tool_type == "pipeline":
        content = json.dumps({"steps": args.get("steps", [])})
    elif tool_type == "manage_session":
        action = args.get("action", "")
        if action == "list":
            keyword = args.get("keyword", "") or args.get("value", "")
            content = "list" + (("\n" + keyword) if keyword and keyword.lower() != "current" else "")
        else:
            content = action + "\n" + args.get("session_id", "current")
            if args.get("value"):
                content += "\n" + args["value"]
    elif tool_type == "manage_memory":
        action = args.get("action", "")
        if action == "add":
            content = "add\n" + str(args.get("text", ""))
            if args.get("category"):
                content += "\n" + str(args["category"])
        elif action == "edit":
            content = "edit\n" + str(args.get("memory_id", "")) + "\n" + str(args.get("text", ""))
        elif action == "delete":
            content = "delete\n" + str(args.get("memory_id", ""))
        elif action == "search":
            content = "search\n" + str(args.get("text", ""))
        elif action == "list":
            content = "list" + (("\n" + str(args["category"])) if args.get("category") else "")
        else:
            content = action
    elif tool_type == "ui_control":
        action = args.get("action", "")
        name_arg = args.get("name", "")
        value = args.get("value", "")
        if action == "open_panel":
            content = f"open_panel {name_arg or value}"
        elif action == "toggle":
            content = f"toggle {name_arg} {value}"
        else:
            content = action
    elif tool_type in ("manage_tasks", "manage_skills", "api_call", "manage_endpoints",
                       "manage_mcp", "manage_webhooks", "manage_tokens",
                       "manage_documents", "manage_settings", "manage_notes",
                       "manage_research", "manage_bg_jobs"):
        content = json.dumps(args)
    elif tool_type in ("get_workspace", "list_models"):
        content = args.get("filter", "") if tool_type == "list_models" else ""
    else:
        content = json.dumps(args) if args else ""
    return ToolBlock(tool_type, str(content or ""))


def _parse_raw_openai_tool_call_json(text: str) -> Optional[ToolBlock]:
    if not isinstance(text, str) or '"function"' not in text:
        return None
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\[{]", text):
        try:
            parsed, _end = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        block = _raw_openai_tool_call_to_block(parsed)
        if block:
            return block
    return None


def _strip_raw_openai_tool_call_json(text: str) -> str:
    """Strip raw JSON tool calls such as {"function": {...}, "type": "function"}.

    Some local models emit native tool-call JSON into assistant text. The agent
    can still parse/execute it through the native path, but the raw payload must
    not render or persist as prose.
    """
    if not isinstance(text, str) or '"function"' not in text:
        return text
    decoder = json.JSONDecoder()
    pieces = []
    pos = 0
    changed = False
    for match in re.finditer(r"[\[{]", text):
        start = match.start()
        if start < pos:
            continue
        try:
            parsed, rel_end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        end = start + rel_end
        if not _looks_like_openai_tool_call_blob(parsed):
            continue
        pieces.append(text[pos:start])
        pos = end
        changed = True
        while pos < len(text) and text[pos] in " \t\r\n":
            pos += 1
        if pos < len(text) and text[pos] == "]":
            pos += 1
    if not changed:
        return text
    pieces.append(text[pos:])
    return "".join(pieces)


def iter_email_addresses(text: str, *, ascii_only: bool = False):
    """Find legacy email-shaped strings without retrying every word suffix.

    Match the address only at the start of each maximal local-part token.
    When that attempt fails, every suffix has the same '@'/domain boundary
    and must fail too. Local/domain character runs are each scanned a bounded
    number of times, including malformed text with no '@' or domain dot.
    """
    local_re = _ASCII_EMAIL_LOCAL_RE if ascii_only else _EMAIL_LOCAL_RE
    address_re = _ASCII_EMAIL_ADDRESS_RE if ascii_only else _EMAIL_ADDRESS_RE
    pos = 0
    while token := local_re.search(text, pos):
        address = address_re.match(text, token.start())
        if address is not None:
            yield address.group(0)
            pos = address.end()
        else:
            pos = token.end()


def _iter_qwen_python_args(raw_args: str):
    """Match each maximal key once, then its value at that fixed position.

    If a word has no following equals sign, none of its suffixes can have one
    either. Advancing past it avoids the old unanchored regex's O(n^2) retries
    on a long malformed key, while preserving permissive quoted/bare values.
    """
    pos = 0
    while key := _QWEN_PYTHON_ARG_KEY_RE.search(raw_args, pos):
        value = _QWEN_PYTHON_ARG_VALUE_RE.match(raw_args, key.end())
        if value is not None:
            yield key.group(0), value.group(1)
            pos = value.end()
        else:
            pos = key.end()


def _parse_qwen3_native_text_call(
    text: str,
    additional_tool_names: Optional[Iterable[str]] = None,
) -> Optional[ToolBlock]:
    """Parse textual call shapes emitted by Qwen3.x MLX adapters.

    This deployment is intentionally run without OpenAI tool schemas.  It
    commonly emits one call several times as one response, using either
    ``{"tool": ..., "parameters": ...}``, ``{"function": ..., ...}``,
    ``manage_notes(action=...)``, or a bare MCP tool name.  Return only the
    first valid call so duplicate renderings cannot execute repeatedly.
    """
    if not isinstance(text, str) or not text.strip():
        return None

    from src.tool_schemas import function_call_to_tool_block
    declared_names = _declared_tool_name_map(additional_tool_names)

    def declared_or_builtin(name: str, args: dict) -> Optional[ToolBlock]:
        normalized_name = name.strip().lower()
        if declared_name := declared_names.get(normalized_name):
            return ToolBlock(declared_name, json.dumps(args, ensure_ascii=False))
        return function_call_to_tool_block(name.strip(), json.dumps(args))

    # Qwen native text rendering:
    # <tool_call><function=manage_notes><parameter=action>list</parameter>...
    fn_match = next(_iter_named_blocks(
        text, _QWEN_FUNCTION_OPEN_RE, _QWEN_FUNCTION_CLOSE_RE
    ), None)
    if fn_match:
        name, raw_body = fn_match
        args = {}
        for key, raw_value in _iter_named_blocks(
            raw_body.rstrip(), _QWEN_PARAMETER_OPEN_RE, _QWEN_PARAMETER_CLOSE_RE,
        ):
            value = raw_value.strip()
            if value and value[0] in "[{\"":
                try:
                    args[key] = json.loads(value)
                    continue
                except (TypeError, json.JSONDecodeError):
                    pass
            args[key] = value
        block = declared_or_builtin(name, args)
        if block:
            return block

    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\[{]", text):
        try:
            value, _end = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        name = value.get("tool")
        args = value.get("parameters")
        if isinstance(name, str):
            if name.strip().lower() in declared_names and set(value) != {"tool", "parameters"}:
                continue
            if not isinstance(args, dict):
                args = {}
            block = declared_or_builtin(name, args)
            if block:
                return block
        name = value.get("function")
        args = value.get("arguments")
        if isinstance(name, str):
            if name.strip().lower() in declared_names and set(value) != {"function", "arguments"}:
                continue
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (TypeError, json.JSONDecodeError):
                    args = {}
            if not isinstance(args, dict):
                args = {}
            block = declared_or_builtin(name, args)
            if block:
                return block

    # Python-like single-call rendering: manage_notes(action="list").
    match = re.search(r"\b([A-Za-z_][\w:.-]*)\s*\(([^()]*)\)", text)
    if match:
        name, raw_args = match.groups()
        normalized_name = name.strip().lower()
        mapped_name = _TOOL_NAME_MAP.get(normalized_name, normalized_name)
        if (
            mapped_name in TOOL_TAGS
            or mapped_name.startswith("mcp__")
            or normalized_name in BUILTIN_EMAIL_TOOLS
            or normalized_name in declared_names
        ):
            args = {}
            for key, raw_value in _iter_qwen_python_args(raw_args):
                try:
                    args[key] = ast.literal_eval(raw_value.strip())
                except (ValueError, SyntaxError):
                    args[key] = raw_value.strip().strip("'\"")
            block = declared_or_builtin(normalized_name, args)
            if block:
                return block

    # Some MCP calls are emitted as the bare name, repeated once per format.
    names = re.findall(r"(?m)^\s*(mcp__[A-Za-z0-9_.-]+)\s*$", text)
    if names and len(set(names)) == 1:
        block = function_call_to_tool_block(names[0], "{}")
        if block:
            return block
    return None

def _parse_tool_call_block(raw: str) -> Optional[ToolBlock]:
    """Parse a [TOOL_CALL] block into a ToolBlock.

    Handles formats like:
      {tool => "shell", args => {--command "ls -la"}}
      {tool: "shell", command: "ls -la"}
    """
    # Try to extract tool name
    tool_match = re.search(r'tool\s*(?:=>|:|=)\s*["\']?(\w+)["\']?', raw, re.IGNORECASE)
    if not tool_match:
        return None

    tool_name = tool_match.group(1).lower()
    # Fall back to the raw name when it's a real tool but not in the alias
    # map, so known tools (e.g. manage_calendar) aren't silently dropped.
    mapped = _TOOL_NAME_MAP.get(tool_name) or (tool_name if tool_name in TOOL_TAGS else None)
    if not mapped:
        return None

    # Extract the command/content — try several patterns
    content = None

    # Pattern: --command "value" or --command 'value'
    cmd_match = re.search(r'--command\s+["\'](.+?)["\']', raw, re.DOTALL)
    if cmd_match:
        content = cmd_match.group(1)

    # Pattern: command => "value" or command: "value"
    if not content:
        cmd_match = re.search(r'command\s*(?:=>|:|=)\s*["\'](.+?)["\']', raw, re.DOTALL)
        if cmd_match:
            content = cmd_match.group(1)

    # Pattern: args => {content} — extract everything inside the nested braces.
    # Find the opener, then take through the LAST `}` (rfind). Equivalent to the
    # greedy `\{([\s\S]*)\}` capture, but the bounded opener + rfind avoids
    # finditer rescanning from every `args:{` opener (CodeQL py/polynomial-redos).
    if not content:
        am = _ARGS_BRACE_OPEN_RE.search(raw)
        close = raw.rfind('}')
        if am and close >= am.end():
            inner = raw[am.end():close].strip()
            # Strip quotes and key prefixes
            inner = re.sub(r'^--?\w+\s+', '', inner)
            inner = inner.strip('\'"')
            if inner:
                content = inner

    # Pattern: query/path/code => "value"
    if not content:
        for key in ("query", "path", "code", "content", "text", "file"):
            m = re.search(rf'{key}\s*(?:=>|:|=)\s*["\'](.+?)["\']', raw, re.DOTALL)
            if m:
                content = m.group(1)
                break

    # Last resort: take everything after the tool declaration
    if not content:
        rest = raw[tool_match.end():].strip()
        rest = re.sub(r'^[,;]\s*', '', rest)
        rest = rest.strip('{} \t\n\'"')
        if rest:
            content = rest

    if content:
        return ToolBlock(mapped, content.strip())
    return None


def _parse_xml_invoke(name, body) -> Optional[ToolBlock]:
    """Parse an <invoke name="tool"><parameter ...>...</parameter></invoke> call.

    Delegates content-shaping to function_call_to_tool_block — the SAME
    converter used for native function calls — so the full tool set (every
    name in TOOL_TAGS, plus email + MCP tools) and the correct per-tool
    content format are handled in ONE place. The previous version duplicated
    a partial, hand-maintained tool-name map plus a `key: value` serializer:
    any tool missing from that map (e.g. `manage_calendar`) was silently
    dropped, and JSON-arg tools got an unparseable `k: v` blob. Both bugs
    made deepseek's DSML `create_event` calls vanish with no execution.
    """
    # Lowercase the tool name: models often emit capitalized invoke names
    # (e.g. <invoke name="Bash">) and function_call_to_tool_block matches
    # case-sensitively against the lowercase _TOOL_NAME_MAP / TOOL_TAGS, so a
    # raw capitalized name would be silently dropped.
    tool_name = name.lower()
    params = {}
    for pname, pval in _iter_named_blocks(body, _XML_PARAM_OPEN_RE, _XML_PARAM_CLOSE_RE):
        params[pname] = pval.strip()
    # Local import to avoid a circular import at module load.
    from src.tool_schemas import function_call_to_tool_block
    return function_call_to_tool_block(tool_name, json.dumps(params))


def _parse_xml_direct_tool(name, body) -> Optional[ToolBlock]:
    """Parse direct XML tool tags inside <tool_call>.

    Some local models emit:
      <tool_call><web_search>query</web_search></tool_call>
    instead of the invoke/parameter shape:
      <tool_call><invoke name="web_search"><parameter name="query">query</parameter></invoke></tool_call>
    Keep this as an adapter to the canonical function-call converter so aliases
    and per-tool argument formatting stay in one place.
    """
    tool_name = name.lower().replace("-", "_")
    if tool_name in {"invoke", "parameter", "tool_call", "function_call"}:
        return None
    mapped = _TOOL_NAME_MAP.get(tool_name) or (tool_name if tool_name in TOOL_TAGS else None)
    if not mapped:
        return None
    body = body.strip()
    if not body:
        return None
    try:
        params = json.loads(body)
        if not isinstance(params, dict):
            params = {}
    except json.JSONDecodeError:
        if mapped == "web_search":
            params = {"query": body}
        elif mapped == "web_fetch":
            params = {"url": body}
        elif mapped == "bash":
            params = {"command": body}
        elif mapped == "python":
            params = {"code": body}
        elif mapped in ("read_file", "write_file"):
            params = {"path": body}
        else:
            params = {"content": body}
    from src.tool_schemas import function_call_to_tool_block
    return function_call_to_tool_block(mapped, json.dumps(params))


def _looks_like_json_body(body: str) -> bool:
    """True when a <tool_call> wrapper body is JSON, not XML markup."""
    return body.lstrip()[:1] in ("{", "[")


def _parse_json_tool_call_body(body: str) -> Optional[ToolBlock]:
    """Parse a Qwen/Hermes text-mode wrapper body: bare JSON inside <tool_call>.

      <tool_call>
      {"name": "bash", "arguments": {"command": "mkdir -p agent-test"}}
      </tool_call>

    Strict by design (issue #5187 / tracker #5333): the body must decode to an
    object with a string "name", and "arguments" — when present — must itself
    be an object. Anything else returns None rather than being coerced, so a
    malformed call is dropped instead of dispatching with mangled arguments.
    raw_decode tolerates trailing chatter after the JSON object; the trailing
    text is never scanned for tool markup. Conversion goes through
    function_call_to_tool_block so aliases and per-tool argument formatting
    stay identical to the XML invoke path.
    """
    stripped = body.strip()
    if not stripped.startswith("{"):
        return None
    try:
        parsed, _end = json.JSONDecoder().raw_decode(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    name = parsed.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    if "arguments" in parsed and not isinstance(parsed["arguments"], dict):
        return None
    args = parsed.get("arguments", {})
    from src.tool_schemas import function_call_to_tool_block
    return function_call_to_tool_block(name.strip().lower(), json.dumps(args))


def _iter_stepfun_tool_calls(text: str):
    """Yield StepFun native tool-call token bodies without regex backtracking."""
    pos = 0
    while True:
        start = text.find(_STEPFUN_CALL_BEGIN, pos)
        if start < 0:
            return
        name_start = start + len(_STEPFUN_CALL_BEGIN)
        sep = text.find(_STEPFUN_CALL_SEP, name_start)
        if sep < 0:
            return
        end = text.find(_STEPFUN_CALL_END, sep + len(_STEPFUN_CALL_SEP))
        if end < 0:
            return
        raw_name = text[name_start:sep].strip()
        body = text[sep + len(_STEPFUN_CALL_SEP):end].strip()
        if raw_name and len(raw_name) <= 128:
            yield raw_name, body
        pos = end + len(_STEPFUN_CALL_END)


def _strip_stepfun_tool_markup(text: str) -> str:
    """Remove StepFun tool-call token blocks and wrappers using literal scans."""
    out = []
    pos = 0
    while True:
        start = text.find(_STEPFUN_CALL_BEGIN, pos)
        if start < 0:
            out.append(text[pos:])
            break
        end = text.find(_STEPFUN_CALL_END, start + len(_STEPFUN_CALL_BEGIN))
        if end < 0:
            out.append(text[pos:])
            break
        out.append(text[pos:start])
        pos = end + len(_STEPFUN_CALL_END)
    cleaned = "".join(out)
    return cleaned.replace(_STEPFUN_CALLS_BEGIN, "").replace(_STEPFUN_CALLS_END, "")


def _strip_bare_invoke_markup(text: str) -> str:
    """Remove bare <invoke ...>...</invoke> blocks without regex backtracking."""
    out = []
    pos = 0
    while True:
        start = text.lower().find("<invoke", pos)
        if start < 0:
            out.append(text[pos:])
            break
        tag_end = text.find(">", start)
        if tag_end < 0:
            out.append(text[pos:])
            break
        close = text.lower().find("</invoke>", tag_end + 1)
        if close < 0:
            out.append(text[pos:])
            break
        out.append(text[pos:start])
        pos = close + len("</invoke>")
    return "".join(out)


def _parse_stepfun_tool_call(tool_name: str, body: str) -> Optional[ToolBlock]:
    """Parse StepFun native tool-call tokens into an Odysseus ToolBlock."""
    tool_name = tool_name.lower().replace("-", "_").replace(".", "_")
    mapped = _TOOL_NAME_MAP.get(tool_name) or (tool_name if tool_name in TOOL_TAGS else None)
    if not mapped:
        return None
    body = (body or "").strip()
    if not body:
        return None
    try:
        params = json.loads(body)
        if not isinstance(params, dict):
            params = {}
    except json.JSONDecodeError:
        if mapped == "web_search":
            params = {"query": body}
        elif mapped == "web_fetch":
            params = {"url": body}
        elif mapped == "bash":
            params = {"command": body}
        elif mapped == "python":
            params = {"code": body}
        elif mapped in ("read_file", "write_file"):
            params = {"path": body}
        else:
            params = {"content": body}
    from src.tool_schemas import function_call_to_tool_block
    return function_call_to_tool_block(mapped, json.dumps(params))


def _parse_tool_code_block(raw: str) -> Optional[ToolBlock]:
    """Parse a <tool_code>{tool => 'name', args => '...'}</tool_code> block (MiniMax style)."""
    # Extract tool name
    tool_match = re.search(r"tool\s*=>\s*['\"](\S+?)['\"]", raw)
    if not tool_match:
        return None
    tool_name = tool_match.group(1).lower().replace('-', '_')
    # Strip MCP prefixes like "mcp__server__" or "cli-mcp-server-"
    for prefix in ("mcp__", "cli_mcp_server_", "desktop_commander_", "mcp_code_executor_"):
        if tool_name.startswith(prefix):
            tool_name = tool_name[len(prefix):]
            break

    mapped = _TOOL_NAME_MAP.get(tool_name)

    # Extract args content
    args_match = re.search(r"args\s*=>\s*['\"]?\s*([\s\S]*?)\s*['\"]?\s*$", raw, re.DOTALL)
    args_body = args_match.group(1).strip().strip("'\"") if args_match else ""

    # Parse XML params inside args (e.g. <command>ls</command>). Forward-only
    # backref scan so a `<x><x>...` opener flood can't drive the O(n^2) lazy
    # rescan (CodeQL py/polynomial-redos); see _iter_backref_blocks.
    xml_params = {}
    for pname, pval in _iter_backref_blocks(args_body, _TOOL_CODE_PARAM_OPEN_RE, _TOOL_CODE_PARAM_CLOSE_ANY_RE):
        xml_params[pname] = pval.strip()

    # When the model gave structured params, hand them to the canonical
    # converter (same as native calls + <invoke>) so the full tool set and
    # correct per-tool content format apply — not a partial map + k:v blob.
    if xml_params:
        from src.tool_schemas import function_call_to_tool_block
        block = function_call_to_tool_block(mapped or tool_name, json.dumps(xml_params))
        if block:
            return block

    # No structured params: args_body is a raw single value (e.g. a bash
    # command). Keep the freeform special-casing for the simple tools.
    if mapped:
        if mapped == "bash":
            content = xml_params.get("command", args_body)
        elif mapped == "python":
            content = xml_params.get("code", args_body)
        elif mapped == "web_search":
            content = xml_params.get("query", args_body)
        elif mapped == "web_fetch":
            content = xml_params.get("url", args_body)
        elif mapped in ("read_file", "write_file"):
            content = xml_params.get("path", xml_params.get("file_path", args_body))
        else:
            content = "\n".join(f"{k}: {v}" for k, v in xml_params.items()) if xml_params else args_body
        if content:
            return ToolBlock(mapped, content.strip())
    elif tool_name and args_body:
        # Unknown tool — try as MCP tool call
        content = "\n".join(f"{k}: {v}" for k, v in xml_params.items()) if xml_params else args_body
        return ToolBlock(tool_name, content.strip())
    return None

_GEMMA_FALLBACK_KEY_RE = re.compile(r"\w++")
_GEMMA_FALLBACK_COLON_RE = re.compile(r"\s*+:")
_GEMMA_FALLBACK_BOUNDARY_RE = re.compile(r"(?<!\s)(\s*+)(?:,\s*+\w++\s*+:|})")


def _iter_gemma_fallback_items(body):
    """Preserve permissive k:v recovery with one pass over keys/boundaries."""
    boundaries = list(_GEMMA_FALLBACK_BOUNDARY_RE.finditer(body))
    boundary_index = 0
    pos = 0
    newline = body.find("\n")
    while key := _GEMMA_FALLBACK_KEY_RE.search(body, pos):
        colon = _GEMMA_FALLBACK_COLON_RE.match(body, key.end())
        if colon is None:
            pos = key.end()
            continue
        start = colon.end()
        while start < len(body) and body[start].isspace():
            start += 1
        if start < len(body) and body[start] in "\"'":
            start += 1
        while boundary_index < len(boundaries) and boundaries[boundary_index].end(1) < start:
            boundary_index += 1
        if boundary_index == len(boundaries):
            return
        boundary = boundaries[boundary_index]
        end = max(start, boundary.start())
        if 0 <= newline < start:
            newline = body.find("\n", start)
        if newline >= 0 and newline < end:
            pos = key.end()
            continue
        capture_end = end
        if capture_end > start and body[capture_end - 1] in "\"'":
            capture_end -= 1
        yield key.group(0), body[start:capture_end].strip()
        pos = end


def _parse_gemma_tool_call(tool_name: str, body: str) -> Optional[ToolBlock]:
    """Parse a Gemma-style call:tool_name{...} block into a ToolBlock."""
    tool_name = tool_name.strip().lower().replace("-", "_")
    body = body.strip()
    if not body:
        return None

    # Replace custom Gemma string delimiters with standard quotes
    body = body.replace('<|"|>', '"').replace('<|"', '"').replace('"|>', '"')

    # Try standard JSON parsing
    params = {}
    try:
        params = json.loads(body)
        if not isinstance(params, dict):
            params = {}
    except json.JSONDecodeError:
        # Try unquoted keys repair: e.g. {query: "..."} -> {"query": "..."}
        try:
            repaired = re.sub(r'([{,]\s*)(\w+)\s*:', r'\1"\2":', body)
            params = json.loads(repaired)
            if not isinstance(params, dict):
                params = {}
        except Exception:
            params = dict(_iter_gemma_fallback_items(body))

    from src.tool_schemas import function_call_to_tool_block
    return function_call_to_tool_block(tool_name, json.dumps(params))


def _parse_function_model_call(body: str) -> Optional[ToolBlock]:
    """Parse <function_model><function_call>tool</...><parameters>...</...>."""
    name_match = _FUNCTION_MODEL_NAME_RE.search(body or "")
    if not name_match:
        return None
    tool_name = name_match.group(1).strip().lower().replace("-", "_")
    params = "{}"
    for _ms, inner_start, inner_end, _me in _iter_delimited(
        body,
        _FUNCTION_MODEL_PARAMS_OPEN_RE,
        _FUNCTION_MODEL_PARAMS_CLOSE_RE,
    ):
        params = body[inner_start:inner_end].strip() or "{}"
        break
    from src.tool_schemas import function_call_to_tool_block
    return function_call_to_tool_block(tool_name, params)


def _iter_delimited(text, open_re, close_re):
    """Yield ``(match_start, inner_start, inner_end, match_end)`` for each
    non-overlapping ``open_re ... close_re`` pair, scanning strictly forward.

    For the lazy, non-nesting delimiters here this is equivalent to
    ``re.finditer`` of ``open_re([\\s\\S]*?)close_re`` (each opener pairs with
    the first closer after it; the next scan resumes past that closer), but it
    runs in O(n): the moment an opener has no reachable closer, no later opener
    can have one either, so we stop. ``re.finditer`` instead retries from every
    opener and rescans to end-of-string each time -> O(n^2) on attacker-
    controlled "many openers, no closer" model output (CodeQL py/polynomial-redos).

    A whole-string "is the closer present?" guard is not enough: a stale closer
    placed before an opener flood, or a closer with no matching inner delimiter
    (e.g. `[/TOOL_CALL]` but no `}`), keeps the guard true while every opener
    still rescans. Pairing each opener only with a closer *after* it closes both
    holes.
    """
    pos = 0
    while True:
        om = open_re.search(text, pos)
        if om is None:
            return
        cm = close_re.search(text, om.end())
        if cm is None:
            return
        yield om.start(), om.end(), cm.start(), cm.end()
        pos = cm.end()


def _strip_delimited(text: str, open_re, close_re, replacement: str = "") -> str:
    """Replace every ``open_re ... close_re`` span (forward-only; see
    _iter_delimited). Equivalent to ``open_re([\\s\\S]*?)close_re`` substitution
    for these delimiters, without the O(n^2) rescan on unclosed openers."""
    spans = list(_iter_delimited(text, open_re, close_re))
    if not spans:
        return text
    out = []
    last = 0
    for match_start, _inner_start, _inner_end, match_end in spans:
        out.append(text[last:match_start])
        out.append(replacement)
        last = match_end
    out.append(text[last:])
    return "".join(out)


def strip_angle_tags(text: str, replacement: str = "", *, allow_empty: bool = False) -> str:
    """Linear equivalent of replacing ``<[^>]+>`` (or ``<[^>]*>``).

    This preserves the existing flat text cleanup, including nested '<' and
    malformed tails; it does not interpret HTML. Stop once no '>' is reachable
    instead of retrying a suffix scan at every '<' in untrusted text.
    """
    opener = _ANGLE_TAG_OPEN_RE if allow_empty else _NONEMPTY_ANGLE_TAG_OPEN_RE
    return _strip_delimited(text, opener, _ANGLE_TAG_CLOSE_RE, replacement)


def _iter_named_blocks(text, open_re, close_re):
    """Forward-only equivalent of ``open_re([\\s\\S]*?)close_re`` finditer where
    open_re captures a name in group 1: yield ``(name, body)``, pairing each
    opener with the first ``close_re`` after it. O(n) once no closer is reachable
    from an opener, no later opener has one either (see _iter_delimited), so
    untrusted opener floods can't drive the lazy O(n^2) rescan."""
    pos = 0
    while True:
        om = open_re.search(text, pos)
        if om is None:
            return
        cm = close_re.search(text, om.end())
        if cm is None:
            return
        yield om.group(1), text[om.end():cm.start()]
        pos = cm.end()


def _iter_xml_invoke(text):
    """Forward-only ``<invoke name="..">...</invoke>`` scan (see _iter_named_blocks)."""
    return _iter_named_blocks(text, _XML_INVOKE_OPEN_RE, _XML_INVOKE_CLOSE_RE)


def _iter_backref_block_spans(text, open_re, close_any_re, ci=False):
    """Forward-only equivalent of an ``<tag>([\\s\\S]*?)</tag>`` backreference
    finditer (same-name open/close): yield ``(name, body)``, pairing each opener
    with the nearest following matching closer and skipping an opener whose
    closer is unreachable.

    Every closer is indexed by tag name in one linear pass, then each opener
    binary-searches its own name's closer positions. A flood of distinct unclosed
    tag names therefore stays O(n log n) rather than the lazy backref's O(n^2)
    suffix rescan (CodeQL py/polynomial-redos); per-name memoization alone left
    that distinct-name case quadratic. ``close_any_re`` matches ANY closer and
    captures its tag name in group 1; ``ci`` lowercases names for matching, since
    the original backref closer is case-insensitive under re.IGNORECASE."""
    norm = (lambda s: s.lower()) if ci else (lambda s: s)
    closer_starts = {}
    closer_ends = {}
    for cm in close_any_re.finditer(text):
        k = norm(cm.group(1))
        closer_starts.setdefault(k, []).append(cm.start())
        closer_ends.setdefault(k, []).append(cm.end())
    om = open_re.search(text)
    while om is not None:
        name = om.group(1)
        k = norm(name)
        resume = om.end()
        starts = closer_starts.get(k)
        if starts:
            i = bisect.bisect_left(starts, om.end())
            if i < len(starts):
                yield name, text[om.end():starts[i]], om.start(), closer_ends[k][i]
                resume = closer_ends[k][i]
        om = open_re.search(text, resume)


def _iter_backref_blocks(text, open_re, close_any_re, ci=False):
    for name, body, _start, _end in _iter_backref_block_spans(
        text, open_re, close_any_re, ci=ci
    ):
        yield name, body


def _iter_xml_direct(text):
    """Forward-only equivalent of ``_XML_DIRECT_TOOL_RE.finditer`` (see
    _iter_backref_blocks)."""
    return _iter_backref_blocks(text, _XML_DIRECT_OPEN_RE, _XML_DIRECT_CLOSE_ANY_RE, ci=True)


def _declared_tool_name_map(additional_tool_names: Optional[Iterable[str]]) -> dict[str, str]:
    return {
        name.lower(): name
        for raw_name in (additional_tool_names or ())
        if isinstance(raw_name, str)
        and (name := raw_name.strip())
        and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", name)
    }


def _unique_declared_schema_match(
    arguments: object,
    declared_names: dict[str, str],
    additional_tool_schemas: Optional[Iterable[dict]],
) -> Optional[str]:
    """Return one declared function whose object schema accepts every key."""
    if not isinstance(arguments, dict) or not arguments:
        return None
    argument_keys = set(arguments)
    matches: list[str] = []
    for raw_schema in additional_tool_schemas or ():
        if not isinstance(raw_schema, dict):
            continue
        function = raw_schema.get("function", raw_schema)
        if not isinstance(function, dict):
            continue
        raw_name = function.get("name")
        if not isinstance(raw_name, str):
            continue
        declared_name = declared_names.get(raw_name.strip().lower())
        parameters = function.get("parameters")
        if not declared_name or not isinstance(parameters, dict):
            continue
        properties = parameters.get("properties")
        required = parameters.get("required") or []
        if not isinstance(properties, dict) or not isinstance(required, list):
            continue
        if argument_keys <= set(properties) and set(required) <= argument_keys:
            matches.append(declared_name)
    return matches[0] if len(set(matches)) == 1 else None


def _parse_declared_direct_xml_calls(
    text: str,
    declared_names: dict[str, str],
) -> list[tuple[ToolBlock, int, int]]:
    """Parse direct XML only for request-declared tools with object arguments."""

    calls = []
    if not declared_names:
        return calls
    for raw_name, body, start, end in _iter_backref_block_spans(
        text, _XML_DIRECT_OPEN_RE, _XML_DIRECT_CLOSE_ANY_RE, ci=True
    ):
        declared_name = declared_names.get(raw_name.lower())
        if not declared_name:
            continue
        try:
            arguments = json.loads(body.strip())
        except (TypeError, ValueError):
            continue
        if isinstance(arguments, dict):
            calls.append((
                ToolBlock(declared_name, json.dumps(arguments, ensure_ascii=False)),
                start,
                end,
            ))
    return calls


def _parse_adjacent_declared_tool_fences(
    text: str,
    declared_names: dict[str, str],
    additional_tool_names: Optional[Iterable[str]],
) -> Optional[ToolBlock]:
    """Recover ``tool-name`` and JSON fences emitted as one textual call."""

    if not declared_names:
        return None
    matches = list(_tool_block_re(additional_tool_names).finditer(text))
    for index, match in enumerate(matches):
        call = _fenced_tool_call(match)
        if call is None:
            continue
        tag, content = call
        declared_name = declared_names.get(content.strip().lower())
        if not declared_name or tag not in {"bash", "python", "json", "function_name"}:
            continue
        if index > 0:
            previous_match = matches[index - 1]
            between = text[previous_match.end():match.start()]
            previous_call = _fenced_tool_call(previous_match)
            if not between.strip() and previous_call is not None and previous_call[0] == "json":
                try:
                    arguments = json.loads(previous_call[1])
                except (TypeError, ValueError):
                    arguments = None
                if isinstance(arguments, dict):
                    return ToolBlock(declared_name, json.dumps(arguments, ensure_ascii=False))
        tail = text[match.end():]
        if index + 1 < len(matches):
            next_match = matches[index + 1]
            if tail[:next_match.start() - match.end()].strip():
                continue
            next_call = _fenced_tool_call(next_match)
            if next_call is not None and next_call[0] == "json":
                try:
                    arguments = json.loads(next_call[1])
                except (TypeError, ValueError):
                    arguments = None
                if isinstance(arguments, dict):
                    return ToolBlock(declared_name, json.dumps(arguments, ensure_ascii=False))
        if not tail.strip():
            return ToolBlock(declared_name, "{}")
    return None


def parse_tool_blocks(
    text: str,
    skip_fenced: bool = False,
    additional_tool_names: Optional[Iterable[str]] = None,
    additional_tool_schemas: Optional[Iterable[dict]] = None,
) -> List[ToolBlock]:
    """Extract executable tool blocks from LLM response text.

    Supports multiple formats:
    1. ```bash ... ``` fenced code blocks (standard)
    2. [TOOL_CALL] ... [/TOOL_CALL] blocks (some models)
    3. XML-style <tool_call>/<invoke> blocks
    4. <tool_code> blocks (MiniMax-M2.5 style)
    5. StepFun Step-3 native <｜tool▁call▁begin｜> tokens
    6. DeepSeek DSML markup (normalized to <invoke> first)
    7. Non-native local model fallback: prose mentioning web_search followed by
       bare JSON args, e.g. {"query":"...", "time_filter":"week"}

    `skip_fenced`: when True, Pattern 1 (fenced ```bash/```python/```json code
    blocks) is not matched at all. Native function-calling models (GPT/Claude/
    Grok/Qwen3/DeepSeek-V, etc.) commonly write illustrative fenced examples in
    prose; for those models we trust the structured tool_calls channel for real
    invocations and treat a bare fence as display text rather than an action
    (issue #3222). Patterns 2-5 — explicit [TOOL_CALL]/<invoke>/<tool_code>/DSML
    markup that leaked into content as text — stay fully active regardless,
    since that markup is never an illustrative example and dropping it would
    silently lose real calls (e.g. DeepSeek-V falling back to DSML when it
    can't emit structured tool_calls).
    """
    blocks = []

    # Normalize DeepSeek DSML markup into standard <invoke> form so the
    # XML patterns below catch it.
    text = _normalize_dsml(text)

    # Pattern 1: fenced code blocks (skipped when `skip_fenced` — see docstring).
    # Explicit envelopes take precedence over Markdown fences. A fence in the
    # same response is commonly an example or scratch work, while the
    # envelope is the model's actual invocation.
    skip_fenced_for_mixed = skip_fenced or _contains_explicit_tool_markup(text)
    if not skip_fenced_for_mixed:
        additional_names = _declared_tool_name_map(additional_tool_names)
        adjacent_declared_call = _parse_adjacent_declared_tool_fences(
            text, additional_names, additional_tool_names
        )
        if adjacent_declared_call is not None:
            return [adjacent_declared_call]
        for m in _tool_block_re(additional_tool_names).finditer(text):
            call = _fenced_tool_call(m)
            if call is None:
                continue
            tag, content = call
            if not content:
                # An empty fence is still an unambiguous call for the email
                # tools — ```list_email_accounts``` with no body is a shape
                # local models really emit for no-arg tools. Dispatch with
                # empty args and let the tool's own validation answer;
                # silently dropping the call left models concluding email was
                # broken. Other tags (bash, python, ...) keep skipping: empty
                # content is nothing to run.
                if tag in BUILTIN_EMAIL_TOOLS or tag in additional_names:
                    declared_tag = additional_names.get(tag, tag)
                    blocks.append(ToolBlock(declared_tag, "{}" if tag in additional_names else ""))
                continue
            if tag in {"bash", "python"} and additional_names:
                raw_name, separator, raw_arguments = content.partition("\n")
                declared_name = additional_names.get(raw_name.strip().lower())
                if declared_name and separator and raw_arguments.strip():
                    blocks.append(ToolBlock(declared_name, raw_arguments.strip()))
                    continue
            if tag == "json" and additional_names:
                try:
                    flat_envelope = json.loads(content)
                except (TypeError, ValueError):
                    flat_envelope = None
                if (
                    isinstance(flat_envelope, dict)
                    and set(flat_envelope) == {"function", "arguments"}
                    and isinstance(flat_envelope.get("function"), str)
                    and isinstance(flat_envelope.get("arguments"), dict)
                    and (
                        declared_name := additional_names.get(
                            flat_envelope["function"].strip().lower()
                        )
                    )
                ):
                    blocks.append(ToolBlock(
                        declared_name,
                        json.dumps(flat_envelope["arguments"], ensure_ascii=False),
                    ))
                    continue
                if (
                    declared_name := _unique_declared_schema_match(
                        flat_envelope,
                        additional_names,
                        additional_tool_schemas,
                    )
                ):
                    blocks.append(ToolBlock(
                        declared_name,
                        json.dumps(flat_envelope, ensure_ascii=False),
                    ))
                    continue
                raw_name, separator, raw_arguments = content.partition("\n")
                declared_name = additional_names.get(raw_name.strip().lower())
                if declared_name and separator:
                    try:
                        arguments = json.loads(raw_arguments)
                    except (TypeError, ValueError):
                        arguments = None
                    if isinstance(arguments, dict):
                        blocks.append(ToolBlock(declared_name, json.dumps(arguments, ensure_ascii=False)))
                        continue
                elif declared_name:
                    # Some local models close the JSON fence after the function
                    # name, then emit the argument object immediately after it:
                    # ```json\nfunction\n```\n{"arg": 1}\n```. Decode the object
                    # structurally and require that no prose follows it.
                    tail = text[m.end():].lstrip()
                    try:
                        arguments, consumed = json.JSONDecoder().raw_decode(tail)
                    except (TypeError, ValueError):
                        arguments, consumed = None, 0
                    remainder = tail[consumed:].strip() if consumed else tail
                    if isinstance(arguments, dict) and remainder in {"", "```"}:
                        blocks.append(ToolBlock(declared_name, json.dumps(arguments, ensure_ascii=False)))
                        continue
                # ``json`` is only an envelope for a declared function, never
                # a dispatchable tool in its own right.
                continue
            # If a code block's content is an <invoke> XML call (some models wrap
            # tool calls in ```python or ```xml fences), parse the invoke instead.
            if '<invoke' in content:
                for inv_name, inv_body in _iter_xml_invoke(content):
                    block = _parse_xml_invoke(inv_name, inv_body)
                    if block:
                        blocks.append(block)
                # This fenced block is <invoke> markup, not literal code. Whether or
                # not any call converted, never fall through to append the raw XML as
                # a python/bash block — e.g. a hyphenated/namespaced tool name that
                # _XML_INVOKE_RE's \w+ can't match would otherwise be executed as code.
                continue
            if tag in ("python", "bash"):
                block = (_parse_misfenced_media_lookup(content)
                         or _parse_misfenced_web_lookup(content)
                         or _parse_misfenced_read_file_lookup(content, allow_shell_style=(tag == "bash")))
                if block:
                    blocks.append(block)
                    continue
            blocks.append(ToolBlock(tag, content))

    # Pattern 2: [TOOL_CALL] blocks (only if no fenced blocks found)
    # _iter_delimited scans the delimiter-bounded formats forward-only so
    # untrusted "many openers, no closer" output can't drive the O(n^2)
    # finditer rescan (ReDoS); see its docstring.
    if not blocks:
        for _ms, inner_start, inner_end, _me in _iter_delimited(
            text, _TOOL_CALL_OPEN_RE, _TOOL_CALL_CLOSE_RE
        ):
            block = _parse_tool_call_block(text[inner_start:inner_end])
            if block:
                blocks.append(block)

    # Pattern 3: XML-style <tool_call>/<invoke> blocks
    if not blocks:
        for tool_name, body in _iter_stepfun_tool_calls(text):
            block = _parse_stepfun_tool_call(tool_name, body)
            if block:
                blocks.append(block)
        if blocks:
            return blocks
        # Try wrapped: <tool_call><invoke ...>...</invoke></tool_call>
        # A wrapper body that is JSON (Qwen/Hermes text mode, issue #5187) is
        # parsed as JSON or dropped — never scanned by the XML iterators, so
        # XML-like text inside JSON argument values stays data instead of
        # selecting a different tool.
        json_body_seen = False
        for _ms, inner_start, inner_end, _me in _iter_delimited(
            text, _XML_TOOL_CALL_OPEN_RE, _XML_TOOL_CALL_CLOSE_RE
        ):
            body = text[inner_start:inner_end]
            if _looks_like_json_body(body):
                json_body_seen = True
                block = _parse_json_tool_call_body(body)
                if block:
                    blocks.append(block)
                continue
            for inv_name, inv_body in _iter_xml_invoke(body):
                block = _parse_xml_invoke(inv_name, inv_body)
                if block:
                    blocks.append(block)
            if not blocks:
                for d_name, d_body in _iter_xml_direct(body):
                    block = _parse_xml_direct_tool(d_name, d_body)
                    if block:
                        blocks.append(block)
        # Some local models stream an opening <tool_call> wrapper and a
        # complete inner tool tag, but forget the closing </tool_call>.
        if not blocks:
            for m in _XML_OPEN_TOOL_CALL_RE.finditer(text):
                body = m.group(1)
                if _looks_like_json_body(body):
                    # Same fail-closed rule as above for an unclosed wrapper.
                    json_body_seen = True
                    block = _parse_json_tool_call_body(body)
                    if block:
                        blocks.append(block)
                    break
                for inv_name, inv_body in _iter_xml_invoke(body):
                    block = _parse_xml_invoke(inv_name, inv_body)
                    if block:
                        blocks.append(block)
                if blocks:
                    break
                for d_name, d_body in _iter_xml_direct(body):
                    block = _parse_xml_direct_tool(d_name, d_body)
                    if block:
                        blocks.append(block)
        # Try bare <invoke> without wrapper. Skipped when a JSON wrapper body
        # was seen but produced no block: this rescan covers the full text,
        # wrapper bodies included, and <invoke> markup inside a (possibly
        # malformed) JSON payload must stay data rather than dispatch.
        if not blocks and not json_body_seen:
            for inv_name, inv_body in _iter_xml_invoke(text):
                block = _parse_xml_invoke(inv_name, inv_body)
                if block:
                    blocks.append(block)
        if not blocks:
            blocks.extend(
                block
                for block, _start, _end in _parse_declared_direct_xml_calls(
                    text, _declared_tool_name_map(additional_tool_names)
                )
            )

    # Pattern 4: <tool_code> blocks (MiniMax-M2.5 style)
    if not blocks:
        for _ms, inner_start, inner_end, _me in _iter_delimited(
            text, _TOOL_CODE_OPEN_RE, _TOOL_CODE_CLOSE_RE
        ):
            block = _parse_tool_code_block(text[inner_start:inner_end])
            if block:
                blocks.append(block)

    # Pattern 4b: Gemma-style <|tool_call|> blocks
    if not blocks:
        for tool_name, body in _iter_named_blocks(
            text, _GEMMA_TOOL_CALL_OPEN_RE, _GEMMA_TOOL_CALL_CLOSE_RE
        ):
            block = _parse_gemma_tool_call(tool_name, "{" + body + "}")
            if block:
                blocks.append(block)

    # Pattern 4c: <function_model> wrapper from local MLX/Exo models.
    if not blocks:
        for _ms, inner_start, inner_end, _me in _iter_delimited(
            text, _FUNCTION_MODEL_OPEN_RE, _FUNCTION_MODEL_CLOSE_RE
        ):
            block = _parse_function_model_call(text[inner_start:inner_end])
            if block:
                blocks.append(block)

    # Pattern 4d: raw OpenAI-style tool-call JSON leaked as assistant text.
    # Example: {"function":{"arguments":"{\"action\":\"add\"}","name":"manage_memory"},"type":"function"}
    if not blocks:
        block = _parse_raw_openai_tool_call_json(text)
        if block:
            blocks.append(block)

    # Pattern 4e: Qwen3.x MLX textual call fallback.  This must run after the
    # explicit markup parsers but before the response is treated as prose.
    if not blocks:
        block = _parse_qwen3_native_text_call(text, additional_tool_names)
        if block:
            blocks.append(block)

    # Pattern 6: local text-model web_search call leaked as prose + bare JSON.
    if not blocks and not skip_fenced:
        raw_web_json = _parse_raw_web_json_lookup(text)
        if raw_web_json:
            blocks.append(raw_web_json[0])

    # Pattern 7: plain `ui_control open_panel notes` line. This commonly comes
    # from weaker native-tool models after reading the tool docs but failing to
    # emit the actual structured call.
    if not blocks:
        m = next(_iter_plain_ui_open_panel(text), None)
        if m:
            blocks.append(ToolBlock("ui_control", f"open_panel {m.group(1).lower()}{m.group(2).lower()}".strip()))

    return blocks


def strip_tool_blocks(
    text: str,
    skip_fenced: bool = False,
    additional_tool_names: Optional[Iterable[str]] = None,
) -> str:
    """Remove executable tool blocks from text for clean display.

    `skip_fenced`: when True, fenced ```bash/```python/```json code blocks
    (Pattern 1) are left intact instead of being stripped. This must mirror
    whatever `skip_fenced` value `parse_tool_blocks` was called with for the
    same response: if a fence wasn't executed as a tool call (because it's an
    illustrative example from a native function-calling model), it shouldn't
    vanish from the persisted/displayed text either — otherwise the example
    streams once and then disappears on reload (issue #3222 follow-up).
    Patterns 2-5 + DSML markup are always stripped, since that markup should
    never reach the user regardless of whether it converted to a tool call.
    """
    # Normalize DSML first so its markup gets stripped by the <invoke>
    # / <tool_call> removers below instead of leaking to the user.
    text = _normalize_dsml(text)
    # Keep the executed-vs-illustrative fence distinction (only strip fences
    # that actually dispatched; leave example fences from native models inert
    # but visible), then remove [TOOL_CALL]{...}[/TOOL_CALL] markup.
    cleaned = (
        text
        if (skip_fenced or _contains_explicit_tool_markup(text))
        else _tool_block_re(additional_tool_names).sub(_strip_executed_fence, text)
    )
    # Forward-only removal mirrors parse_tool_blocks: _strip_delimited pairs each
    # opener with a later closer and stops when none is reachable, so untrusted
    # output can't drive the O(n^2) lazy-rescan (ReDoS); see _iter_delimited.
    cleaned = _strip_delimited(cleaned, _TOOL_CALL_OPEN_RE, _TOOL_CALL_CLOSE_RE)
    cleaned = _strip_stepfun_tool_markup(cleaned)
    cleaned = _strip_delimited(cleaned, _XML_TOOL_CALL_OPEN_RE, _XML_TOOL_CALL_CLOSE_RE)
    cleaned = _XML_OPEN_TOOL_CALL_RE.sub('', cleaned)
    cleaned = _strip_delimited(cleaned, _TOOL_CODE_OPEN_RE, _TOOL_CODE_CLOSE_RE)
    cleaned = _strip_delimited(cleaned, _GEMMA_TOOL_CALL_OPEN_RE, _GEMMA_TOOL_CALL_CLOSE_RE)
    cleaned = _strip_delimited(cleaned, _FUNCTION_MODEL_OPEN_RE, _FUNCTION_MODEL_CLOSE_RE)
    cleaned = _strip_raw_openai_tool_call_json(cleaned)
    declared_xml_calls = _parse_declared_direct_xml_calls(
        cleaned, _declared_tool_name_map(additional_tool_names)
    )
    if declared_xml_calls:
        cleaned = _strip_spans(
            cleaned,
            [(start, start, end, end) for _block, start, end in declared_xml_calls],
        )
    cleaned = _QWEN_OPEN_TOOLS_RE.sub('', cleaned)
    cleaned = _QWEN_ROLE_MARKER_RE.sub('', cleaned)
    cleaned = _QWEN_BARE_MARKER_RE.sub(' ', cleaned)
    if not skip_fenced:
        raw_web_json = _parse_raw_web_json_lookup(cleaned)
        if raw_web_json:
            _, (start, end) = raw_web_json
            cleaned = cleaned[:start] + cleaned[end:]
    cleaned = _strip_plain_ui_open_panel(cleaned)
    # Strip bare <invoke> blocks not wrapped in <tool_call>
    cleaned = _strip_bare_invoke_markup(cleaned)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()
