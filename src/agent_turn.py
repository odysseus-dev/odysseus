"""One streamed agent round.

The outer agent loop owns run setup and multi-round decisions. This module owns
the repeated round work: stream one model response, resolve tool calls, and run
the tools for that round.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from src.tool_policy import ToolPolicy

logger = logging.getLogger(__name__)

_ADMIN_SCHEMA_NAMES = {
    "manage_session", "manage_skills", "manage_tasks",
    "manage_endpoints", "manage_mcp", "manage_webhooks", "manage_tokens",
    "create_session", "list_sessions", "send_to_session", "pipeline",
    "ask_teacher", "list_models", "search_chats",
}
_DOCUMENT_TOOLS = {"create_document", "update_document", "edit_document", "suggest_document"}
_DOCUMENT_STREAM_LANGS = {
    "python", "py", "javascript", "js", "typescript", "ts", "html", "css",
    "json", "yaml", "bash", "sql", "rust", "go", "java", "c", "cpp",
    "markdown", "text",
}


def sse(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _resolve_tool_blocks(round_response: str, native_tool_calls: list, round_num: int):
    """Choose native function calls or fenced code block parsing."""
    from src.agent_tools import function_call_to_tool_block, parse_tool_blocks

    used_native = False
    tool_blocks = []
    if native_tool_calls:
        for tc in native_tool_calls:
            tc_name = tc.get("name", "")
            tc_args = tc.get("arguments", "{}")
            block = function_call_to_tool_block(tc_name, tc_args)
            if block:
                tool_blocks.append(block)
                logger.info("  -> converted: %s -> %s", tc_name, block.tool_type)
            else:
                logger.warning("  -> FAILED to convert native call: %s args=%s", tc_name, tc_args[:200])
        used_native = bool(tool_blocks)
    if not used_native:
        tool_blocks = parse_tool_blocks(round_response)
        if tool_blocks:
            logger.info("Agent round %s: %s fenced tool block(s) detected", round_num, len(tool_blocks))

    resp_preview = round_response[:200].replace("\n", "\\n") if round_response else "(empty)"
    logger.info(
        "Agent round %s summary: %s chars, %s native calls, %s tool blocks. Preview: %s",
        round_num,
        len(round_response),
        len(native_tool_calls),
        len(tool_blocks),
        resp_preview,
    )
    return tool_blocks, used_native


def _append_tool_results(
    messages: List[Dict],
    round_response: str,
    native_tool_calls: list,
    tool_results: list,
    tool_result_texts: list,
    used_native: bool,
    round_num: int,
    round_reasoning: str = "",
):
    """Append tool execution results back into history for the next LLM round."""
    for msg in messages:
        if msg.get("role") == "assistant":
            msg.pop("reasoning_content", None)
    if used_native and native_tool_calls:
        assistant_msg = {"role": "assistant"}
        assistant_msg["content"] = round_response if round_response.strip() else None
        if round_reasoning:
            assistant_msg["reasoning_content"] = round_reasoning
        assistant_msg["tool_calls"] = [
            {
                "id": tc.get("id", f"call_{round_num}_{j}"),
                "type": "function",
                "function": {
                    "name": tc.get("name", ""),
                    "arguments": tc.get("arguments", "{}"),
                },
                **({"extra_content": tc["extra_content"]} if tc.get("extra_content") else {}),
            }
            for j, tc in enumerate(native_tool_calls)
        ]
        messages.append(assistant_msg)
        for j, tc in enumerate(native_tool_calls):
            result_text = tool_result_texts[j] if j < len(tool_result_texts) else ""
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", f"call_{round_num}_{j}"),
                "content": result_text,
            })
    else:
        tool_output_text = "\n\n".join(tool_results)
        msg = {"role": "assistant", "content": round_response}
        if round_reasoning:
            msg["reasoning_content"] = round_reasoning
        messages.append(msg)
        messages.append({"role": "user", "content": f"[Tool execution results]\n\n{tool_output_text}"})


def select_turn_tool_schemas(
    *,
    force_answer: bool,
    is_api_model: bool,
    relevant_tools: Optional[Set[str]],
    mcp_schemas: list,
    needs_admin: bool,
    disabled_tools: Set[str],
    last_user: str,
    mcp_keywords: Set[str],
) -> list:
    """Select native tool schemas for a single model round."""
    from src.agent_tools import FUNCTION_TOOL_SCHEMAS

    if force_answer:
        return []
    if is_api_model:
        if relevant_tools:
            base = [
                schema for schema in FUNCTION_TOOL_SCHEMAS
                if schema.get("function", {}).get("name") in relevant_tools
            ]
            mcp = [
                schema for schema in mcp_schemas
                if schema.get("function", {}).get("name") in relevant_tools
            ]
            schemas = base + mcp
        else:
            base = FUNCTION_TOOL_SCHEMAS if needs_admin else [
                schema for schema in FUNCTION_TOOL_SCHEMAS
                if schema.get("function", {}).get("name") not in _ADMIN_SCHEMA_NAMES
            ]
            schemas = base + mcp_schemas
        if disabled_tools:
            return [
                schema for schema in schemas
                if schema.get("function", {}).get("name") not in disabled_tools
                and schema.get("name") not in disabled_tools
            ]
        return schemas

    wants_mcp = any(keyword in (last_user or "").lower() for keyword in mcp_keywords)
    return mcp_schemas if wants_mcp and mcp_schemas else []


@dataclass
class TurnUsage:
    actual_model: str
    input_tokens: int = 0
    output_tokens: int = 0
    last_round_input_tokens: int = 0
    has_real_usage: bool = False
    backend_gen_tps: float = 0
    backend_prefill_tps: float = 0
    time_to_first_token: Optional[float] = None
    first_token_received: bool = False


@dataclass
class ModelTurnResult:
    response_text: str = ""
    round_response: str = ""
    round_reasoning: str = ""
    native_tool_calls: list = field(default_factory=list)
    tool_blocks: list = field(default_factory=list)
    used_native: bool = False
    usage: Optional[TurnUsage] = None
    doc_stream_started: bool = False


@dataclass
class ModelTurnRequest:
    round_num: int
    candidates: list
    messages: List[Dict]
    temperature: float
    max_tokens: int
    prompt_type: Optional[str]
    tool_schemas: list
    timeout: int
    deadline: float
    requested_model: str
    actual_model: str
    total_start: float
    first_token_received: bool
    tool_policy: Optional[ToolPolicy]
    stream_llm: Callable[..., Any]


class DocumentStream:
    def __init__(self) -> None:
        self.arg_acc = ""
        self.opened = False
        self.started = False
        self.last_len = 0
        self.fence_offset = 0
        self.scan_from = 0

    def handle_native_delta(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        self.arg_acc += data.get("arg_delta", "")
        if not self.opened:
            title_match = re.search(r'"title"\s*:\s*"((?:[^"\\]|\\.)*)"', self.arg_acc)
            if title_match:
                self.opened = True
                self.started = True
                title = _decode_json_string(title_match.group(1))
                lang_match = re.search(r'"language"\s*:\s*"((?:[^"\\]|\\.)*)"', self.arg_acc)
                lang = _decode_json_string(lang_match.group(1)) if lang_match else ""
                logger.info("Doc streaming: open title=%r lang=%r", title, lang)
                events.append({"type": "doc_stream_open", "title": title, "language": lang})
        if self.opened:
            content_match = re.search(r'"content"\s*:\s*"', self.arg_acc)
            if content_match:
                raw = self.arg_acc[content_match.end():]
                raw = re.sub(r'"\s*\}\s*$', "", raw)
                decoded = _decode_partial_json_string(raw)
                if len(decoded) > self.last_len:
                    self.last_len = len(decoded)
                    events.append({"type": "doc_stream_delta", "content": decoded})
        return events

    def handle_fenced_delta(self, round_response: str) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        marker = "```create_document\n"
        if not self.opened and marker in round_response[self.scan_from:]:
            fence_index = round_response.index(marker, self.scan_from)
            after = round_response[fence_index + len(marker):]
            lines = after.split("\n")
            if lines and lines[0].strip():
                self.opened = True
                self.started = True
                title = lines[0].strip()
                lang = lines[1].strip() if len(lines) > 1 and lines[1].strip().lower() in _DOCUMENT_STREAM_LANGS else ""
                self.fence_offset = fence_index + len(marker) + len(lines[0]) + 1
                if lang:
                    self.fence_offset += len(lines[1]) + 1
                self.last_len = 0
                events.append({"type": "doc_stream_open", "title": title, "language": lang})
        if self.opened:
            content = round_response[self.fence_offset:]
            close_index = content.find("\n```")
            if close_index >= 0:
                content = content[:close_index]
            if len(content) > self.last_len:
                self.last_len = len(content)
                events.append({"type": "doc_stream_delta", "content": content})
            if close_index >= 0:
                self.opened = False
                self.scan_from = self.fence_offset + close_index + len("\n```")
                self.fence_offset = 0
                self.last_len = 0
        return events


class ModelTurn:
    def __init__(self, request: ModelTurnRequest) -> None:
        self.request = request
        self.doc_stream = DocumentStream()
        self.result = ModelTurnResult(
            usage=TurnUsage(
                actual_model=request.actual_model,
                first_token_received=request.first_token_received,
            )
        )

    async def stream(self):
        req = self.request
        async for chunk in req.stream_llm(
            req.candidates,
            req.messages,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            prompt_type=req.prompt_type if req.round_num == 1 else None,
            tools=req.tool_schemas if req.tool_schemas else None,
            timeout=req.timeout,
        ):
            if time.time() > req.deadline:
                logger.warning("[agent] round %s stream exceeded wall-clock deadline; cutting off", req.round_num)
                break
            if chunk.startswith("event: error"):
                yield chunk
                continue
            if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
                try:
                    data = json.loads(chunk[6:])
                except json.JSONDecodeError:
                    if req.round_num == 1:
                        yield chunk
                    continue
                async for event in self._handle_data_chunk(data, chunk):
                    yield event
            elif chunk.startswith("event: "):
                yield chunk

        result = self.result
        result.tool_blocks, result.used_native = _resolve_tool_blocks(
            result.round_response,
            result.native_tool_calls,
            req.round_num,
        )
        result.doc_stream_started = self.doc_stream.started

    async def _handle_data_chunk(self, data: Dict[str, Any], chunk: str):
        req = self.request
        result = self.result
        usage = result.usage
        assert usage is not None

        if data.get("type") == "tool_call_delta":
            if req.tool_policy and req.tool_policy.blocks(data.get("name")):
                return
            logger.debug(
                "tool_call_delta: name=%s, len(arg_delta)=%s",
                data.get("name"),
                len(data.get("arg_delta", "")),
            )
            for event in self.doc_stream.handle_native_delta(data):
                yield sse(event)
        elif data.get("type") == "tool_calls":
            result.native_tool_calls = data.get("calls", [])
            logger.info("Agent round %s: received %s native tool call(s)", req.round_num, len(result.native_tool_calls))
        elif data.get("type") == "usage":
            usage_data = data.get("data", {})
            usage.actual_model = usage_data.get("model") or usage.actual_model
            round_input = usage_data.get("input_tokens", 0)
            usage.input_tokens += round_input
            usage.output_tokens += usage_data.get("output_tokens", 0)
            usage.last_round_input_tokens = round_input
            usage.has_real_usage = True
            if usage_data.get("gen_tps"):
                usage.backend_gen_tps = usage_data["gen_tps"]
            if usage_data.get("prefill_tps"):
                usage.backend_prefill_tps = usage_data["prefill_tps"]
        elif data.get("type") == "fallback":
            usage.actual_model = data.get("answered_by") or usage.actual_model
            logger.warning(
                "[agent] round %s fell back: %s -> %s",
                req.round_num,
                data.get("selected_model"),
                data.get("answered_by"),
            )
            yield chunk
        elif data.get("type") == "model_actual":
            usage.actual_model = data.get("model") or usage.actual_model
            data["requested_model"] = req.requested_model
            yield sse(data)
        elif "delta" in data:
            if not usage.first_token_received:
                usage.time_to_first_token = time.time() - req.total_start
                usage.first_token_received = True
            if data.get("thinking"):
                result.round_reasoning += data["delta"]
            else:
                result.round_response += data["delta"]
                result.response_text += data["delta"]
            yield chunk
            if (
                req.round_num > 1
                and not self.doc_stream.arg_acc
                and not (req.tool_policy and req.tool_policy.blocks("create_document"))
            ):
                for event in self.doc_stream.handle_fenced_delta(result.round_response):
                    yield sse(event)
        elif data.get("error"):
            err_msg = data.get("error", "unknown")
            logger.error("Agent round %s: stream error: %s", req.round_num, err_msg)
            yield sse({"delta": "\n\n*[Stream error: " + str(err_msg) + "]*"})


@dataclass
class ToolTurnResult:
    total_tool_calls: int
    response_text: str = ""
    tool_results: list = field(default_factory=list)
    tool_result_texts: list = field(default_factory=list)
    tool_events: list = field(default_factory=list)
    budget_hit: bool = False
    awaiting_user: bool = False
    effectful_used: bool = False


@dataclass
class ToolTurnRequest:
    tool_blocks: list
    round_num: int
    total_tool_calls: int
    max_tool_calls: int
    session_id: Optional[str]
    disabled_tools: Set[str]
    tool_policy: Optional[ToolPolicy]
    owner: Optional[str]
    workspace: Optional[str]
    full_response_so_far: str
    effectful_tools: Set[str]
    doc_stream_started: bool
    execute_tool: Callable[..., Awaitable[Any]]
    format_tool_result: Callable[[str, Dict[str, Any]], str]


class ToolTurn:
    def __init__(self, request: ToolTurnRequest) -> None:
        self.request = request
        self.result = ToolTurnResult(total_tool_calls=request.total_tool_calls)

    async def stream(self):
        for event in prestream_document_tool(
            self.request.tool_blocks,
            self.request.round_num,
            self.request.doc_stream_started,
            self.request.tool_policy,
        ):
            yield sse(event)

        for block in self.request.tool_blocks:
            if self._budget_exceeded():
                yield sse({
                    "type": "budget_exceeded",
                    "limit": self.request.max_tool_calls,
                    "used": self.result.total_tool_calls,
                })
                self.result.budget_hit = True
                break

            self.result.total_tool_calls += 1
            async for event in self._run_block(block):
                yield event

    def _budget_exceeded(self) -> bool:
        return (
            self.request.max_tool_calls > 0
            and self.result.total_tool_calls >= self.request.max_tool_calls
        )

    async def _run_block(self, block: ToolBlock):
        is_doc_tool = block.tool_type in _DOCUMENT_TOOLS
        cmd_display = (
            block.content.split("\n")[0].strip()[:80]
            if is_doc_tool
            else block.content.strip()
        )

        if self.request.tool_policy and self.request.tool_policy.blocks(block.tool_type):
            desc = f"{block.tool_type}: BLOCKED"
            result = {
                "error": self.request.tool_policy.reason_for(block.tool_type),
                "exit_code": 1,
                "blocked": True,
            }
            logger.info("Tool blocked before start by policy: %s", block.tool_type)
        else:
            yield sse({"type": "tool_start", "tool": block.tool_type, "command": cmd_display, "round": self.request.round_num})
            progress_q: asyncio.Queue = asyncio.Queue()

            async def push_progress(payload):
                await progress_q.put(payload)

            async def run_tool():
                try:
                    return await self.request.execute_tool(
                        block,
                        session_id=self.request.session_id,
                        disabled_tools=self.request.disabled_tools,
                        tool_policy=self.request.tool_policy,
                        owner=self.request.owner,
                        progress_cb=push_progress,
                        workspace=self.request.workspace,
                    )
                finally:
                    await progress_q.put(None)

            tool_task = asyncio.create_task(run_tool())
            while True:
                event = await progress_q.get()
                if event is None:
                    break
                yield sse({"type": "tool_progress", "tool": block.tool_type, "round": self.request.round_num, **event})
            desc, result = await tool_task

        for event in tool_side_effect_events(block, result):
            yield sse(event)

        question_text = ask_user_response_text(result, self.request.full_response_so_far + self.result.response_text)
        if question_text:
            self.result.response_text += question_text
            yield sse({"delta": question_text})
        if "ask_user" in result:
            yield sse({"type": "ask_user", "data": result["ask_user"]})
        if "plan_update" in result:
            yield sse({"type": "plan_update", "data": result["plan_update"]})

        yield sse(tool_output_event(block, result, cmd_display))
        for event in document_activation_events(block, result):
            yield sse(event)

        link_text = tool_link_text(block, result)
        if link_text:
            self.result.response_text += link_text
            yield sse({"delta": link_text})

        tool_event = {
            "round": self.request.round_num,
            "tool": block.tool_type,
            "command": cmd_display,
            "output": tool_output_text(block.tool_type, result),
            "exit_code": result.get("exit_code"),
        }
        for key in ("image_url", "image_prompt", "image_model", "image_size", "image_quality"):
            if result.get(key):
                tool_event[key] = result[key]
        if result.get("doc_id"):
            tool_event["doc_id"] = result["doc_id"]
            tool_event["doc_title"] = result.get("title", "")
        if result.get("diff"):
            tool_event["diff"] = result["diff"]
        self.result.tool_events.append(tool_event)
        if block.tool_type in self.request.effectful_tools:
            self.result.effectful_used = True

        formatted = self.request.format_tool_result(desc, result)
        self.result.tool_results.append(formatted)
        self.result.tool_result_texts.append(formatted)
        if "ask_user" in result:
            self.result.awaiting_user = True


def add_auto_document_tool(
    tool_blocks: list,
    native_tool_calls: list,
    round_response: str,
    *,
    session_id: Optional[str],
    disabled_tools: Set[str],
) -> tuple[list, List[Dict[str, Any]]]:
    from src.agent_tools import TOOL_TAGS, ToolBlock

    has_doc_tool = any(
        block.tool_type in ("create_document", "update_document")
        for block in tool_blocks
    ) or any(
        call.get("name") in ("create_document", "update_document")
        for call in native_tool_calls
    )
    if has_doc_tool or not session_id or "create_document" in disabled_tools:
        return tool_blocks, []

    for match in re.finditer(r"```(\w*)\n([\s\S]*?)```", round_response):
        lang_tag = match.group(1).lower()
        code_body = match.group(2).strip()
        if code_body.count("\n") < 30 or lang_tag in TOOL_TAGS:
            continue
        lang_map = {"py": "python", "js": "javascript", "ts": "typescript", "": "text"}
        doc_lang = lang_map.get(lang_tag, lang_tag or "text")
        doc_title = f"Code ({doc_lang})"
        logger.info("Auto-created document from %s code block (%s lines)", lang_tag, code_body.count("\n") + 1)
        return (
            list(tool_blocks) + [ToolBlock("create_document", f"{doc_title}\n{doc_lang}\n{code_body}")],
            [
                {"type": "doc_stream_open", "title": doc_title, "language": doc_lang},
                {"type": "doc_stream_delta", "content": code_body},
            ],
        )
    return tool_blocks, []


def prestream_document_tool(
    tool_blocks: list,
    round_num: int,
    doc_stream_started: bool,
    tool_policy: Optional[ToolPolicy],
) -> List[Dict[str, Any]]:
    if doc_stream_started:
        return []
    if round_num == 1:
        for block in tool_blocks:
            if _tool_blocked(tool_policy, block):
                continue
            if block.tool_type == "create_document":
                return []

    for block in tool_blocks:
        if _tool_blocked(tool_policy, block):
            continue
        if block.tool_type == "create_document":
            lines = block.content.strip().split("\n")
            title = lines[0].strip() if lines else "Untitled"
            lang = ""
            content_start = 1
            if len(lines) > 1 and len(lines[1].strip()) < 20 and lines[1].strip().isalpha():
                lang = lines[1].strip()
                content_start = 2
            content = "\n".join(lines[content_start:]) if len(lines) > content_start else ""
            events = [{"type": "doc_stream_open", "title": title, "language": lang}]
            if content:
                events.append({"type": "doc_stream_delta", "content": content})
            return events
        if block.tool_type == "update_document":
            return [
                {"type": "doc_stream_open", "title": "", "language": ""},
                {"type": "doc_stream_delta", "content": block.content.strip()},
            ]
    return []


def tool_side_effect_events(block: ToolBlock, result: Dict[str, Any]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    sources = extract_web_sources(block.tool_type, result)
    if sources is not None:
        events.append({"type": "web_sources", "data": sources})

    is_doc_tool = block.tool_type in _DOCUMENT_TOOLS
    if is_doc_tool and "action" in result:
        if result["action"] == "suggest":
            events.append({
                "type": "doc_suggestions",
                "doc_id": result["doc_id"],
                "suggestions": result["suggestions"],
            })
        else:
            events.append({
                "type": "doc_update",
                "doc_id": result["doc_id"],
                "content": result["content"],
                "version": result["version"],
                "title": result.get("title", ""),
                "language": result.get("language"),
            })
    if "ui_event" in result:
        events.append({"type": "ui_control", "data": result})
    return events


def document_activation_events(block: ToolBlock, result: Dict[str, Any]) -> List[Dict[str, Any]]:
    if block.tool_type in ("create_document", "update_document", "edit_document") and result.get("doc_id"):
        return [{
            "type": "doc_update",
            "doc_id": result["doc_id"],
            "title": result.get("title", ""),
            "language": result.get("language", ""),
            "content": result.get("content", ""),
            "version": result.get("version", 1),
        }]
    return []


def ask_user_response_text(result: Dict[str, Any], full_response: str) -> str:
    if "ask_user" in result:
        question = (result["ask_user"].get("question") or "").strip()
        if question and question not in full_response:
            return ("\n\n" if full_response.strip() else "") + question
    return ""


def tool_link_text(block: ToolBlock, result: Dict[str, Any]) -> str:
    if result.get("research_session_id"):
        return f"\n\n[Open in Deep Research](#research-{result['research_session_id']})\n"
    if result.get("note_id") and block.tool_type == "manage_notes":
        title = (result.get("note_title") or "").strip()
        label = f"View note: {title}" if title else "View note"
        return f"\n\n[{label}](#note-{result['note_id']})\n"
    return ""


def extract_web_sources(tool_type: str, result: Dict[str, Any]) -> Optional[list]:
    if tool_type != "web_search":
        return None
    src_text = result.get("output") or result.get("results") or result.get("stdout") or ""
    if not src_text:
        return None
    marker = "<!-- SOURCES:"
    start = src_text.find(marker)
    if start < 0:
        return None
    end = src_text.find(" -->", start)
    if end < 0:
        return None
    try:
        sources = json.loads(src_text[start + len(marker):end])
    except (json.JSONDecodeError, Exception):
        return None
    clean = src_text[:start].rstrip()
    if "output" in result:
        result["output"] = clean
    elif "results" in result:
        result["results"] = clean
    elif "stdout" in result:
        result["stdout"] = clean
    return sources


def tool_output_text(tool_type: str, result: Dict[str, Any]) -> str:
    if tool_type in _DOCUMENT_TOOLS and "action" in result:
        action = result["action"]
        title = result.get("title", "")
        version = result.get("version", "?")
        if action == "create":
            return f'Document created: "{title}" (v{version})'
        if action == "edit":
            return f'Document edited: "{title}" (v{version}, {result.get("applied", 0)} edit(s))'
        if action == "update":
            return f'Document updated: "{title}" (v{version})'
    if "stdout" in result:
        return (result["stdout"] or result["stderr"] or result.get("error", ""))[:2000]
    if "output" in result:
        return (result["output"] or "")[:2000]
    if "response" in result:
        label = result.get("model", result.get("session_name", "AI"))
        return f"{label}: {result['response']}"[:4000]
    if "content" in result:
        return result["content"][:2000]
    if "results" in result:
        return result["results"][:4000]
    if "session_id" in result and "name" in result:
        return f"Session created: {result['name']} (id: {result['session_id']})"
    if "success" in result:
        return f"Written: {result.get('path', '')}" if result["success"] else f"Error: {result.get('error', '')}"
    if "error" in result:
        return result["error"][:2000]
    return ""


def tool_output_event(block: ToolBlock, result: Dict[str, Any], cmd_display: str) -> Dict[str, Any]:
    data = {
        "type": "tool_output",
        "tool": block.tool_type,
        "command": cmd_display,
        "output": tool_output_text(block.tool_type, result),
        "exit_code": result.get("exit_code"),
    }
    if "ui_event" in result:
        data["ui_event"] = result["ui_event"]
        for key in ("toggle_name", "state", "mode", "model", "endpoint_url", "theme_name", "colors"):
            if key in result:
                data[key] = result[key]
    for key in ("image_url", "image_prompt", "image_model", "image_size", "image_quality"):
        if key in result:
            data[key] = result[key]
    if result.get("images"):
        image = result["images"][0]
        data["screenshot"] = f"data:{image['mimeType']};base64,{image['data']}"
    if "diff" in result:
        data["diff"] = result["diff"]
    return data


def _decode_json_string(raw: str) -> str:
    try:
        return json.loads('"' + raw + '"')
    except Exception:
        return raw


def _decode_partial_json_string(raw: str) -> str:
    try:
        return json.loads('"' + raw + '"')
    except Exception:
        try:
            return json.loads('"' + raw.rstrip("\\") + '"')
        except Exception:
            return (
                raw.replace("\\n", "\n")
                .replace("\\t", "\t")
                .replace('\\"', '"')
                .replace("\\\\", "\\")
            )


def _tool_blocked(tool_policy: Optional[ToolPolicy], block: ToolBlock) -> bool:
    return bool(tool_policy and tool_policy.blocks(block.tool_type))
