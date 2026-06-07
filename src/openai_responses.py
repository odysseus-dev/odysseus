"""OpenAI Responses API adapter (POST /v1/responses).

Pure protocol helpers mapping Odysseus's Chat-Completions-shaped messages to the
Responses wire format (`input`/`output`, named SSE events, flattened tools) and
translating Responses SSE back into the internal stream contract. First used by
Perplexity's Agent API; `max_steps` is its extension (1 = single-shot).
Transport (the HTTP loop) stays in `llm_core`.
"""
import copy
import json
from typing import Dict, List, Optional


def _delta_event(text: str, *, thinking: bool = False) -> str:
    payload = {"delta": text}
    if thinking:
        payload["thinking"] = True
    return f"data: {json.dumps(payload)}\n\n"


def _responses_content_parts(content) -> List[Dict]:
    """OpenAI message content → Responses input parts (text + image)."""
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}]
    if not isinstance(content, list):
        return [{"type": "input_text", "text": "" if content is None else str(content)}]
    parts: List[Dict] = []
    for block in content:
        if not isinstance(block, dict):
            parts.append({"type": "input_text", "text": str(block)})
            continue
        btype = block.get("type")
        if btype == "text":
            parts.append({"type": "input_text", "text": block.get("text", "")})
        elif btype == "image_url":
            url = (block.get("image_url") or {}).get("url", "")
            if url.startswith("data:"):
                try:
                    header, b64_data = url.split(",", 1)
                    media_type = header.split(";")[0].replace("data:", "")
                except (ValueError, IndexError):
                    continue
                parts.append({"type": "input_image",
                              "source": {"type": "base64", "media_type": media_type, "data": b64_data}})
            elif url:
                parts.append({"type": "input_image", "source": {"type": "url", "url": url}})
    return parts


def sanitize_responses_schema(node):
    """Repair schema shapes Perplexity's Responses validator rejects: objects
    need `properties`, typeless leaves need a `type`. Mutates in place.
    """
    if isinstance(node, dict):
        _composition = ("anyOf", "oneOf", "allOf", "$ref", "enum", "const")
        if "properties" in node and "type" not in node:
            node["type"] = "object"
        if node.get("type") == "object" and "properties" not in node:
            node["properties"] = {}
        if ("type" not in node and "properties" not in node
                and not any(k in node for k in _composition)):
            node["type"] = "string"
        for key in ("properties", "$defs", "definitions"):
            sub = node.get(key)
            if isinstance(sub, dict):
                for v in sub.values():
                    sanitize_responses_schema(v)
        for key in ("items", "additionalProperties", "contains", "not"):
            if isinstance(node.get(key), dict):
                sanitize_responses_schema(node[key])
        for key in ("anyOf", "oneOf", "allOf", "prefixItems"):
            if isinstance(node.get(key), list):
                for v in node[key]:
                    sanitize_responses_schema(v)
    elif isinstance(node, list):
        for v in node:
            sanitize_responses_schema(v)
    return node


def responses_tools(tools) -> List[Dict]:
    """Flatten Chat-Completions function tools to the Responses shape
    (deep-copied + sanitized; `strict=False` since our schemas aren't closed).
    """
    out: List[Dict] = []
    for t in tools or []:
        if not isinstance(t, dict) or t.get("type") != "function":
            continue
        fn = t.get("function") or {}
        params = copy.deepcopy(fn.get("parameters")) or {"type": "object", "properties": {}}
        sanitize_responses_schema(params)
        out.append({
            "type": "function",
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "parameters": params,
            "strict": False,
        })
    return out


def build_responses_payload(model, messages, temperature, max_tokens,
                            *, stream=False, tools=None, max_steps=None) -> Dict:
    """Build a Responses request from OpenAI-style messages: system →
    `instructions`, assistant `tool_calls` → `function_call`, role:tool →
    `function_call_output`.
    """
    instructions_parts: List[str] = []
    input_items: List[Dict] = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            instructions_parts.append(m.get("content") or "")
        elif role == "tool":
            input_items.append({
                "type": "function_call_output",
                "call_id": m.get("tool_call_id", ""),
                "output": m.get("content") or "",
            })
        elif role == "assistant" and isinstance(m.get("tool_calls"), list):
            if m.get("content"):
                input_items.append({
                    "type": "message", "role": "assistant",
                    "content": [{"type": "output_text", "text": m["content"]}],
                })
            for tc in m["tool_calls"]:
                fn = tc.get("function") or {}
                input_items.append({
                    "type": "function_call",
                    "call_id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "arguments": fn.get("arguments") or "{}",
                })
        elif role == "assistant":
            text = m.get("content")
            if isinstance(text, list):
                text = "".join(b.get("text", "") for b in text
                               if isinstance(b, dict) and b.get("type") in ("text", "output_text"))
            input_items.append({
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": text or ""}],
            })
        else:
            input_items.append({
                "type": "message", "role": role or "user",
                "content": _responses_content_parts(m.get("content")),
            })

    payload: Dict = {"model": model, "input": input_items}
    instructions = "\n\n".join(p for p in instructions_parts if p)
    if instructions:
        payload["instructions"] = instructions
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens and max_tokens > 0:
        payload["max_output_tokens"] = max_tokens
    if stream:
        payload["stream"] = True
    tools_payload = responses_tools(tools)
    if tools_payload:
        payload["tools"] = tools_payload
    if max_steps is not None:
        payload["max_steps"] = max_steps
    return payload


def parse_responses_output(data: dict) -> str:
    """Concatenate assistant text from a non-streaming `output` array."""
    chunks: List[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "output_text":
                chunks.append(part.get("text") or "")
    return "".join(chunks)


def parse_responses_usage(obj: dict) -> Optional[Dict]:
    """Extract {input_tokens, output_tokens} from a usage block, else None."""
    usage = (obj or {}).get("usage") or {}
    inp = usage.get("input_tokens")
    out = usage.get("output_tokens")
    if inp is None and out is None:
        return None
    return {"input_tokens": inp or 0, "output_tokens": out or 0}


def _responses_calls_from_output(response: dict) -> List[Dict]:
    """Completed function calls from `output` (fallback when args aren't streamed)."""
    calls: List[Dict] = []
    for item in (response or {}).get("output") or []:
        if isinstance(item, dict) and item.get("type") == "function_call":
            calls.append({
                "id": item.get("call_id") or "",
                "name": item.get("name") or "",
                "arguments": item.get("arguments") or "",
            })
    return calls


class ResponsesStreamTranslator:
    """Translate Responses SSE events into Odysseus's internal stream chunks
    (delta / thinking / tool_calls / usage / error / [DONE]).
    """

    def __init__(self):
        self._calls: Dict[str, Dict] = {}   # item_id (fc_…) -> {id(call_id), name, arguments}
        self._order: List[str] = []
        self._reasoning_streamed = False
        self._done = False

    def _slot(self, item_id: str) -> Dict:
        if item_id not in self._calls:
            self._calls[item_id] = {"id": "", "name": "", "arguments": ""}
            self._order.append(item_id)
        return self._calls[item_id]

    def feed(self, ev: dict) -> List[str]:
        etype = ev.get("type", "")
        out: List[str] = []
        if etype == "response.output_text.delta":
            d = ev.get("delta") or ""
            if d:
                out.append(_delta_event(d))
        elif etype in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
            d = ev.get("delta") or ""
            if d:
                self._reasoning_streamed = True
                out.append(_delta_event(d, thinking=True))
        elif etype == "response.output_item.added":
            item = ev.get("item") or {}
            if item.get("type") == "function_call":
                slot = self._slot(ev.get("item_id") or item.get("id") or "")
                slot["id"] = item.get("call_id") or slot["id"]
                slot["name"] = item.get("name") or slot["name"]
        elif etype == "response.function_call_arguments.delta":
            slot = self._slot(ev.get("item_id") or "")
            slot["arguments"] += ev.get("delta") or ""
        elif etype == "response.function_call_arguments.done":
            if ev.get("arguments") is not None:
                self._slot(ev.get("item_id") or "")["arguments"] = ev.get("arguments")
        elif etype == "response.output_item.done":
            item = ev.get("item") or {}
            itype = item.get("type")
            if itype == "function_call":
                slot = self._slot(ev.get("item_id") or item.get("id") or "")
                slot["id"] = item.get("call_id") or slot["id"]
                slot["name"] = item.get("name") or slot["name"]
                if item.get("arguments") is not None:
                    slot["arguments"] = item.get("arguments")
            elif itype == "reasoning" and not self._reasoning_streamed:
                text = self._reasoning_text(item)
                if text:
                    self._reasoning_streamed = True
                    out.append(_delta_event(text, thinking=True))
        elif etype in ("response.completed", "response.incomplete"):
            out.extend(self._finish(ev.get("response") or {}))
        elif etype in ("response.failed", "error") or isinstance(ev.get("error"), dict):
            # Bare {"error": {...}} frame (no top-level type) — surface it, don't swallow.
            out.append(self._error_event(ev))
            self._done = True
        return out

    @staticmethod
    def _reasoning_text(item: dict) -> str:
        parts = item.get("summary") or item.get("content") or []
        return "".join(p.get("text", "") for p in parts if isinstance(p, dict))

    def _finish(self, response: dict) -> List[str]:
        if self._done:
            return []
        self._done = True
        out: List[str] = []
        calls = [
            {"id": self._calls[i]["id"], "name": self._calls[i]["name"], "arguments": self._calls[i]["arguments"]}
            for i in self._order
        ]
        if not calls:
            calls = _responses_calls_from_output(response)
        if calls:
            out.append(f'data: {json.dumps({"type": "tool_calls", "calls": calls})}\n\n')
        usage = parse_responses_usage(response)
        if usage:
            out.append(f'data: {json.dumps({"type": "usage", "data": usage})}\n\n')
        out.append("data: [DONE]\n\n")
        return out

    @staticmethod
    def _error_event(ev: dict) -> str:
        err = (ev.get("response") or {}).get("error") or ev.get("error") or {}
        msg = err.get("message") if isinstance(err, dict) else str(err)
        code = err.get("code") if isinstance(err, dict) else None
        status = code if isinstance(code, int) else 502
        return f'event: error\ndata: {json.dumps({"error": msg or "Responses API error", "status": status})}\n\n'

    def flush(self) -> List[str]:
        """Finish a stream that closed with no explicit completed/failed event."""
        if self._done:
            return []
        return self._finish({})
