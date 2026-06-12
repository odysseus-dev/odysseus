# services/tts/markdown_to_speech.py
"""Markdown -> speakable text.

Converts raw assistant markdown into plain prose a TTS engine can read
naturally: no "double star" for **bold**, no URLs, no LaTeX, no code dumps.
Mirrors static/js/ttsText.js — keep the two in sync.
"""

import re

# Thinking markers as emitted by real models — mirrors the normalization in
# static/js/markdown.js (extractThinkingBlocks): <think>/<thinking>/<thought>
# with optional attributes (e.g. <think time="12.3">), Gemma channel markers,
# unclosed openers, orphan closers, and plain "Thinking:" prefixes.
_THINK_OPEN = r"<(?:think(?:ing)?|thought)(?:\s+[^>]*)?>"
_THINK_CLOSE = r"</(?:think(?:ing)?|thought)>"


def _strip_thinking(text: str) -> str:
    # Gemma-style channel markers
    text = re.sub(r"<\|channel>thought\s*\n?[\s\S]*?<channel\|>\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<\|channel>thought\s*\n?[\s\S]*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<\|channel>response\s*\n?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<channel\|>", "", text, flags=re.IGNORECASE)

    # Closed blocks (tags may carry attributes)
    text = re.sub(_THINK_OPEN + r"[\s\S]*?" + _THINK_CLOSE, "", text, flags=re.IGNORECASE)

    # Unclosed opener. Two cases (same policy as markdown.js):
    # (a) stray opener at the very start with no reply before it — some
    #     quantized models emit a literal <think> token and never close it;
    #     strip just the tag and keep the body as the answer.
    # (b) opener after real reply text — truncated thinking; drop from the
    #     tag onward.
    m = re.match(r"\s*" + _THINK_OPEN + r"([\s\S]*)$", text, flags=re.IGNORECASE)
    if m:
        text = m.group(1)
    else:
        text = re.sub(_THINK_OPEN + r"[\s\S]*$", "", text, flags=re.IGNORECASE)

    # Orphan closer with no opening tag — text before it is leaked reasoning
    text = re.sub(r"^[\s\S]*?" + _THINK_CLOSE, "", text, count=1, flags=re.IGNORECASE)
    text = re.sub(_THINK_CLOSE, "", text, flags=re.IGNORECASE)

    # Plain "Thinking:" / "Thinking Process:" prefix with no tags — drop the
    # leading reasoning paragraph when an answer paragraph follows.
    if re.match(r"\s*thinking(?:\s+process)?\s*:", text, flags=re.IGNORECASE):
        parts = re.split(r"\n\s*\n", text, maxsplit=1)
        if len(parts) == 2 and parts[1].strip():
            text = parts[1]

    return text


# Abbreviations TTS engines tend to read literally ("ee gee", "etk").
# Conservative list — titles like Dr./Mr. are already handled well by engines.
_ABBREVIATIONS = [
    (r"\be\.g\.,?", "for example,"),
    (r"\bi\.e\.,?", "that is,"),
    (r"\betc\.", "etcetera."),
    (r"\bvs\.", "versus"),
    (r"\bet al\.", "and others"),
    (r"\bapprox\.", "approximately"),
]


def _naturalize(text: str) -> str:
    """Speech-friendly rewrites applied after markdown stripping: expand
    abbreviations and symbols engines mangle, smooth punctuation that is
    read awkwardly, and drop glyphs that should never be spoken."""
    for pattern, replacement in _ABBREVIATIONS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # Common HTML entities that survive tag stripping
    text = text.replace("&nbsp;", " ").replace("&amp;", " & ")
    text = text.replace("&lt;", " less than ").replace("&gt;", " greater than ")

    # Symbols engines spell out poorly or skip entirely
    text = re.sub(r"(?<=\d)\s*%", " percent", text)
    text = re.sub(r" & ", " and ", text)
    text = re.sub(r"°\s*C\b", " degrees Celsius", text)
    text = re.sub(r"°\s*F\b", " degrees Fahrenheit", text)
    text = re.sub(r"(?<=\d)°", " degrees", text)
    text = re.sub(r"~(?=\d)", "about ", text)
    text = re.sub(r"\s*(?:->|=>|→|⇒)\s*", " to ", text)
    text = re.sub(r"±", " plus or minus ", text)

    # Punctuation smoothing
    text = text.replace("…", "...")
    text = re.sub(r"\s*—\s*", ", ", text)                  # em dash → spoken pause
    text = re.sub(r"(?<=\d)\s*–\s*(?=\d)", " to ", text)   # numeric en-dash range
    text = re.sub(r"\s*–\s*", ", ", text)
    text = re.sub(r"([!?])\1+", r"\1", text)               # "!!" / "??" → single
    text = re.sub(r",\s*,", ",", text)                     # artifacts of the above
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2018", "'").replace("\u2019", "'")

    # snake_case identifiers read better as separate words
    text = re.sub(r"(?<=\w)_(?=\w)", " ", text)

    # Emoji / pictographs / leftover arrows — never spoken
    text = re.sub(r"[\U0001F000-\U0001FAFF\u2190-\u21FF\u2300-\u27BF\u2B00-\u2BFF\uFE0F\u200D]", "", text)

    return text


def markdown_to_speech(src: str) -> str:
    if not src:
        return ""
    text = _strip_thinking(str(src))

    # Fenced code blocks (``` and ~~~), incl. mermaid; drop unclosed trailing fence too
    text = re.sub(r"^[ \t]*(`{3,}|~{3,})[^\n]*\n[\s\S]*?\n[ \t]*\1[ \t]*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[ \t]*(`{3,}|~{3,})[^\n]*\n[\s\S]*$", "", text, flags=re.MULTILINE, count=1)

    # Block math: $$...$$ and \[...\]
    text = re.sub(r"\$\$[\s\S]*?\$\$", "", text)
    text = re.sub(r"\\\[[\s\S]*?\\\]", "", text)
    # Inline math: \(...\) and $...$ (single line, non-greedy)
    text = re.sub(r"\\\([\s\S]*?\\\)", "", text)
    text = re.sub(r"\$(?=\S)[^$\n]*?\S\$", "", text)

    # Images: drop entirely
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)

    # Links: keep the label only
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    # Autolinks / bare URLs
    text = re.sub(r"<https?://[^>]+>", "", text)
    text = re.sub(r"https?://\S+", "", text)

    # Remaining HTML tags
    text = re.sub(r"</?[a-zA-Z][^>]*>", "", text)

    # Tables: separator rows out, data rows -> comma-separated sentence
    text = re.sub(r"^[ \t]*\|?[ \t:|-]+\|[ \t:|-]*$", "", text, flags=re.MULTILINE)

    def _table_row(m: "re.Match[str]") -> str:
        cells = [c.strip() for c in m.group(1).split("|") if c.strip()]
        return ", ".join(cells) + "." if cells else ""

    text = re.sub(r"^[ \t]*\|(.+)\|[ \t]*$", _table_row, text, flags=re.MULTILINE)

    # Headings: keep the title, ensure a sentence-ending pause
    def _heading(m: "re.Match[str]") -> str:
        title = m.group(1)
        return title if re.search(r"[.!?:]$", title) else title + "."

    text = re.sub(r"^[ \t]*#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", _heading, text, flags=re.MULTILINE)

    # Blockquotes
    text = re.sub(r"^[ \t]*>[ \t]?", "", text, flags=re.MULTILINE)

    # Horizontal rules
    text = re.sub(r"^[ \t]*([-*_])[ \t]*(?:\1[ \t]*){2,}$", "", text, flags=re.MULTILINE)

    # Task list checkboxes (before list markers so "- [ ] Task" fully strips)
    text = re.sub(r"^([ \t]*(?:[-*+]|\d{1,3}[.)])[ \t]+)\[[ xX]\][ \t]+", r"\1", text, flags=re.MULTILINE)

    # List markers (bulleted and numbered): keep the content, and end each
    # item with sentence punctuation so engines pause between items instead
    # of running the whole list together.
    def _list_item(m: "re.Match[str]") -> str:
        content = m.group(1).rstrip()
        return content if re.search(r"[.!?:;,]$", content) else content + "."

    text = re.sub(r"^[ \t]*[-*+][ \t]+(.+)$", _list_item, text, flags=re.MULTILINE)
    text = re.sub(r"^[ \t]*\d{1,3}[.)][ \t]+(.+)$", _list_item, text, flags=re.MULTILINE)

    # Emphasis / strikethrough markers (keep the words)
    text = re.sub(r"(\*\*\*|___)(?=\S)([\s\S]*?\S)\1", r"\2", text)
    text = re.sub(r"(\*\*|__)(?=\S)([\s\S]*?\S)\1", r"\2", text)
    text = re.sub(r"(\*|_)(?=\S)([\s\S]*?\S)\1", r"\2", text)
    text = re.sub(r"~~(?=\S)([\s\S]*?\S)~~", r"\1", text)

    # Inline code: strip the backticks, keep the content
    text = re.sub(r"`([^`\n]+)`", r"\1", text)
    text = re.sub(r"`+", "", text)

    # Footnote refs like [^1]
    text = re.sub(r"\[\^[^\]]*\]", "", text)

    # Speech-friendly rewrites (abbreviations, symbols, punctuation)
    text = _naturalize(text)

    # Whitespace cleanup
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()
