"""ChatGPT-subscription (codex) transport — the OpenAI **Responses** wire format.

Pure request/response shaping + a stateful streaming decoder, mirroring the
openai_chat / anthropic_messages transports. Speaks the codex Responses API behind
a ChatGPT Plus/Pro OAuth session (`POST {base}/responses`, `store:false`, low-level
streamed events) rather than the metered chat/completions API. Wire format
validated against open-source codex clients (pi, opencode) running this exact
pattern on remote deployments.

Boundaries (kept deliberately):
  - Credentials (Authorization + chatgpt-account-id) come from `auth.resolve`; this
    transport only adds the wire-format headers (originator / User-Agent / Beta).
  - `store:false` + `include:[reasoning.encrypted_content]` are sent, but the
    encrypted reasoning is NOT persisted/echoed across turns (Odysseus stores
    plain chat history). That costs cross-turn reasoning-cache affinity, not
    correctness. Reasoning round-trip + WebSocket transport are possible follow-ups.
  - `max_tokens` and `temperature` are intentionally OMITTED — the codex Responses
    backend rejects both ("Unsupported parameter"). Reasoning models are governed by
    reasoning effort, not temperature. Mirrors pi / opencode.
"""
import json
import logging
from typing import Dict, List, Optional, Tuple

from src.providers.common import DOC_TOOLS

logger = logging.getLogger(__name__)

# Backend-compatibility headers — mimic the codex CLI (whose public client_id we
# reuse). Maintained by hand against the codex CLI's current values.
ORIGINATOR = "codex_cli_rs"
USER_AGENT = "codex_cli_rs/0.0.0 (Odysseus)"
OPENAI_BETA = "responses=experimental"

# Terminal Responses events — any of these ends the stream.
_TERMINAL_EVENTS = ("response.completed", "response.done", "response.incomplete")


def _text_from_blocks(blocks: List[Dict]) -> str:
    """Join the text of OpenAI-chat multimodal content blocks."""
    return "".join(b.get("text", "") for b in blocks
                   if isinstance(b, dict) and b.get("type") == "text")


def _to_input_content(content) -> List[Dict]:
    """OpenAI-chat user content -> Responses `input_text`/`input_image` parts."""
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}]
    parts: List[Dict] = []
    for b in content or []:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            parts.append({"type": "input_text", "text": b.get("text", "")})
        elif b.get("type") == "image_url":
            url = (b.get("image_url") or {}).get("url", "")
            if url:
                parts.append({"type": "input_image", "detail": "auto", "image_url": url})
    return parts


def _stringify(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return _text_from_blocks(content)
    return "" if content is None else str(content)


def _convert_messages(messages: List[Dict]) -> Tuple[Optional[str], List[Dict]]:
    """OpenAI-chat transcript -> (instructions, Responses `input` items).

    System messages become `instructions` (codex carries the system prompt out of
    band). assistant `tool_calls` -> `function_call`; `role:"tool"` results ->
    `function_call_output`, paired by `call_id`.
    """
    instructions_parts: List[str] = []
    input_items: List[Dict] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "system":
            txt = content if isinstance(content, str) else _text_from_blocks(content or [])
            if txt:
                instructions_parts.append(txt)
        elif role == "user":
            parts = _to_input_content(content)
            if parts:
                input_items.append({"role": "user", "content": parts})
        elif role == "assistant":
            text = content if isinstance(content, str) else _text_from_blocks(content or [])
            if text and text.strip():
                input_items.append({
                    "type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": text, "annotations": []}],
                })
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                args = fn.get("arguments")
                if not isinstance(args, str):
                    args = json.dumps(args or {})
                # `id` (item id) is intentionally omitted: it only matters for the
                # reasoning-item pairing OpenAI runs on stored sessions, which this
                # transport does not use (store:false, no reasoning echo). call_id
                # pairs the result below.
                input_items.append({
                    "type": "function_call", "call_id": tc.get("id", ""),
                    "name": fn.get("name", ""), "arguments": args,
                })
        elif role == "tool":
            input_items.append({
                "type": "function_call_output",
                "call_id": m.get("tool_call_id", ""),
                "output": _stringify(content),
            })
    instructions = "\n\n".join(p for p in instructions_parts if p) or None
    return instructions, input_items


def _convert_tools(tools: Optional[List[Dict]]) -> List[Dict]:
    """OpenAI-chat tools -> Responses function tools (flat, not nested under `function`)."""
    out: List[Dict] = []
    for t in tools or []:
        fn = t.get("function") or {}
        out.append({
            "type": "function",
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters") or {},
            "strict": None,
        })
    return out


class CodexStreamDecoder:
    """Reconstruct Odysseus SSE chunks from low-level codex Responses events.

    Emits the SAME normalized chunk shapes as `OpenAIStreamDecoder` (text `delta`,
    `thinking` delta, `tool_calls`, `tool_call_delta`, `usage`, `[DONE]`) so the
    client and `agent_loop` are provider-agnostic.
    """

    def __init__(self, model: str):
        self._model = model
        self._cur_type: Optional[str] = None      # message | reasoning | function_call
        self._cur_tool: Optional[Dict] = None      # {id, name, arguments}
        self._tool_calls: List[Dict] = []
        self._tool_index = 0
        self._saw_text_delta = False

    # -- chunk emitters (byte-compatible with openai_chat) -------------------
    @staticmethod
    def _text_chunk(text: str) -> str:
        return f'data: {json.dumps({"delta": text})}\n\n'

    @staticmethod
    def _thinking_chunk(text: str) -> str:
        return f'data: {json.dumps({"delta": text, "thinking": True})}\n\n'

    @staticmethod
    def _usage_chunk(usage: Dict) -> str:
        data = {
            "input_tokens": usage.get("input_tokens", 0) or 0,
            "output_tokens": usage.get("output_tokens", 0) or 0,
        }
        return f'data: {json.dumps({"type": "usage", "data": data})}\n\n'

    def _emit_tool_calls(self) -> Optional[str]:
        if not self._tool_calls:
            return None
        return f'data: {json.dumps({"type": "tool_calls", "calls": self._tool_calls})}\n\n'

    @staticmethod
    def _error_chunk(message: str, status: int = 502) -> str:
        return f'event: error\ndata: {json.dumps({"error": str(message), "status": status})}\n\n'

    def finalize(self) -> List[str]:
        out: List[str] = []
        tc = self._emit_tool_calls()
        if tc:
            out.append(tc)
        out.append("data: [DONE]\n\n")
        return out

    def decode_line(self, line: str) -> Tuple[List[str], bool]:
        if not line or not line.startswith("data:"):
            return [], False
        data = line[5:].strip()
        if not data:
            return [], False
        if data == "[DONE]":
            return self.finalize(), True
        try:
            ev = json.loads(data)
        except Exception:
            return [], False

        t = ev.get("type")
        out: List[str] = []
        try:
            if t == "response.output_item.added":
                item = ev.get("item") or {}
                self._cur_type = item.get("type")
                if self._cur_type == "function_call":
                    self._cur_tool = {
                        "id": item.get("call_id") or item.get("id") or "",
                        "name": item.get("name", ""),
                        "arguments": item.get("arguments") or "",
                    }
                elif self._cur_type == "message":
                    self._saw_text_delta = False

            elif t in ("response.output_text.delta", "response.refusal.delta"):
                d = ev.get("delta", "")
                if d:
                    self._saw_text_delta = True
                    out.append(self._text_chunk(d))

            elif t == "response.reasoning_summary_text.delta":
                d = ev.get("delta", "")
                if d:
                    out.append(self._thinking_chunk(d))

            elif t == "response.reasoning_summary_part.done":
                out.append(self._thinking_chunk("\n\n"))

            elif t == "response.function_call_arguments.delta":
                if self._cur_tool is not None:
                    d = ev.get("delta", "")
                    self._cur_tool["arguments"] += d
                    if d and self._cur_tool.get("name") in DOC_TOOLS:
                        out.append(f'data: {json.dumps({"type": "tool_call_delta", "index": self._tool_index, "name": self._cur_tool["name"], "arg_delta": d})}\n\n')

            elif t == "response.function_call_arguments.done":
                if self._cur_tool is not None:
                    args = ev.get("arguments")
                    if args is not None:
                        self._cur_tool["arguments"] = args

            elif t == "response.output_item.done":
                item = ev.get("item") or {}
                it = item.get("type")
                if it == "function_call" and self._cur_tool is not None:
                    if not self._cur_tool["arguments"]:
                        self._cur_tool["arguments"] = item.get("arguments") or ""
                    self._cur_tool["id"] = self._cur_tool["id"] or item.get("call_id") or item.get("id") or ""
                    self._cur_tool["name"] = self._cur_tool["name"] or item.get("name", "")
                    self._tool_calls.append(self._cur_tool)
                    self._cur_tool = None
                    self._tool_index += 1
                elif it == "message" and not self._saw_text_delta:
                    # Backend streamed no deltas — emit the finished text once so we
                    # never drop content (the streaming contract normally sends deltas).
                    text = "".join(
                        p.get("text", "") for p in (item.get("content") or [])
                        if isinstance(p, dict) and p.get("type") == "output_text"
                    )
                    if text:
                        out.append(self._text_chunk(text))
                self._cur_type = None

            elif t in _TERMINAL_EVENTS:
                resp = ev.get("response") or {}
                usage = resp.get("usage")
                if usage:
                    out.append(self._usage_chunk(usage))
                out.extend(self.finalize())
                return out, True

            elif t == "error":
                msg = ev.get("message") or ev.get("code") or "codex stream error"
                return [self._error_chunk(msg)], True

            elif t == "response.failed":
                err = (ev.get("response") or {}).get("error") or {}
                return [self._error_chunk(err.get("message") or "codex response failed")], True

        except Exception as e:
            logger.error(f"Error decoding codex stream event: {e}")
            return out, False
        return out, False


class CodexResponsesTransport:
    id = "codex_responses"

    def target_url(self, url: str) -> str:
        n = (url or "").rstrip("/")
        if n.endswith("/codex/responses"):
            return n
        if n.endswith("/codex"):
            return n + "/responses"
        return n + "/codex/responses"

    def build_payload(self, model, messages, temperature, max_tokens, *, stream=False, tools=None) -> Dict:
        instructions, input_items = _convert_messages(messages)
        payload: Dict = {
            "model": model,
            "store": False,
            "stream": bool(stream),
            "input": input_items,
            "include": ["reasoning.encrypted_content"],
            "tool_choice": "auto",
            "parallel_tool_calls": True,
            "text": {"verbosity": "low"},
        }
        if instructions:
            payload["instructions"] = instructions
        if tools:
            payload["tools"] = _convert_tools(tools)
        # temperature + max_tokens are intentionally omitted — the codex Responses
        # backend rejects both ("Unsupported parameter"). Reasoning models use
        # reasoning effort, not temperature; matches pi / opencode.
        return payload

    def build_headers(self, headers: Optional[Dict]) -> Dict:
        h = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "OpenAI-Beta": OPENAI_BETA,
            "originator": ORIGINATOR,
            "User-Agent": USER_AGENT,
        }
        if headers:
            h.update(headers)   # Authorization + chatgpt-account-id from auth.resolve
        return h

    def parse_response(self, data: Dict) -> str:
        """Extract assistant text from a non-streaming Responses object.

        Streaming is the proven path; non-stream support depends on the backend
        honoring `stream:false` (verified in manual smoke, not unit-provable here).
        """
        if isinstance(data.get("output_text"), str):
            return data["output_text"]
        texts: List[str] = []
        for item in data.get("output") or []:
            if isinstance(item, dict) and item.get("type") == "message":
                for part in item.get("content") or []:
                    if isinstance(part, dict) and part.get("type") == "output_text":
                        texts.append(part.get("text", ""))
        return "".join(texts)

    def stream_decoder(self, model: str) -> CodexStreamDecoder:
        return CodexStreamDecoder(model)
