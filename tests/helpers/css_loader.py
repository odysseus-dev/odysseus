"""Test helpers for reading CSS entrypoints that use local ``@import`` files."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


_CSS_TOKEN_RE = re.compile(
    r"""
    (?P<comment>/\*.*?\*/)
    |
    (?P<import>
        @import\s+
        (?:
            "(?P<double_quoted>[^"]+)"
            |
            '(?P<single_quoted>[^']+)'
            |
            url\(\s*
                (?:
                    "(?P<url_double_quoted>[^"]+)"
                    |
                    '(?P<url_single_quoted>[^']+)'
                    |
                    (?P<url_bare>[^)\s]+)
                )
            \s*\)
        )
        (?:\s+[^;]+)?
        \s*;
    )
    """,
    re.IGNORECASE | re.DOTALL | re.VERBOSE,
)


def read_css_with_imports(path: str | Path) -> str:
    """Return *path* with relative local ``@import`` rules expanded in order.

    Imports are resolved relative to the file that declares them. URL, data,
    protocol-relative, fragment, and root-relative imports are left unchanged
    because they do not identify a local file relative to the entrypoint.
    """

    return _read_css(Path(path).resolve(), ())


def _read_css(path: Path, import_stack: tuple[Path, ...]) -> str:
    if path in import_stack:
        cycle = " -> ".join(str(item) for item in (*import_stack, path))
        raise ValueError(f"Circular CSS @import: {cycle}")

    source = path.read_text(encoding="utf-8")
    stack = (*import_stack, path)

    def expand(match: re.Match[str]) -> str:
        if match.lastgroup == "comment":
            return match.group(0)

        target = next(
            value
            for value in (
                match.group("double_quoted"),
                match.group("single_quoted"),
                match.group("url_double_quoted"),
                match.group("url_single_quoted"),
                match.group("url_bare"),
            )
            if value is not None
        )
        parsed = urlsplit(target)
        if (
            parsed.scheme
            or parsed.netloc
            or target.startswith(("//", "/", "\\", "#"))
            or not parsed.path
        ):
            return match.group(0)

        imported_path = (path.parent / unquote(parsed.path)).resolve()
        return _read_css(imported_path, stack)

    return _CSS_TOKEN_RE.sub(expand, source)
