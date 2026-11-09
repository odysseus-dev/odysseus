"""Provider-neutral output budgeting and context-error recovery helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from src.model_context import estimate_tokens


_CONTEXT_ERROR_MARKERS = (
    "context length",
    "context window",
    "context size",
    "context_length_exceeded",
    "maximum context",
    "max model len",
    "max_model_len",
    "too many tokens",
    "max_tokens is too large",
    "maximum number of tokens",
)

_CONTEXT_LIMIT_PATTERNS = (
    r"maximum context(?: length| window| size)?(?: is(?: only)?| of|:)?\s*([\d,]+)",
    r"maximum context(?: length| window| size)?\s*\(([\d,]+)\)",
    r"context(?: length| window| size)(?: is(?: only)?| of|:|=)\s*([\d,]+)",
    r"max(?:imum)?[_ ]model[_ ]len(?: is| of|:|=)?\s*([\d,]+)",
)

_INPUT_TOKEN_PATTERNS = (
    r"input length\s*\(([\d,]+)\)",
    r"(?:request has|resulted in|contains?)\s*([\d,]+)\s*(?:input|prompt)?\s*tokens",
    r"([\d,]+)\s*(?:input|prompt)\s*tokens",
    r"([\d,]+)\s*in (?:the )?messages",
)

# Local multimodal servers tokenize an image into visual patch tokens after the
# ordinary chat-message serializer has run.  ``estimate_tokens`` intentionally
# estimates textual chat content and consequently cannot see that cost.  Qwen
# VL uses at least 656 tokens for the small image previews the harness injects;
# reserve a little more per image so a request cannot sit exactly one token
# beyond the provider's context limit after a tool result.
MULTIMODAL_IMAGE_TOKEN_RESERVE = 1024


@dataclass(frozen=True)
class ContextErrorDetails:
    context_limit: Optional[int] = None
    input_tokens: Optional[int] = None
    input_tokens_is_lower_bound: bool = False


@dataclass(frozen=True)
class ContextRecoveryPlan:
    max_tokens: int
    context_limit: Optional[int]
    observed_input_tokens: Optional[int]


def context_safety_margin(context_length: int) -> int:
    """Leave room for provider chat templates and tokenizer estimation error."""

    if context_length <= 0:
        return 256
    # The text/token estimate is intentionally provider-neutral.  vLLM then
    # adds chat-template and multimodal serialization tokens that are not
    # visible to that estimate.  A 32k Qwen-VL request previously reached the
    # provider with ``input + max_tokens == context + 1`` and paid for a full
    # failed request/retry.  Keep a 1k floor for normal large local windows;
    # this is small relative to the window but makes the proactive clamp
    # robust to the observed tokenizer drift.
    return max(256, min(2048, max(1024, int(context_length * 0.02))))


def estimate_tool_schema_tokens(tools: Optional[List[Dict]]) -> int:
    """Estimate the request tokens consumed by native tool definitions."""

    if not tools:
        return 0
    try:
        encoded = json.dumps(tools, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        encoded = str(tools)
    return int(len(encoded) * 0.3) + (8 * len(tools))


def estimate_multimodal_image_tokens(messages: List[Dict]) -> int:
    """Reserve provider-side visual patch tokens for image content blocks.

    This deliberately counts image blocks rather than inspecting image URLs or
    data payloads: providers may resize an image differently, but a conservative
    fixed reserve prevents a text-only estimate from exhausting the entire
    context window before visual tokenization happens upstream.
    """

    image_count = 0
    for message in messages or []:
        content = message.get("content", "") if isinstance(message, dict) else ""
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "").lower() in {
                "image",
                "image_url",
                "input_image",
            }:
                image_count += 1
    return image_count * MULTIMODAL_IMAGE_TOKEN_RESERVE


def estimate_request_tokens(messages: List[Dict], tools: Optional[List[Dict]] = None) -> int:
    return (
        estimate_tokens(messages)
        + estimate_multimodal_image_tokens(messages)
        + estimate_tool_schema_tokens(tools)
    )


def fit_output_token_budget(
    requested_max_tokens: int,
    context_length: int,
    messages: List[Dict],
    tools: Optional[List[Dict]] = None,
    *,
    observed_input_tokens: Optional[int] = None,
) -> int:
    """Clamp a positive output allowance to the remaining context window.

    A non-positive allowance retains its existing provider-default semantics.
    """

    try:
        requested = int(requested_max_tokens or 0)
        context = int(context_length or 0)
    except (TypeError, ValueError):
        return requested_max_tokens
    if requested <= 0 or context <= 0:
        return requested

    if observed_input_tokens is not None and int(observed_input_tokens) >= 0:
        # Provider context errors generally report message tokens but omit the
        # native tool schemas that are serialized alongside them.
        estimated_input = int(observed_input_tokens) + estimate_tool_schema_tokens(tools)
    else:
        estimated_input = estimate_request_tokens(messages, tools)
    available = context - estimated_input - context_safety_margin(context)
    return max(1, min(requested, available))


def parse_context_error(text: str) -> Optional[ContextErrorDetails]:
    """Extract provider-reported context and input counts from an error."""

    value = str(text or "")
    lower = value.lower()
    if not any(marker in lower for marker in _CONTEXT_ERROR_MARKERS):
        return None

    def _first(patterns) -> Optional[int]:
        for pattern in patterns:
            match = re.search(pattern, value, flags=re.IGNORECASE)
            if not match:
                continue
            try:
                parsed = int(match.group(1).replace(",", ""))
            except (TypeError, ValueError):
                continue
            if parsed > 0:
                return parsed
        return None

    lower_bound_input = bool(re.search(
        r"prompt\s+contains?\s+at\s+least\s+[\d,]+\s+(?:input\s+)?tokens",
        value,
        flags=re.IGNORECASE,
    ))
    return ContextErrorDetails(
        context_limit=_first(_CONTEXT_LIMIT_PATTERNS),
        input_tokens=_first(_INPUT_TOKEN_PATTERNS),
        input_tokens_is_lower_bound=lower_bound_input,
    )


def plan_context_recovery(
    error_text: str,
    failed_max_tokens: int,
    messages: List[Dict],
    tools: Optional[List[Dict]] = None,
) -> Optional[ContextRecoveryPlan]:
    """Plan a retry that reduces output or trims provider-proven input overflow."""

    details = parse_context_error(error_text)
    try:
        failed = int(failed_max_tokens or 0)
    except (TypeError, ValueError):
        return None
    if details is None:
        return None

    # Local OpenAI-compatible servers commonly interpret ``0`` as "use the
    # provider default".  An input-only overflow can therefore arrive without
    # a positive client-side output allowance.  It is still recoverable when
    # the server supplied both the observed prompt size and its context cap:
    # reserve a bounded tool-call-sized continuation and let the caller trim
    # the message history before retrying.
    if failed <= 0:
        if (
            details.context_limit is None
            or details.input_tokens is None
            or details.input_tokens < details.context_limit
        ):
            return None
        return ContextRecoveryPlan(
            max_tokens=1024,
            context_limit=details.context_limit,
            observed_input_tokens=details.input_tokens,
        )

    if failed == 1:
        # At the minimum generation allowance, only a provider-proven prompt
        # overflow can recover. The stream wrapper bounds this deeper trim.
        if (
            details.context_limit is None
            or details.input_tokens is None
            or details.input_tokens < details.context_limit
        ):
            return None
        return ContextRecoveryPlan(
            max_tokens=1,
            context_limit=details.context_limit,
            observed_input_tokens=details.input_tokens,
        )

    reduced = min(failed - 1, max(1, failed // 2))
    if details.input_tokens_is_lower_bound:
        # Some vLLM-compatible servers report ``context - max_tokens + 1`` as
        # "prompt contains at least N". That is a rejection threshold, not an
        # observed tokenizer count, so a single half-size retry can fail again
        # with a different derived N. Use a bounded tool-call-sized allowance.
        reduced = min(reduced, 1024)
    if details.context_limit:
        reduced = min(
            reduced,
            fit_output_token_budget(
                failed,
                details.context_limit,
                messages,
                tools,
                observed_input_tokens=(
                    None
                    if details.input_tokens_is_lower_bound
                    else details.input_tokens
                ),
            ),
        )
    if reduced >= failed:
        return None
    return ContextRecoveryPlan(
        max_tokens=max(1, reduced),
        context_limit=details.context_limit,
        observed_input_tokens=details.input_tokens,
    )
