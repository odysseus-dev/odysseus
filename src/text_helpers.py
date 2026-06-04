"""Text-cleanup helpers shared across LLM-output paths.

Single source of truth for `<think>`-tag stripping, Qwen-style "Thinking
Process" blocks, and the soft "reasoning prose" heuristic that catches
chain-of-thought leaks from models that don't tag their reasoning.

Before this module, six different files (`email_routes.py`,
`chat_helpers.py`, `note_routes.py`, `builtin_actions.py`, `research_utils.py`,
`agent_loop.py`) each had their own variant of the same regex. They all
broke in slightly different ways on the edges (unclosed `<think>`, nested
tags, model emitting `<thinking>` instead of `<think>`).
"""

from __future__ import annotations

import re

# Closed reasoning blocks. Multi-pass loop in `strip_think` handles nested
# `<think><think>...</think></think>` patterns some models emit.
_THINK_CLOSED_RE = re.compile(r"<think(?:ing)?>[\s\S]*?</think(?:ing)?>\s*", re.IGNORECASE)
# Orphan opening or closing tags that survive after the closed-pass.
_THINK_TAG_RE = re.compile(r"</?think(?:ing)?[^>]*>\s*", re.IGNORECASE)
# Dangling opener anywhere in the response with no closer — strip everything
# from `<think>` to the end of string.
_THINK_OPEN_RE = re.compile(r"<think(?:ing)?>[\s\S]*$", re.IGNORECASE)
# Streaming models occasionally emit `<thinking time="0.42">`-style attributes.
# Normalize to a plain `<think>` so the regexes above catch them.
_THINK_ATTR_RE = re.compile(r"<think(?:ing)?\s+[^>]*>", re.IGNORECASE)
_THINK_ATTR_CLOSE_RE = re.compile(r"</think(?:ing)?\s+[^>]*>", re.IGNORECASE)
# Qwen and a few other models prefix the response with a "Thinking Process:"
# block before the real answer.
_QWEN_THINKING_RE = re.compile(
    r"^Thinking Process:.*?(?=\n\n#|\n\n\*\*|\Z)",
    re.IGNORECASE | re.DOTALL,
)
# Leaked prompt-echo headers (a few models replay the request before answering).
_PROMPT_ECHO_RES = (
    re.compile(r"^The user asks:.*?(?=\n\n#|\n\n\*\*[A-Z]|\Z)", re.DOTALL),
    re.compile(r"^We need to.*?(?=\n\n#|\n\n\*\*[A-Z]|\Z)", re.DOTALL),
)

# Aggressive heuristic for untagged reasoning prose (models that don't wrap
# CoT in `<think>` tags). Only applied as opt-in (`prose=True`) because it
# false-positives on legit user content like "Looking at the attached file…".
_REASONING_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"the user (?:wants|is|asks|needs|wrote|said|told|messaged|requested)|"
    r"i (?:need|should|have|'ll|will|am going)(?: to)? (?:write|draft|reply|respond|read|check|look|review|consider|think|provide|generate|produce|craft|compose|acknowledge|summarize|answer|give|keep|aim|make|address|focus|use|just|simply|analyze|format|create|build|note|decide)|"
    r"let me (?:think|look|see|check|read|review|consider|draft|write|analyze|format|summarize|create|produce|craft|note|extract|identify|figure)|"
    r"looking at (?:the|this|that)|"
    r"(?:okay|alright|hmm|right|so|well|first|next|now)[,.]?\s+(?:the|i|let|so|now|this|here)|"
    r"based on (?:the|this|what|context)|"
    r"to (?:draft|write|reply|respond|summarize|answer)"
    r")\b",
    re.IGNORECASE,
)


def _strip_reasoning_prose(text: str) -> str:
    if not text or not text.strip():
        return text
    paragraphs = re.split(r"\n\s*\n", text.strip())
    if len(paragraphs) <= 1:
        return text
    # Strip only a LEADING contiguous run of reasoning paragraphs. Keeping the
    # text after the *last* reasoning paragraph destroyed the real answer when a
    # reasoning-style sentence trailed it: keep became empty and the function
    # returned that trailing sentence instead of the answer above it.
    first_keep = 0
    for i, p in enumerate(paragraphs):
        if _REASONING_PREFIX_RE.match(p):
            first_keep = i + 1
        else:
            break
    if first_keep == 0:
        return text
    keep = paragraphs[first_keep:]
    return "\n\n".join(keep).strip() if keep else text


def strip_think(text: str, *, prose: bool = False, prompt_echo: bool = True) -> str:
    """Strip `<think>` blocks from model output.

    Args:
      prose: also strip untagged "reasoning prose" paragraphs. Risky on user
        content (false-positives on phrases like "Looking at the attached
        file…"); only enable for short LLM-only outputs and only when a
        `<think>` tag was actually present in the input — callers can use
        the `had_think` semantics by passing `prose=True` only when they
        know the input is LLM-only.
      prompt_echo: also strip Qwen "Thinking Process:" blocks and
        "The user asks:" / "We need to" leaked prompt echoes.

    Robust to:
      * closed `<think>...</think>` (any depth, both `<think>` and `<thinking>`)
      * dangling unclosed `<think>...`
      * stray opener/closer tags
      * `<think time="0.42">`-style attributes
    """
    if not text:
        return ""
    # Normalize attributes so the closed/open regexes can catch them.
    text = _THINK_ATTR_RE.sub("<think>", text)
    text = _THINK_ATTR_CLOSE_RE.sub("</think>", text)
    # Multi-pass for nested blocks.
    prev = None
    out = text
    while prev != out:
        prev = out
        out = _THINK_CLOSED_RE.sub("", out)
    out = _THINK_OPEN_RE.sub("", out)
    out = _THINK_TAG_RE.sub("", out)
    if prompt_echo:
        out = _QWEN_THINKING_RE.sub("", out)
        for _re in _PROMPT_ECHO_RES:
            out = _re.sub("", out)
    if prose:
        out = _strip_reasoning_prose(out)
    return out.strip()


# Back-compat alias for the deep-research code path. Keeps existing imports
# from `src.research_utils` working while delegating to the central impl.
def strip_thinking(text: str) -> str:
    return strip_think(text or "", prose=False, prompt_echo=True)
