"""factory_continuation — auto-continuation for truncated producer output.

Some LLM endpoints cap output tokens per request and return a document cut
mid-construct (e.g. deepseek-v4-pro truncating a long HTML/CSS file). The
Factory producer would then be rejected by the reviewer 3x and stall in
human_intervention.

This module detects such truncation from the returned STRING ONLY (no
finish_reason plumbing required — keeps the shared llm_core untouched) and,
when detected, sends continuation turns that are stitched back together.

It is deliberately decoupled from factory_orchestrator: callers inject the
LLM callable, so this module has no dependency on the orchestrator or
llm_core and can be unit-tested in isolation.

Detection philosophy
--------------------
Truncation almost always removes CLOSERS (the file ends before it can close
the current block). So every structural check uses the conservative
"openers > closers" direction. The opposite imbalance (extra closers) is
NOT flagged, because that is the false-positive direction and a
false-positive continuation can corrupt a complete file by appending
unwanted content. The refusal + repetition guards below provide additional
defense-in-depth.
"""

from __future__ import annotations

import logging
import re
from typing import Awaitable, Callable, List

logger = logging.getLogger(__name__)

# Max auto-continue rounds after the initial produce call.
MAX_CONTINUATIONS = 4

CONTINUATION_PROMPT = (
    "Continue EXACTLY from where the previous output stopped. "
    "Do NOT repeat any text already produced. Do NOT add explanations or apologies. "
    "Output ONLY the remaining content of the file, continuing the same code/markup verbatim."
)

# Short natural-language responses that mean "I have nothing more to add".
# Only matched against SHORT chunks so real code is never mistaken for a refusal.
_REFUSAL_RE = re.compile(
    r"\b(already\s+complete|is\s+complete|fully\s+complete|nothing\s+(more|else)|"
    r"no\s+more|end\s+of\s+(the\s+)?(file|document|output)|that'?s\s+all|"
    r"i\s+have\s+(already\s+)?provided|the\s+(complete\s+)?file\s+(is\s+)?(above|provided|done)|"
    r"file\s+is\s+already)\b",
    re.IGNORECASE,
)

# A trailing unclosed tag, e.g. ...<a href="#contact" class="btn btn-prima
_TRAILING_OPEN_TAG_RE = re.compile(r"<[a-zA-Z0-9!/][^\n>]*$")


# ═══════════════════════════════════════════════════════════════════════
# Language families + noise strippers
# ═══════════════════════════════════════════════════════════════════════

# Brace-delimited (C-style) — complete file has balanced { }.
_CSS_SUFFIXES = (".css", ".scss", ".sass", ".less")
_BRACE_SUFFIXES = (
    ".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx", ".json", ".json5",
    ".java", ".scala", ".sc", ".kt", ".kts", ".gradle", ".groovy",
    ".cs", ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".m", ".mm",
    ".go", ".rs", ".swift", ".dart", ".php", ".phtml",
)
# Pythonic — indentation-based; group with () [] {} and triple-quoted strings.
_PYTHONIC_SUFFIXES = (".py", ".pyw", ".pyi", ".gd", ".cfg_py", ".svelte_script")
# Keyword-terminated: blocks closed by `end` (ruby/lua) or `fi/esac/done` (shell).
_END_RUBY_SUFFIX = ".rb"
_LUA_SUFFIXES = (".lua", ".luau")
_SHELL_SUFFIXES = (".sh", ".bash", ".zsh", ".ksh", ".fish")
# Markup
_MARKUP_HTML_SUFFIXES = (".html", ".htm", ".xhtml")
# SQL
_SQL_SUFFIXES = (".sql", ".psql", ".plsql")

# CSS/SCSS noise: block comments + quoted strings (so '}' inside them is ignored).
_CSS_NOISE_RE = re.compile(
    r'/\*.*?\*/'
    r'|"(?:\\.|[^"\\])*"'
    r"|'(?:\\.|[^'\\])*'",
    re.DOTALL,
)
# JS/TS/C#/C/C++/Go/Rust/PHP noise: line + block comments + strings + templates.
# Regex literals intentionally NOT stripped (ambiguous with division) — JS brace
# counts are therefore advisory, which the conservative direction handles safely.
_JS_NOISE_RE = re.compile(
    r'//[^\n]*'
    r'|/\*.*?\*/'
    r'|"(?:\\.|[^"\\\n])*"'
    r"|'(?:\\.|[^'\\\n])*'"
    r'|`(?:\\.|[^`\\])*`',
    re.DOTALL,
)
# Python/GDScript noise: # comments, triple-quoted strings, normal strings.
_PY_NOISE_RE = re.compile(
    r'#[^\n]*'
    r'|(?:[rbf]*"""[\s\S]*?""")'
    r"|(?:[rbf]*'''[\s\S]*?''')"
    r'|(?:[rbf]*"(?:\\.|[^"\\\n])*")'
    r"|(?:[rbf]*'(?:\\.|[^'\\\n])*')",
)
# Ruby / Shell noise: # comments + quoted strings.
_HASH_NOISE_RE = re.compile(
    r'#[^\n]*'
    r'|"(?:\\.|[^"\\\n])*"'
    r"|'(?:\\.|[^'\\\n])*'",
    re.DOTALL,
)
# Lua noise: -- line comments, --[[ block ]] comments, strings.
_LUA_NOISE_RE = re.compile(
    r'--\[\[.*?\]\]'
    r'|--[^\n]*'
    r'|"(?:\\.|[^"\\\n])*"'
    r"|'(?:\\.|[^'\\\n])*'"
    r'|\[\[[\s\S]*?\]\]',
    re.DOTALL,
)
# SQL noise: -- and /* */ comments, single/double quoted strings.
_SQL_NOISE_RE = re.compile(
    r'--[^\n]*'
    r'|/\*.*?\*/'
    r"|'(?:''|[^'])*'"
    r'|"(?:""|[^"])*"',
    re.DOTALL,
)


def _balance(stripped: str, open_ch: str, close_ch: str) -> int:
    """openers minus closers (>0 means openers exceed closers)."""
    return stripped.count(open_ch) - stripped.count(close_ch)


def _brace_imbalance(s: str, family: str) -> int:
    """'{ 'count minus '}'count after stripping language noise."""
    stripped = (_CSS_NOISE_RE if family == "css" else _JS_NOISE_RE).sub("", s)
    return _balance(stripped, "{", "}")


def _py_unclosed(s: str) -> bool:
    # 1) Unclosed triple-quoted string. Check on raw text first — counting
    #    markers here is reliable because complete docstrings pair up, and
    #    doing it before stripping avoids the string-stripper consuming the
    #    first two quotes of an unclosed triple as an empty "".
    no_esc = re.sub(r"\\.", "", s)
    if no_esc.count('"""') % 2 != 0 or no_esc.count("'''") % 2 != 0:
        return True
    # 2) Unbalanced grouping brackets () [] {} (spans across lines when truncated).
    stripped = _PY_NOISE_RE.sub("", s)
    for o, c in (("(", ")"), ("[", "]"), ("{", "}")):
        if stripped.count(o) != stripped.count(c):
            return True
    return False


def _endblock_unclosed(s: str, noise_re, open_words, close_words) -> bool:
    """Generic keyword-terminated check: count opening keywords vs closing keywords.

    openers > closers => truncation removed the closers.
    """
    stripped = noise_re.sub("", s)
    openers = sum(len(re.findall(rf"\b{w}\b", stripped)) for w in open_words)
    closers = sum(len(re.findall(rf"\b{w}\b", stripped)) for w in close_words)
    return openers > closers


def _lua_unclosed(s: str) -> bool:
    stripped = _LUA_NOISE_RE.sub("", s)
    openers = len(re.findall(r"\b(function|if|for|while|do|repeat)\b", stripped))
    closers = len(re.findall(r"\bend\b", stripped)) + len(re.findall(r"\buntil\b", stripped))
    return openers > closers


def _shell_unclosed(s: str) -> bool:
    stripped = _HASH_NOISE_RE.sub("", s)
    openers = (len(re.findall(r"\b(if|case|for|while|until)\b", stripped))
               + stripped.count("{") + stripped.count("("))
    closers = (len(re.findall(r"\b(fi|esac|done)\b", stripped))
               + stripped.count("}") + stripped.count(")"))
    return openers > closers


def _sql_unclosed(s: str) -> bool:
    stripped = _SQL_NOISE_RE.sub("", s)
    openers = len(re.findall(r"\bBEGIN\b", stripped, re.IGNORECASE))
    closers = len(re.findall(r"\bEND\b", stripped, re.IGNORECASE))
    if openers > closers:
        return True
    # Parenthesis spans (subqueries / function args) cut across truncation.
    return _balance(stripped, "(", ")") > 0


def _looks_truncated_lang(s: str, fl: str) -> bool:
    """Language-specific structural checks. Conservative — only flags the
    truncation direction (openers > closers)."""
    low = s.lower()
    # Markup: must contain the document close.
    if fl.endswith(_MARKUP_HTML_SUFFIXES):
        return "</html>" not in low
    if fl.endswith((".svg",)):
        return "</svg>" not in low
    if fl.endswith(".vue"):
        # SFC: needs </template>; script block must be brace-balanced.
        if "</template>" not in low:
            return True
        return _brace_imbalance(s, "js") > 0
    if fl.endswith(_CSS_SUFFIXES):
        return _brace_imbalance(s, "css") > 0
    if fl.endswith(_BRACE_SUFFIXES):
        return _brace_imbalance(s, "js") > 0
    if fl.endswith(_PYTHONIC_SUFFIXES):
        return _py_unclosed(s)
    if fl.endswith(_END_RUBY_SUFFIX):
        # Ruby blocks closed by `end`: do/def/class/module/case/begin always pair.
        return _endblock_unclosed(s, _HASH_NOISE_RE,
                                  ("do", "def", "class", "module", "case", "begin"),
                                  ("end",))
    if fl.endswith(_LUA_SUFFIXES):
        return _lua_unclosed(s)
    if fl.endswith(_SHELL_SUFFIXES):
        return _shell_unclosed(s)
    if fl.endswith(_SQL_SUFFIXES):
        return _sql_unclosed(s)
    return False  # unknown language — fall back to general signals only


# ═══════════════════════════════════════════════════════════════════════
# Public detection API
# ═══════════════════════════════════════════════════════════════════════

def looks_truncated(text: str, fname: str = "") -> bool:
    """Heuristic: does this producer output look structurally incomplete?

    Conservative — only flags clear truncation signals so we don't trigger
    spurious continuations on already-complete output (a false-positive
    continuation could append unwanted content and corrupt a complete file).
    """
    if not text:
        return False
    s = text.rstrip()
    fl = (fname or "").lower()
    # ── General signals (all languages) ──
    if s.count("```") % 2 != 0:          # unbalanced markdown code fences
        return True
    if _TRAILING_OPEN_TAG_RE.search(s):  # trailing "<tag..." (markup cut)
        return True
    tail_low = s[-200:].lower()
    if s.endswith(("...", "\u2026")) or "[truncat" in tail_low:
        return True
    # ── Language-specific structural checks ──
    return _looks_truncated_lang(s, fl)


def is_refusal(chunk: str) -> bool:
    """True if a continuation response is natural-language 'nothing more' rather
    than code/markup. Used to abort a false-positive continuation WITHOUT
    appending prose to the output (which would corrupt the file).

    Only triggers on SHORT responses that match a refusal pattern — a long
    chunk is always treated as content even if it happens to contain those
    words.
    """
    if not chunk or not chunk.strip():
        return True
    if len(chunk) > 200:
        return False
    return bool(_REFUSAL_RE.search(chunk))


def _is_repetition(output: str, chunk: str) -> bool:
    """True if a continuation chunk appears to REPEAT the tail of the existing
    output (model ignored the 'do not repeat' instruction). Used to abort
    without appending, so a false-positive continuation can't corrupt the file
    by duplicating content.
    """
    if not chunk or not output:
        return False
    head = chunk.lstrip()[:60]
    if len(head) < 12:
        return False
    return head in output[-400:]


# Callable: messages -> awaited content string
LlmCall = Callable[[List[dict]], Awaitable[str]]


async def produce_with_continuation(
    system_prompt: str,
    user_prompt: str,
    llm_call: LlmCall,
    fname: str = "",
    max_continuations: int = MAX_CONTINUATIONS,
) -> str:
    """Produce output, auto-continuing if it comes back truncated.

    Args:
        system_prompt: the agent system prompt.
        user_prompt: the composed task prompt.
        llm_call: async callable taking a messages list and returning content.
        fname: target filename (drives language-specific truncation heuristics).
        max_continuations: cap on continuation rounds.

    Returns:
        The (possibly stitched) output string.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    chunk = await llm_call(messages)
    output = chunk or ""
    conversation = list(messages)

    rounds = 0
    while output and looks_truncated(output, fname) and rounds < max_continuations:
        rounds += 1
        logger.info("Factory continuation: output looks truncated (len=%d); "
                    "sending round %d/%d", len(output), rounds, max_continuations)
        conversation = conversation + [
            {"role": "assistant", "content": chunk},
            {"role": "user", "content": CONTINUATION_PROMPT},
        ]
        try:
            chunk = await llm_call(conversation)
        except Exception as e:
            logger.warning("Factory continuation: round %d failed (%s); "
                           "using output accumulated so far", rounds, e)
            break

        if is_refusal(chunk):
            logger.info("Factory continuation: model returned a refusal/complete "
                        "response; stopping (no append).")
            break

        if _is_repetition(output, chunk):
            logger.info("Factory continuation: model repeated existing output "
                        "(len=%d); stopping to avoid duplication.", len(chunk))
            break

        output = output + chunk

    if rounds:
        logger.info("Factory continuation: completed after %d round(s); final len=%d",
                    rounds, len(output))
    return output
