"""
Acqua OS -- Claude CLI provider
================================
Routes LLM calls through the ``claude`` CLI subprocess instead of the
Anthropic REST API.  Uses Micah's Claude Max subscription (zero API cost,
zero API key required).

Configure an endpoint in the Acqua OS UI:
  URL:    claude-cli://local
  Model:  claude-sonnet-4-5   (or any Claude model you have access to)
  API Key: (leave blank)

Billing guardrail: if ANTHROPIC_API_KEY is present in the environment,
this module raises an error and refuses to run -- preventing any accidental
paid-API charges.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from typing import AsyncIterator, Dict, List

logger = logging.getLogger(__name__)

# ---- Constants ---------------------------------------------------------------

# The endpoint URL that triggers this provider in Odysseus endpoint config.
CLAUDE_CLI_URL = "claude-cli://local"

# Model list served to the UI when this endpoint is selected.
CLAUDE_CLI_MODELS: List[str] = [
    "claude-sonnet-4-5",
    "claude-opus-4",
    "claude-haiku-4",
    "claude-sonnet-4-5-20250929",
    "claude-opus-4-20250514",
    "claude-haiku-4-20250514",
]

# Default CLI timeout (seconds).  Override with CLAUDE_CLI_TIMEOUT env var.
_CLI_TIMEOUT: int = int(os.environ.get("CLAUDE_CLI_TIMEOUT", "120"))

# ---- Helpers -----------------------------------------------------------------

def _guardrail() -> None:
    """Refuse to proceed if ANTHROPIC_API_KEY is set in the environment.

    Having that variable set is the only way to accidentally start billing
    against the paid REST API.  If someone sets it, we hard-fail here rather
    than silently ignore it.
    """
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        msg = (
            "ANTHROPIC_API_KEY is set in the environment. "
            "Acqua OS uses the Claude CLI (subscription quota), NOT the paid API. "
            "Unset ANTHROPIC_API_KEY to proceed."
        )
        logger.error(msg)
        raise RuntimeError(msg)


def _extract_system_and_user(messages: List[Dict]) -> "tuple[str, str]":
    """Pull system prompt and last user message out of an OpenAI-style list.

    Returns (system_prompt, user_message).  Multimodal content blocks are
    flattened to text.  The *last* user message is used (matches how Odysseus
    calls the upstream for most single-turn completions).
    """
    system_parts: List[str] = []
    user_msg: str = ""
    for m in messages:
        role = m.get("role", "")
        content = m.get("content") or ""
        if isinstance(content, list):
            # Flatten multimodal blocks -- extract text parts only
            content = " ".join(
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        if role == "system":
            system_parts.append(content)
        elif role == "user":
            user_msg = content  # keep the last user message
    return "\n\n".join(system_parts), user_msg


# ---- Sync call ---------------------------------------------------------------

def call_claude_cli(messages: List[Dict], model: str = "claude-sonnet-4-5") -> str:
    """Synchronous Claude CLI call.  Blocks until the response is complete.

    Args:
        messages: OpenAI-style message list (system / user / assistant roles).
        model:    Claude model slug, e.g. ``"claude-sonnet-4-5"``.

    Returns:
        The assistant's response text.

    Raises:
        RuntimeError: on timeout, non-zero exit, or CLI error response.
    """
    _guardrail()
    system_prompt, user_message = _extract_system_and_user(messages)
    if not user_message:
        raise ValueError("No user message found in messages list")

    args = ["claude", "--print", "--model", model, "--output-format", "json"]
    if system_prompt:
        args += ["--append-system-prompt", system_prompt]

    logger.debug("claude-cli [sync]: %s", " ".join(args[:4]))
    try:
        result = subprocess.run(
            args,
            input=user_message,
            capture_output=True,
            text=True,
            timeout=_CLI_TIMEOUT,
            shell=True,  # needed on Windows where claude is claude.cmd
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"claude CLI timed out after {_CLI_TIMEOUT}s")

    if result.returncode != 0:
        raise RuntimeError(
            f"claude CLI exited {result.returncode}: {result.stderr[:500]}"
        )

    try:
        wrapper = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Failed to parse claude CLI JSON output: {exc}\n"
            f"stdout head: {result.stdout[:300]}"
        )

    if wrapper.get("is_error"):
        raise RuntimeError(f"claude CLI returned error: {wrapper.get('result', '')}")

    text = (wrapper.get("result") or "").strip()
    cost = wrapper.get("total_cost_usd") or 0
    usage = wrapper.get("usage") or {}
    logger.debug(
        "claude-cli [sync]: done -- cost=$%.4f  in=%d  out=%d",
        cost,
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
    )
    return text


# ---- Async call --------------------------------------------------------------

async def call_claude_cli_async(
    messages: List[Dict], model: str = "claude-sonnet-4-5"
) -> str:
    """Async Claude CLI call.

    Runs the synchronous ``call_claude_cli`` in a thread pool so that the
    event loop stays unblocked during the subprocess wait.  This also keeps
    Windows compatibility -- ``claude`` is usually ``claude.cmd`` on Windows,
    which only works with ``shell=True``; the sync call already handles that.

    Args:
        messages: OpenAI-style message list.
        model:    Claude model slug.

    Returns:
        The assistant's response text.
    """
    # asyncio.to_thread requires Python 3.9+; fall back to run_in_executor
    try:
        return await asyncio.to_thread(call_claude_cli, messages, model=model)
    except AttributeError:
        loop = asyncio.get_event_loop()
        import functools
        return await loop.run_in_executor(
            None, functools.partial(call_claude_cli, messages, model=model)
        )


# ---- Streaming (pseudo-stream) -----------------------------------------------

async def stream_claude_cli(
    messages: List[Dict], model: str = "claude-sonnet-4-5"
) -> AsyncIterator[str]:
    """Yield Odysseus-compatible SSE chunks from a Claude CLI response.

    The CLI does not stream natively -- we run the full call and emit the
    result paragraph-by-paragraph so the UI renders progressively rather than
    waiting for one giant chunk.

    Yields:
        SSE strings: ``data: {"delta": "..."}\\n\\n`` or
        ``event: error\\ndata: {...}\\n\\n`` or
        ``data: [DONE]\\n\\n``.
    """
    try:
        text = await call_claude_cli_async(messages, model=model)
    except RuntimeError as exc:
        yield f'event: error\ndata: {json.dumps({"error": str(exc), "status": 502})}\n\n'
        return

    # Yield paragraph by paragraph for a progressive-render feel
    paragraphs = text.split("\n\n")
    for i, para in enumerate(paragraphs):
        if not para:
            continue
        chunk = para if i == 0 else "\n\n" + para
        yield f'data: {json.dumps({"delta": chunk})}\n\n'

    yield "data: [DONE]\n\n"
