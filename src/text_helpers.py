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

_THINK_TAG_NAME = r"(?:think(?:ing)?|thought)"

# Think-tag matchers. `[^<>]` (not `[^>]`) bounds attribute scans at the next
# `<` so an opener flood with no closing `>` can't backtrack to end-of-string
# (ReDoS, CodeQL py/polynomial-redos); capture is identical for well-formed tags.
# Opener/closer are split for the forward-only block strip (_sub_delimited).
_THINK_OPEN_TAG_RE = re.compile(rf"<{_THINK_TAG_NAME}(?:\s[^<>]*)?>", re.IGNORECASE)
_THINK_CLOSE_TAG_RE = re.compile(rf"</{_THINK_TAG_NAME}>\s*", re.IGNORECASE)
# Orphan opening/closing tags left after the block strip.
_THINK_TAG_RE = re.compile(rf"</?{_THINK_TAG_NAME}[^<>]*>\s*", re.IGNORECASE)
# Dangling opener with no closer: strip from `<think>` to end of string.
_THINK_OPEN_RE = re.compile(rf"<{_THINK_TAG_NAME}(?:\s[^<>]*)?>[\s\S]*$", re.IGNORECASE)
# Normalize `<thinking time="0.42">`-style attributes to a plain `<think>`.
_THINK_ATTR_RE = re.compile(rf"<{_THINK_TAG_NAME}\s[^<>]*>", re.IGNORECASE)
_THINK_ATTR_CLOSE_RE = re.compile(rf"</{_THINK_TAG_NAME}\s[^<>]*>", re.IGNORECASE)
_GEMMA_THOUGHT_OPEN_RE = re.compile(r"<\|channel>thought\s*\n?[\s\S]*$", re.IGNORECASE)
_GEMMA_RESPONSE_OPEN_RE = re.compile(r"<\|channel>response\s*\n?", re.IGNORECASE)
_GEMMA_CHANNEL_CLOSE_RE = re.compile(r"<channel\|>", re.IGNORECASE)
_THOUGHT_TAG_OPEN_RE = re.compile(r"<thought(\s[^<>]*)?>", re.IGNORECASE)
_THOUGHT_TAG_CLOSE_RE = re.compile(r"</thought>", re.IGNORECASE)
# Gemma thought-channel delimiters, split for the forward-only sub (_sub_delimited).
_GEMMA_THOUGHT_CHANNEL_OPEN_RE = re.compile(r"<\|channel>thought\s*\n?", re.IGNORECASE)
_GEMMA_CHANNEL_CLOSE_TRIM_RE = re.compile(r"<channel\|>\s*", re.IGNORECASE)
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


def _sub_delimited(text, open_re, close_re, repl):
    """Forward-only ``re.sub`` of ``open_re...close_re`` that can't ReDoS.

    Pairs each opener with the first closer after it and stops once no closer is
    reachable, so it stays O(n) instead of re.sub's rescan-to-end from every
    opener (O(n^2) on "many openers, no closer" input). ``repl`` gets the inner
    text. A whole-string "closer present?" guard is not enough: a stale closer
    before an opener flood keeps it true while every opener still rescans.
    """
    out = []
    pos = 0
    while True:
        om = open_re.search(text, pos)
        if om is None:
            break
        cm = close_re.search(text, om.end())
        if cm is None:
            break
        out.append(text[pos:om.start()])
        out.append(repl(text[om.end():cm.start()]))
        pos = cm.end()
    out.append(text[pos:])
    return "".join(out)


def normalize_thinking_markup(text: str) -> str:
    """Canonicalize supported thinking wrappers to `<think>` markup.

    The chat UI and persistence layer already understand `<think>...</think>`.
    Gemma 4 may instead emit `<|channel>thought\n...<channel|>`, and some
    gateways/models emit `<thought>...</thought>`. Normalize those shapes into
    the existing representation and strip empty thought channels.
    """
    if not text:
        return text
    out = _THOUGHT_TAG_OPEN_RE.sub(lambda m: "<think" + (m.group(1) or "") + ">", text)
    out = _THOUGHT_TAG_CLOSE_RE.sub("</think>", out)

    def _replace_gemma_thought(inner: str) -> str:
        thought = inner.strip()
        return f"<think>{thought}</think>\n" if thought else ""

    # Forward-only so a stale/unreachable `<channel|>` can't drive a ReDoS rescan.
    out = _sub_delimited(
        out, _GEMMA_THOUGHT_CHANNEL_OPEN_RE, _GEMMA_CHANNEL_CLOSE_TRIM_RE, _replace_gemma_thought
    )
    out = _sub_delimited(
        out, _GEMMA_RESPONSE_OPEN_RE, _GEMMA_CHANNEL_CLOSE_RE, lambda inner: inner
    )
    out = _GEMMA_RESPONSE_OPEN_RE.sub("", out)
    out = _GEMMA_CHANNEL_CLOSE_RE.sub("", out)
    return out


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
      * closed `<think>...</think>` (any depth, plus `<thinking>`/`<thought>`)
      * dangling unclosed `<think>...` / `<thought>...`
      * stray opener/closer tags
      * `<think time="0.42">`-style attributes
      * Gemma 4 `<|channel>thought...<channel|>` wrappers
    """
    if not text:
        return ""
    # Gemma 4 thinking-capable models use channel control tokens rather than
    # XML tags when the runtime does not split reasoning into a separate field.
    # The thought channel can be empty in non-thinking mode; either way it is
    # not user-facing content. A response channel, when present, is only a
    # wrapper around the final answer.
    text = normalize_thinking_markup(text)
    text = _GEMMA_THOUGHT_OPEN_RE.sub("", text)
    # Normalize attributes so the closed/open regexes can catch them.
    text = _THINK_ATTR_RE.sub("<think>", text)
    text = _THINK_ATTR_CLOSE_RE.sub("</think>", text)
    # Forward-only block strip (see _sub_delimited): one pass collapses nested
    # and sequential blocks without the old lazy re.sub loop's ReDoS rescan.
    out = _sub_delimited(text, _THINK_OPEN_TAG_RE, _THINK_CLOSE_TAG_RE, lambda _inner: "")
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
