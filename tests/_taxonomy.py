"""Conservative test taxonomy: classify test files by area and sub-area.

This module is the single source of truth for the collection-time markers added
in ``tests/conftest.py``. It performs no inference beyond simple, exact matching
of filename tokens against small, explicit keyword sets. A file is matched to
the first area (in priority order) whose keyword set intersects its filename
tokens; files that match no area fall back to ``uncategorized`` with the
filename itself as the sub-area.

The categories mirror ``tests/TESTING_STANDARD.md``. This module imports nothing
from the application - only the standard library - and changes no test behavior.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# Area keyword sets. Keep these small and explicit; prefer leaving a file
# ``uncategorized`` over guessing. Matching is exact, token-by-token.
SECURITY_KEYWORDS = frozenset({
    "security", "auth", "owner", "scope",
    "ssrf", "xss", "confinement", "permission", "redaction",
})
CLI_KEYWORDS = frozenset({"cli"})
ROUTES_KEYWORDS = frozenset({"route", "routes", "api"})
SERVICES_KEYWORDS = frozenset({
    "llm", "provider", "cookbook", "session", "history", "email",
    "calendar", "memory", "gallery", "document", "research", "mcp",
    "scheduler", "webhook", "embedding",
})
UNIT_KEYWORDS = frozenset({
    "parse", "parser", "parsing", "nonstring", "nondict",
    "atomic", "regex", "tokenize",
})

# Keyword-matched areas, in priority order (first match wins). Security is a
# cross-cutting concern and intentionally outranks the feature areas, so e.g.
# ``test_email_owner_scope.py`` classifies as ``security``, not ``services``.
# ``js`` and ``helpers`` are matched by dedicated rules in ``_match_area``.
KEYWORD_AREAS = (
    ("security", SECURITY_KEYWORDS),
    ("cli", CLI_KEYWORDS),
    ("routes", ROUTES_KEYWORDS),
    ("services", SERVICES_KEYWORDS),
    ("unit", UNIT_KEYWORDS),
)

# File extensions that indicate a JavaScript/Node-backed test.
JS_EXTENSIONS = frozenset({".js", ".mjs", ".ts"})

UNCATEGORIZED = "uncategorized"


@dataclass(frozen=True)
class TestClassification:
    """Area and sub-area for a single test file."""

    area: str
    sub_area: str


def normalize_marker_name(value: str) -> str:
    """Lowercase ``value`` and reduce it to a marker-safe ``[a-z0-9_]`` token."""
    lowered = value.lower()
    collapsed = re.sub(r"[^a-z0-9]+", "_", lowered)
    return collapsed.strip("_")


def _stem(path: str | Path) -> str:
    """Filename without its extension chain (``invariant.test.mjs`` -> ``invariant``)."""
    return Path(path).name.split(".", 1)[0]


def _extension(path: str | Path) -> str:
    """Lowercased final file extension, e.g. ``.py`` or ``.mjs``."""
    return Path(path).suffix.lower()


def _filename_tokens(path: str | Path) -> tuple[str, ...]:
    """Underscore tokens of the filename stem, with a leading ``test`` dropped."""
    tokens = tuple(t for t in normalize_marker_name(_stem(path)).split("_") if t)
    if tokens and tokens[0] == "test":
        tokens = tokens[1:]
    return tokens


def _matched_keywords(tokens: tuple[str, ...], keywords: frozenset[str]) -> tuple[str, ...]:
    """Filename tokens that appear in ``keywords``, in order, de-duplicated."""
    matched: list[str] = []
    for token in tokens:
        if token in keywords and token not in matched:
            matched.append(token)
    return tuple(matched)


def _match_area(tokens: tuple[str, ...], extension: str) -> tuple[str, tuple[str, ...]]:
    """Return ``(area, matched_keywords)`` using the conservative priority order."""
    if extension in JS_EXTENSIONS or "js" in tokens:
        return "js", ("js",)
    if tokens and tokens[0] == "helpers":
        return "helpers", ("helpers",)
    for area, keywords in KEYWORD_AREAS:
        matched = _matched_keywords(tokens, keywords)
        if matched:
            return area, matched
    return UNCATEGORIZED, ()


def _sub_area(area: str, matched: tuple[str, ...], tokens: tuple[str, ...]) -> str:
    """Derive the sub-area: matched keywords for a known area, else the filename."""
    if area == UNCATEGORIZED:
        return "_".join(tokens)
    return "_".join(matched)


def _in_helpers_dir(path: str | Path) -> bool:
    """True if ``path`` is under the test helper dir ``tests/helpers/``.

    Matches the exact adjacent ``tests``/``helpers`` component pair, so an
    unrelated ancestor directory merely named ``helpers`` does not count.
    """
    parts = Path(path).parent.parts
    adjacent_pairs = list(zip(parts, parts[1:]))
    return ("tests", "helpers") in adjacent_pairs


def classify_test_path(path: str | Path) -> TestClassification:
    """Classify a test file path into an area and a sub-area.

    A test file under a ``helpers`` directory is a helper self-test regardless of
    its filename, which complements the filename first-token rule in
    ``_match_area`` (e.g. ``test_helpers_import_state.py`` in ``tests/``).
    """
    if _in_helpers_dir(path):
        return TestClassification(area="helpers", sub_area="helpers")
    tokens = _filename_tokens(path)
    area, matched = _match_area(tokens, _extension(path))
    sub_area = _sub_area(area, matched, tokens) or UNCATEGORIZED
    return TestClassification(area=area, sub_area=sub_area)


def markers_for_path(path: str | Path) -> tuple[str, ...]:
    """Return the ``(area_*, sub_*)`` marker names for a test file path."""
    classification = classify_test_path(path)
    area_marker = normalize_marker_name(f"area_{classification.area}")
    sub_marker = normalize_marker_name(f"sub_{classification.sub_area}")
    return (area_marker, sub_marker)


def discover_markers(paths: Iterable[str | Path]) -> tuple[str, ...]:
    """Distinct ``area_*`` / ``sub_*`` marker names for ``paths``, sorted.

    Pure: it derives names from the given paths only and performs no filesystem
    access of its own. The caller decides which paths to scan. Used at
    ``pytest_configure`` time to register the dynamic ``sub_*`` markers.
    """
    names: set[str] = set()
    for path in paths:
        names.update(markers_for_path(path))
    return tuple(sorted(names))
