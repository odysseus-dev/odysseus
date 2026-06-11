"""
turn_judge.py — LLM-based turn evaluation, replacing brittle regex matching.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.llm_core import stream_llm

logger = logging.getLogger(__name__)

JUDGE_PROMPT = (
    "You are evaluating a single round of an AI agent. "
    "Given the assistant's response and tool output below, decide "
    "if this round FAILED.\n\n"
    "FAILURE indicators:\n"
    "- Tool errors (Traceback, SyntaxError, non-zero exit code)\n"
    "- Empty or stalling response (\"I couldn't...\" without trying)\n"
    "- Repeated same tool call with the same args\n"
    "- Hallucinated paths, commands, or entities\n"
    "- Clear regression (broke something that worked)\n\n"
    "SUCCESS indicators:\n"
    "- Tools ran cleanly\n"
    "- Progress toward the user's request\n"
    "- Properly handled expected edge cases\n\n"
    "Reply with EXACTLY this JSON on a single line: "
    '{"failure":true/false,"reason":"...","severity":"none/low/medium/high"}'
)


async def evaluate_turn(
    assistant_response: str,
    tool_output_text: str,
    model: str | None = None,
    endpoint_url: str | None = None,
) -> dict[str, Any]:
    """Evaluate a turn using an LLM judge.

    Returns {"failure": bool, "reason": str, "severity": str}
    Falls back to regex scanning when the LLM output can't be parsed.
    """
    prompt = (
        "--- Assistant ---\n"
        + (assistant_response or "")[:1500]
        + "\n--- Tool output ---\n"
        + (tool_output_text or "")[:2500]
    )

    messages = [
        {"role": "system", "content": JUDGE_PROMPT},
        {"role": "user", "content": prompt},
    ]

    schemas = [
        {
            "type": "function",
            "function": {
                "name": "evaluate",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "failure": {"type": "boolean"},
                        "reason": {"type": "string"},
                        "severity": {
                            "type": "string",
                            "enum": ["none", "low", "medium", "high"],
                        },
                    },
                    "required": ["failure", "reason", "severity"],
                },
            },
        }
    ]

    if not endpoint_url or not model:
        return _fallback_eval(assistant_response, tool_output_text)

    text = ""
    try:
        async for chunk in stream_llm(
            endpoint_url,
            model,
            messages,
            tools=schemas,
        ):
            text += _extract_stream_text(chunk)
    except Exception as exc:
        logger.info("turn judge unavailable, using fallback: %s", exc)
        return _fallback_eval(assistant_response, tool_output_text)

    result = _try_parse_json(text)
    if result and "failure" in result:
        return _normalize(result)

    return _fallback_eval(assistant_response, tool_output_text)


def _fallback_eval(assistant_response: str, tool_output_text: str) -> dict[str, Any]:
    """Cheap local fallback when the judge model is unavailable or unparsable."""
    combined = ((assistant_response or "") + "\n" + (tool_output_text or "")).lower()
    for kw in [
        "traceback",
        "syntaxerror",
        "exit code: 1",
        "exception",
        "filenotfound",
        "importerror",
    ]:
        if kw in combined:
            return {
                "failure": True,
                "reason": "Fallback: matched '" + kw + "'",
                "severity": "medium",
            }
    return {
        "failure": False,
        "reason": "Fallback: no error pattern detected",
        "severity": "none",
    }


def _extract_stream_text(chunk: Any) -> str:
    """Extract text or function-call arguments from stream_llm SSE chunks."""
    if chunk is None:
        return ""
    if not isinstance(chunk, str):
        return str(chunk)
    out = []
    for line in chunk.splitlines():
        line = line.strip()
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            out.append(payload)
            continue
        if isinstance(data, dict):
            if isinstance(data.get("delta"), str):
                out.append(data["delta"])
            if data.get("type") == "tool_calls":
                for call in data.get("calls") or []:
                    if call.get("name") != "evaluate":
                        continue
                    args = call.get("arguments") or ""
                    out.append(args if isinstance(args, str) else json.dumps(args))
    return "".join(out)


def _try_parse_json(text: str) -> dict | None:
    """Extract structured JSON from model output."""
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    if "```" in text:
        for block in text.split("```"):
            block = block.strip()
            if block.startswith("{"):
                try:
                    return json.loads(block)
                except json.JSONDecodeError:
                    pass
    try:
        start = text.index("{")
        end = text.rindex("}")
        return json.loads(text[start : end + 1])
    except (ValueError, json.JSONDecodeError):
        pass
    return None


def _normalize(result: dict) -> dict[str, Any]:
    sev = result.get("severity", "none")
    if sev not in ("none", "low", "medium", "high"):
        sev = "none"
    return {
        "failure": bool(result.get("failure", False)),
        "reason": str(result.get("reason", "")),
        "severity": sev,
    }
