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


def strip_code_fences(text: str) -> str:
    """Strip markdown code fences wrapping the entire output.

    Models often wrap file content in ```language ... ``` despite instructions
    not to. This removes a single outer fence if present, preserving inner
    content (e.g. embedded code blocks inside a markdown file).
    """
    if not text:
        return text
    s = text.strip()
    # Match opening fence: ```lang\n ... ``` at the very start and end
    # Only strip if the fence wraps the ENTIRE output (not embedded blocks).
    if not s.startswith("```"):
        return text
    lines = s.split("\n")
    if len(lines) < 2:
        return text
    # First line is ```lang — strip it
    # Last non-empty line must be ``` — strip it
    first = lines[0].strip()
    if not first.startswith("```"):
        return text
    # Find the last ``` line
    last_idx = len(lines) - 1
    while last_idx > 0 and not lines[last_idx].strip():
        last_idx -= 1
    if lines[last_idx].strip() != "```":
        return text  # no closing fence at the end — don't strip
    inner = "\n".join(lines[1:last_idx])
    return inner


# Max auto-continue rounds after the initial produce call.
MAX_CONTINUATIONS = 8

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

# Output that ends mid-word (a-zA-Z0-9_) without a newline — strong truncation signal.
# Covers cases where the model was cut off mid-token, e.g. "export function init(" or
# "const state = getSta" that wouldn't be caught by brace/count imbalance alone.
_END_MID_WORD_RE = re.compile(r'[a-zA-Z0-9_]$')


def _ends_mid_construct(s: str) -> bool:
    """True if output ends with an alphanumeric character (likely cut mid-word)
    AND there's no trailing newline. Conservative — only triggers when the
    trailing content looks like it was interrupted, not when output naturally
    ends with an identifier."""
    if not s:
        return False
    clean = s.rstrip()
    if not clean:
        return False
    # Must end with an identifier character
    if not _END_MID_WORD_RE.search(clean):
        return False
    # Must NOT end with } ) ] ; " ' ` — these are natural line endings
    if clean[-1] in '})];"\'`':
        return False
    # Must NOT end with a common terminal pattern (comment close, etc.)
    # If it ends with alphanumeric AND has no trailing newline, it was likely cut
    return not s.endswith('\n')


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
    # Markup: document must END with the close tag (not just contain it
    # somewhere — a premature </html> mid-document is a false negative).
    if fl.endswith(_MARKUP_HTML_SUFFIXES):
        return not re.search(r'</html>\s*$', low)
    if fl.endswith((".svg",)):
        return not re.search(r'</svg>\s*$', low)
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
    if _ends_mid_construct(s):           # cut off mid-word
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


def _continuation_prompt_for_round(output: str, fname: str, rounds: int) -> str:
    """Build a continuation prompt — increasingly explicit on later rounds.

    Shows the tail of the output on ALL rounds so the model knows EXACTLY where
    to resume, plus forceful anti-repetition language and a language-specific
    hint about what closing structure is missing.
    """
    tail = output.rstrip()[-400:]
    fl = (fname or "").lower()
    missing_hint = ""
    if fl.endswith((".html", ".htm", ".xhtml")):
        missing_hint = (" The document must include ALL remaining sections and end "
                        "with </body></html>.")
    elif fl.endswith((".css", ".scss", ".sass", ".less")):
        missing_hint = " All CSS rules must be properly closed with }."
    elif fl.endswith((".py", ".gd")):
        missing_hint = " All functions and classes must be complete."
    elif fl.endswith((".js", ".ts", ".jsx", ".tsx")):
        missing_hint = " All functions and blocks must be properly closed with }."
    if rounds <= 1:
        base = CONTINUATION_PROMPT
    else:
        base = (
            "The file is STILL INCOMPLETE — it has not been finished yet."
        )
    return (
        f"{base}\n\n"
        f"The output so far ends with EXACTLY this text:\n\n"
        f">>>{tail}<<<\n\n"
        f"Write ONLY what comes IMMEDIATELY AFTER the text above. "
        f"DO NOT start over. DO NOT repeat ANY content. "
        f"DO NOT include any text that already appears above. "
        f"Start with the exact next character that should follow."
        f"{missing_hint}"
    )


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
        prompt = _continuation_prompt_for_round(output, fname, rounds)
        logger.info("Factory continuation: output looks truncated (len=%d); "
                    "sending round %d/%d", len(output), rounds, max_continuations)
        conversation = conversation + [
            {"role": "assistant", "content": chunk or ""},
            {"role": "user", "content": prompt},
        ]
        # Cap conversation size: after round 2, trim intermediate rounds to
        # prevent context window exhaustion (each round's accumulated output
        # eats into the available output token budget for the next round).
        # Keep: system + original user + last assistant chunk + current continuation prompt.
        if rounds >= 2 and len(conversation) > 5:
            conversation = conversation[:2] + conversation[-2:]
        try:
            chunk = await llm_call(conversation)
        except Exception as e:
            logger.warning("Factory continuation: round %d failed (%s); "
                           "using output accumulated so far", rounds, e)
            break

        if is_refusal(chunk):
            if not looks_truncated(output, fname):
                logger.info("Factory continuation: model confirms complete; stopping.")
                break
            # Model refused but output is STILL truncated — the refusal is wrong.
            # Replace the last user prompt with an even more explicit version and
            # retry WITHOUT consuming another round. The model never sees its own
            # refusal in the conversation.
            logger.warning("Factory continuation: model refused on round %d but output "
                           "still truncated (len=%d); retrying with explicit close prompt",
                           rounds, len(output))
            explicit = _continuation_prompt_for_round(output, fname, rounds + 2)
            if conversation and conversation[-1].get("role") == "user":
                conversation[-1]["content"] = explicit
            try:
                chunk = await llm_call(conversation)
            except Exception as e:
                logger.warning("Factory continuation: explicit retry failed (%s)", e)
                break
            if not chunk or is_refusal(chunk) or _is_repetition(output, chunk):
                logger.warning("Factory continuation: explicit retry also refused/empty; "
                               "giving up with %d bytes", len(output))
                break
            output = output + chunk
            continue

        # Stalled progress: chunk is too short to complete the file and output
        # is still truncated. The model returned minimal content without
        # refusing. Retry with an explicit prompt instead of wasting the round.
        if chunk and len(chunk.strip()) < 200 and looks_truncated(output + chunk, fname) \
                and rounds < max_continuations:
            logger.warning("Factory continuation: round %d produced only %d bytes and output "
                           "still truncated; retrying with explicit prompt",
                           rounds, len(chunk.strip()))
            explicit = _continuation_prompt_for_round(output, fname, rounds + 2)
            if conversation and conversation[-1].get("role") == "user":
                conversation[-1]["content"] = explicit
            try:
                retry_chunk = await llm_call(conversation)
            except Exception as e:
                logger.warning("Factory continuation: stalled retry failed (%s)", e)
                break
            if not retry_chunk or is_refusal(retry_chunk) or _is_repetition(output, retry_chunk):
                logger.warning("Factory continuation: stalled retry also failed; "
                               "accepting %d-byte chunk", len(chunk))
            else:
                chunk = retry_chunk

        if _is_repetition(output, chunk):
            if not looks_truncated(output, fname):
                logger.info("Factory continuation: model repeated, output complete; stopping.")
                break
            # Output still truncated but model repeated — retry with explicit prompt
            logger.warning("Factory continuation: round %d model repeated but output still "
                           "truncated (len=%d); retrying with explicit prompt",
                           rounds, len(output))
            explicit = _continuation_prompt_for_round(output, fname, rounds + 2)
            if conversation and conversation[-1].get("role") == "user":
                conversation[-1]["content"] = explicit
            try:
                retry_chunk = await llm_call(conversation)
            except Exception as e:
                logger.warning("Factory continuation: repetition retry failed (%s)", e)
                break
            if not retry_chunk or is_refusal(retry_chunk) or _is_repetition(output, retry_chunk):
                logger.warning("Factory continuation: repetition retry also failed; "
                               "giving up with %d bytes", len(output))
                break
            chunk = retry_chunk

        output = output + chunk

    if rounds:
        logger.info("Factory continuation: completed after %d round(s); final len=%d",
                    rounds, len(output))
    return output
