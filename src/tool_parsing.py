"""
tool_parsing.py

Regex-based parsing of tool invocations from LLM response text.
Supports fenced code blocks, [TOOL_CALL] blocks, and XML-style <invoke> blocks.
"""

import ast
import json
import logging
import re
from typing import List, Optional

from src.agent_tools import ToolBlock, TOOL_TAGS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Pattern 1: ```bash ... ``` fenced code blocks
_TOOL_BLOCK_RE = re.compile(
    r"```(" + "|".join(TOOL_TAGS) + r")\s*\n([\s\S]*?)```",
    re.IGNORECASE,
)

# Pattern 2: [TOOL_CALL] ... [/TOOL_CALL] blocks (some models use this format)
# Matches: {tool => "shell", args => {--command "ls -la"}} etc.
_TOOL_CALL_RE = re.compile(
    r"\[TOOL_CALL\]\s*\{([\s\S]*?)\}\s*\[/TOOL_CALL\]",
    re.IGNORECASE,
)

# Pattern 3: XML-style tool calls (minimax, some other models)
# <minimax:tool_call><invoke name="bash"><parameter name="command">...</parameter></invoke></minimax:tool_call>
# Also handles: <tool_call><invoke ...>, <function_call><invoke ...>, plain <invoke ...>
_XML_TOOL_CALL_RE = re.compile(
    r"<(?:[\w]+:)?(?:tool_call|function_call)>\s*([\s\S]*?)</(?:[\w]+:)?(?:tool_call|function_call)>",
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

# Pattern 4: <tool_code> blocks (MiniMax-M2.5 style)
# {tool => 'tool_name', args => '<param>value</param>'}
_TOOL_CODE_RE = re.compile(
    r"<tool_code>\s*\{([\s\S]*?)\}\s*</tool_code>",
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
def _normalize_dsml(text: str) -> str:
    if not isinstance(text, str):
        return ""
    if "DSML" not in text:
        return text
    t = text
    t = re.sub(rf"<\s*{_DSML_PIPES}\s*DSML\s*{_DSML_PIPES}\s*tool_calls\s*>", "<tool_call>", t, flags=re.IGNORECASE)
    t = re.sub(rf"<\s*/\s*{_DSML_PIPES}\s*DSML\s*{_DSML_PIPES}\s*tool_calls\s*>", "</tool_call>", t, flags=re.IGNORECASE)
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
    "write": "write_file",
    "write_file": "write_file",
    "save": "write_file",
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

_RAW_WEB_JSON_TOOL_RE = re.compile(
    r"\b(?:web_search|websearch|google_search|google_search_retrieval|google_search_grounding)\b",
    re.IGNORECASE,
)
_RAW_WEB_JSON_ALLOWED_KEYS = {"query", "queries", "time_filter", "freshness", "max_pages"}


# ---------------------------------------------------------------------------
# Parsing functions
# ---------------------------------------------------------------------------

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
        module = ast.parse(content.strip(), mode="exec")
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

    # Pattern: args => {content} — extract everything inside the nested braces
    if not content:
        args_match = re.search(r'args\s*(?:=>|:|=)\s*\{([\s\S]*)\}', raw, re.DOTALL)
        if args_match:
            inner = args_match.group(1).strip()
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


def _parse_xml_invoke(inv_match) -> Optional[ToolBlock]:
    """Parse an <invoke name="tool"><parameter ...>...</parameter></invoke> match.

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
    tool_name = inv_match.group(1).lower()
    body = inv_match.group(2)
    params = {}
    for pm in _XML_PARAM_RE.finditer(body):
        params[pm.group(1)] = pm.group(2).strip()
    # Local import to avoid a circular import at module load.
    from src.tool_schemas import function_call_to_tool_block
    return function_call_to_tool_block(tool_name, json.dumps(params))


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

    # Parse XML params inside args (e.g. <command>ls</command>)
    xml_params = {}
    for pm in re.finditer(r"<(\w+)>([\s\S]*?)</\1>", args_body):
        xml_params[pm.group(1)] = pm.group(2).strip()

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


def parse_tool_blocks(text: str, skip_fenced: bool = False) -> List[ToolBlock]:
    """Extract executable tool blocks from LLM response text.

    Supports multiple formats:
    1. ```bash ... ``` fenced code blocks (standard)
    2. [TOOL_CALL] ... [/TOOL_CALL] blocks (some models)
    3. XML-style <tool_call>/<invoke> blocks
    4. <tool_code> blocks (MiniMax-M2.5 style)
    5. DeepSeek DSML markup (normalized to <invoke> first)
    6. Non-native local model fallback: prose mentioning web_search followed by
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
    if not skip_fenced:
        for m in _TOOL_BLOCK_RE.finditer(text):
            tag = m.group(1).lower()
            content = m.group(2).strip()
            if not content:
                continue
            # If a code block's content is an <invoke> XML call (some models wrap
            # tool calls in ```python or ```xml fences), parse the invoke instead.
            if '<invoke' in content:
                for inv in _XML_INVOKE_RE.finditer(content):
                    block = _parse_xml_invoke(inv)
                    if block:
                        blocks.append(block)
                # This fenced block is <invoke> markup, not literal code. Whether or
                # not any call converted, never fall through to append the raw XML as
                # a python/bash block — e.g. a hyphenated/namespaced tool name that
                # _XML_INVOKE_RE's \w+ can't match would otherwise be executed as code.
                continue
            if tag in ("python", "bash"):
                block = _parse_misfenced_web_lookup(content)
                if block:
                    blocks.append(block)
                    continue
            blocks.append(ToolBlock(tag, content))

    # Pattern 2: [TOOL_CALL] blocks (only if no fenced blocks found)
    if not blocks:
        for m in _TOOL_CALL_RE.finditer(text):
            block = _parse_tool_call_block(m.group(1))
            if block:
                blocks.append(block)

    # Pattern 3: XML-style <tool_call>/<invoke> blocks
    if not blocks:
        # Try wrapped: <tool_call><invoke ...>...</invoke></tool_call>
        for m in _XML_TOOL_CALL_RE.finditer(text):
            for inv in _XML_INVOKE_RE.finditer(m.group(1)):
                block = _parse_xml_invoke(inv)
                if block:
                    blocks.append(block)
        # Try bare <invoke> without wrapper
        if not blocks:
            for inv in _XML_INVOKE_RE.finditer(text):
                block = _parse_xml_invoke(inv)
                if block:
                    blocks.append(block)

    # Pattern 4: <tool_code> blocks (MiniMax-M2.5 style)
    if not blocks:
        for m in _TOOL_CODE_RE.finditer(text):
            block = _parse_tool_code_block(m.group(1))
            if block:
                blocks.append(block)

    # Pattern 6: local text-model web_search call leaked as prose + bare JSON.
    if not blocks and not skip_fenced:
        raw_web_json = _parse_raw_web_json_lookup(text)
        if raw_web_json:
            blocks.append(raw_web_json[0])

    return blocks


def strip_tool_blocks(text: str, skip_fenced: bool = False) -> str:
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
    cleaned = text if skip_fenced else _TOOL_BLOCK_RE.sub('', text)
    cleaned = _TOOL_CALL_RE.sub('', cleaned)
    cleaned = _XML_TOOL_CALL_RE.sub('', cleaned)
    cleaned = _TOOL_CODE_RE.sub('', cleaned)
    if not skip_fenced:
        raw_web_json = _parse_raw_web_json_lookup(cleaned)
        if raw_web_json:
            _, (start, end) = raw_web_json
            cleaned = cleaned[:start] + cleaned[end:]
    # Strip bare <invoke> blocks not wrapped in <tool_call>
    cleaned = re.sub(r'<invoke\s+name=["\'].*?</invoke>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()
