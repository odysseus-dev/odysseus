"""Read the document editor's JavaScript the way the browser loads it.

``static/js/document.js`` is being decomposed. It stays the entry point the
browser requests -- ``static/index.html`` names it, ``static/sw.js`` precaches
it, and five modules import it -- but the implementation moves into modules
under ``static/js/document/``. The implementation set is the entry plus that
directory.

Two habits in the existing tests do not survive that move, and this module
exists to replace both.

**Reading the entry file alone.** A membership assertion against
``document.js`` silently covers less the moment the behaviour it names moves
out. Use :func:`document_source` for those: it is the whole implementation set,
so a test keeps finding what it asserts on wherever the code lands.

**Slicing between two adjacent functions.** ``function_body("a")`` means "the region between a and b", which is only
the body of ``a`` while ``a`` and ``b`` happen to be neighbours in one file.
After a split they may sit in different modules, and then the slice runs to the
end of the concatenation and quietly grows: an ``assert "x" in region`` passes
against code it was never meant to see. Several of these also hard-code the
entry file's two-space indentation (``"\\n  function showDocTabMenu"``), which
no extracted module reproduces. Use :func:`function_body` or
:func:`declaration` instead -- they find the construct by name, in whichever
module defines it, and end at its real closing brace.
"""

from __future__ import annotations

import re
from pathlib import Path

_STATIC = Path(__file__).resolve().parents[2] / "static"
_ENTRY = _STATIC / "js" / "document.js"

# Extracted implementation modules get one home, so the set is discoverable
# without a manifest anyone has to remember to update.
_IMPL_DIR = _STATIC / "js" / "document"


def document_source_paths() -> list[Path]:
    """Every file holding document-editor implementation, entry first.

    The entry comes first so a concatenation reads in the order the browser
    evaluates the graph's root; the rest are sorted for determinism.
    """
    if not _ENTRY.is_file():
        raise AssertionError(f"document editor entry point is missing: {_ENTRY}")
    extracted = sorted(_IMPL_DIR.rglob("*.js")) if _IMPL_DIR.is_dir() else []
    return [_ENTRY, *extracted]


def document_source() -> str:
    """The whole implementation set as one string, entry first.

    For membership assertions (``assert "..." in document_source()``). For
    anything positional use :func:`function_body` or :func:`declaration`.
    """
    return "\n".join(p.read_text(encoding="utf-8") for p in document_source_paths())


# --- Locating a construct by name, not by what follows it ------------------

def _defining_source(pattern: re.Pattern[str], what: str) -> tuple[str, int]:
    """The source text that defines ``what``, and the offset of the match."""
    hits = []
    for path in document_source_paths():
        src = path.read_text(encoding="utf-8")
        for m in pattern.finditer(src):
            hits.append((path, src, m.start()))
    if not hits:
        raise AssertionError(f"{what} is not defined anywhere in {_describe_set()}")
    if len(hits) > 1:
        where = ", ".join(
            f"{p.relative_to(_STATIC.parent)}:{s.count(chr(10), 0, o) + 1}"
            for p, s, o in hits
        )
        raise AssertionError(f"{what} is defined more than once ({where})")
    _path, src, offset = hits[0]
    return src, offset


def _describe_set() -> str:
    return ", ".join(str(p.relative_to(_STATIC.parent)) for p in document_source_paths())


def function_body(name: str) -> str:
    """The full text of function ``name``, signature through closing brace.

    Matches ``function name``, optionally prefixed by ``export`` and/or
    ``async``, at any indentation, in whichever module of the implementation
    set defines it. The end is found by matching braces rather than by naming
    whatever declaration follows, so moving the function -- or the one after
    it -- does not change the region a test sees.
    """
    pattern = re.compile(
        r"^[ \t]*(?:export\s+)?(?:async\s+)?function\s+" + re.escape(name) + r"\s*\(",
        re.M,
    )
    src, offset = _defining_source(pattern, f"function {name}")
    # Skip the parameter list before looking for the body. A destructured
    # parameter -- `function f(table, { headerRow, headerColumn })` -- opens a
    # brace that is not the body, and matching it would return the signature
    # alone.
    body_start = _end_of_params(src, src.index("(", offset))
    return src[offset : _end_of_block(src, body_start)]


def declaration(name: str) -> str:
    """The full text of a top-level ``const``/``let``/``var`` named ``name``.

    For the array and object tables the tests assert on (toolbar groups, slash
    commands, input rules). Ends at the declaration's closing bracket or brace,
    or at the end of the statement for a simple initialiser.
    """
    pattern = re.compile(
        r"^[ \t]*(?:export\s+)?(?:const|let|var)\s+" + re.escape(name) + r"\b",
        re.M,
    )
    src, offset = _defining_source(pattern, f"declaration {name}")
    return src[offset : _end_of_statement(src, offset)]


# --- A brace matcher that is not fooled by braces inside literals ----------
#
# `document.js` is full of template literals building DOM, regexes containing
# braces, and apostrophes inside comments. Counting raw `{`/`}` mis-slices on
# all three, so the scan tracks what kind of text it is inside.

# After one of these, `/` starts a regex literal; after a value it is division.
_REGEX_OK_BEFORE = re.compile(r"[({\[,;:=!&|?+\-*~^%<>]\s*$|\b(?:return|typeof|case|in|of|new|delete|void|do|else|yield|await)\s*$")


def _scan(src: str, start: int, stop):
    """Walk ``src`` from ``start``, skipping literals and comments.

    Calls ``stop(index, depth_delta_applied)``-free: instead it yields
    ``(index, char)`` for code positions only, so callers can track nesting.
    """
    i, n = start, len(src)
    # Stack of template-literal depths: entering `${` pushes brace depth.
    template_stack: list[int] = []
    while i < n:
        c = src[i]
        two = src[i : i + 2]
        if two == "//":
            j = src.find("\n", i)
            i = n if j == -1 else j + 1
            continue
        if two == "/*":
            j = src.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if c in "'\"":
            i = _skip_quoted(src, i, c)
            continue
        if c == "`":
            i += 1
            i, entered = _skip_template(src, i)
            if entered:
                template_stack.append(0)
            continue
        if c == "/" and _REGEX_OK_BEFORE.search(src[max(0, i - 24) : i]):
            j = _skip_regex(src, i)
            if j is not None:
                i = j
                continue
        if template_stack:
            # Inside `${ ... }`: a `}` that closes it returns to template text.
            if c == "{":
                template_stack[-1] += 1
            elif c == "}":
                if template_stack[-1] == 0:
                    template_stack.pop()
                    i += 1
                    i, entered = _skip_template(src, i)
                    if entered:
                        template_stack.append(0)
                    continue
                template_stack[-1] -= 1
        yield i, c
        i += 1


def _skip_quoted(src: str, i: int, quote: str) -> int:
    i += 1
    n = len(src)
    while i < n:
        if src[i] == "\\":
            i += 2
            continue
        if src[i] == quote:
            return i + 1
        if src[i] == "\n":  # unterminated; do not run away
            return i
        i += 1
    return n


def _skip_template(src: str, i: int) -> tuple[int, bool]:
    """From inside template text, advance to the backtick end or a ``${``.

    Returns the new index and whether an interpolation was entered.
    """
    n = len(src)
    while i < n:
        if src[i] == "\\":
            i += 2
            continue
        if src[i] == "`":
            return i + 1, False
        if src[i : i + 2] == "${":
            return i + 2, True
        i += 1
    return n, False


def _skip_regex(src: str, i: int) -> int | None:
    """Past a regex literal starting at ``i``, or None if it is not one."""
    i += 1
    n = len(src)
    in_class = False
    while i < n:
        c = src[i]
        if c == "\\":
            i += 2
            continue
        if c == "\n":
            return None
        if in_class:
            if c == "]":
                in_class = False
        elif c == "[":
            in_class = True
        elif c == "/":
            i += 1
            while i < n and src[i].isalpha():  # flags
                i += 1
            return i
        i += 1
    return None


def _end_of_block(src: str, start: int) -> int:
    """Index just past the ``}`` closing the first ``{`` at or after ``start``."""
    depth = 0
    seen = False
    for i, c in _scan(src, start, None):
        if c == "{":
            depth += 1
            seen = True
        elif c == "}":
            depth -= 1
            if seen and depth == 0:
                return i + 1
    raise AssertionError(f"unbalanced braces from offset {start}")


def _end_of_statement(src: str, start: int) -> int:
    """Index just past the end of the declaration statement at ``start``.

    Ends on the ``;`` or newline that closes it at nesting depth zero, so an
    array or object initialiser is returned whole.
    """
    depth = 0
    for i, c in _scan(src, start, None):
        if c in "{[(":
            depth += 1
        elif c in "}])":
            depth -= 1
        elif depth == 0 and c == ";":
            return i + 1
        elif depth == 0 and c == "\n" and i > start:
            return i
    return len(src)


def _end_of_params(src: str, open_paren: int) -> int:
    """Index just past the ``)`` closing the parameter list at ``open_paren``."""
    depth = 0
    for i, c in _scan(src, open_paren, None):
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    raise AssertionError(f"unbalanced parameter list at offset {open_paren}")
