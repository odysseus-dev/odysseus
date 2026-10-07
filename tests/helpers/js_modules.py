"""Read a split JS subpackage the way the module graph does.

``static/js/emailLibrary.js`` is a re-export wrapper; the implementation lives
in ``static/js/emailLibrary/``. A test that asserts on email-library behaviour
has to look at every module in that package, because reading one file ties the
test to whichever module a function happens to sit in today — it goes red the
next time something moves without any behaviour changing.

That is the mistake the stylesheet split made, which is why
``tests/helpers/stylesheets.py`` exists. This is the same helper for JS.

Order is deterministic: the entry module first, then the rest alphabetically.
Tests that assert "A appears before B" are asserting about one module's source,
not about the package, so the concatenation order only has to be stable.
"""

from __future__ import annotations

import re
from pathlib import Path

_STATIC_JS = Path(__file__).resolve().parents[2] / "static" / "js"

EMAIL_LIBRARY_WRAPPER = _STATIC_JS / "emailLibrary.js"
EMAIL_LIBRARY_PACKAGE = _STATIC_JS / "emailLibrary"
EMAIL_LIBRARY_ENTRY = EMAIL_LIBRARY_PACKAGE / "index.js"


def _package_paths(package: Path, entry: Path) -> list[Path]:
    if not entry.is_file():
        raise AssertionError(f"missing package entry module: {entry}")
    rest = sorted(p for p in package.glob("*.js") if p != entry)
    return [entry, *rest]


def email_library_paths(include_wrapper: bool = False) -> list[Path]:
    """Every module of the email-library package, entry module first.

    ``include_wrapper`` adds the compatibility file at the old top-level path.
    Leave it off for assertions about implementation code: the wrapper holds
    only an ``export … from`` list.
    """
    paths = _package_paths(EMAIL_LIBRARY_PACKAGE, EMAIL_LIBRARY_ENTRY)
    return [EMAIL_LIBRARY_WRAPPER, *paths] if include_wrapper else paths


def email_library_source(include_wrapper: bool = False) -> str:
    """The whole email-library package as one string."""
    return "\n".join(
        p.read_text(encoding="utf-8") for p in email_library_paths(include_wrapper)
    )


def js_function_source(name: str, source: str | None = None) -> str:
    """One top-level JS function, from its signature to its closing brace.

    Two things this does not do, on purpose.

    It does not slice between a signature and a marker further down ("from
    ``_toggleCardPreview`` to the ``Wrap a probable signature`` comment"). That
    is what a split breaks: the marker ends up in another module, the slice runs
    past the end of the function without failing, and the assertions keep
    passing against the wrong text.

    It does not balance braces by walking characters either. The obvious version
    of that walker treats the apostrophe in a ``// that's a scroll`` comment as
    an open quote and swallows every brace until the next one, which ends the
    function early — silently, again.

    Instead it uses the invariant the file actually holds: a top-level
    declaration starts at column 0, so its closing brace is the next lone ``}``
    at column 0.
    """
    text = email_library_source() if source is None else source
    signature = re.compile(
        r"^(?:export\s+)?(?:async\s+)?function\s+" + re.escape(name) + r"\s*\(",
        re.M,
    )
    match = signature.search(text)
    assert match, f"no top-level declaration of {name}"
    closing = re.compile(r"^\}", re.M).search(text, match.end())
    assert closing, f"unterminated function {name}"
    return text[match.start():closing.end()]
