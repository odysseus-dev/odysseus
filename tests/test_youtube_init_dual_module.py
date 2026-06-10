"""Regression: app.py must initialize both YouTube handler modules.

The repo has two parallel YouTube handler modules:

  - ``src.youtube_handler``               — used by ``src/chat_handler.py`` and
    ``src/chat_processor.py`` (the chat path the user actually hits).
  - ``services.youtube.youtube_handler``  — used by ``routes/diagnostics_routes.py``.

Each module keeps its own module-level ``YOUTUBE_AVAILABLE`` flag. ``app.py``
previously only called ``services.youtube.init_youtube()`` at startup, so the
``src`` module's flag stayed ``False``. Chat requests that included a YouTube
URL therefore fell through to ``{"success": False, "error": "YouTube transcript
API not available"}`` even when ``youtube-transcript-api`` was installed, while
the diagnostics endpoint worked.

The fix is for ``app.py`` to call both ``init_youtube()`` variants at startup
so both code paths see the same state. This test fails on the broken version
and passes after the fix is applied. The static check uses ``ast`` so the test
is robust to harmless refactors (e.g. aliasing the imports, reformatting, or
moving the call into a helper function).
"""
import ast
import asyncio
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

APP_PY = ROOT / "app.py"

# (module, expected-call-name-or-None) pairs the regression guarantees.
# The second entry uses None as a sentinel: the call name is whatever the
# import was bound to (e.g. ``init_youtube`` or ``init_youtube_src``).
REQUIRED_INITIALIZERS = (
    ("services.youtube", "init_youtube"),
    ("src.youtube_handler", None),
)


def _parse_app_py() -> ast.Module:
    return ast.parse(APP_PY.read_text(encoding="utf-8"), filename=str(APP_PY))


def _collect_init_imports(tree: ast.Module) -> dict[str, str]:
    """Return {module: bound-name} for every `from <module> import init_youtube[ as X]`."""
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module not in {module for module, _ in REQUIRED_INITIALIZERS}:
            continue
        for alias in node.names:
            if alias.name == "init_youtube":
                found[node.module] = alias.asname or alias.name
    return found


def _collect_module_level_calls(tree: ast.Module) -> set[str]:
    """Return the set of call names executed at module level (top-level Expr -> Call)."""
    calls: set[str] = set()
    for stmt in tree.body:
        if not isinstance(stmt, ast.Expr):
            continue
        call = stmt.value
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if isinstance(func, ast.Name):
            calls.add(func.id)
    return calls


def test_app_py_initializes_both_youtube_handlers():
    """Both YouTube handler modules' init_youtube() must be called at app startup."""
    tree = _parse_app_py()
    imports = _collect_init_imports(tree)
    calls = _collect_module_level_calls(tree)

    for module, expected_name in REQUIRED_INITIALIZERS:
        assert module in imports, (
            f"app.py is missing `from {module} import init_youtube`. "
            "Both YouTube handler modules need to be initialized at startup; "
            "see the PR description for the chat-path transcript failure."
        )
        bound_name = imports[module]
        if expected_name is not None:
            assert bound_name == expected_name, (
                f"app.py imports `from {module} import init_youtube` but binds it to "
                f"`{bound_name!r}`; expected `{expected_name!r}`."
            )
        assert bound_name in calls, (
            f"app.py imports init_youtube from {module} as `{bound_name}` but never "
            f"calls it at module level. The {module} module's YOUTUBE_AVAILABLE flag "
            "stays False and its callers short-circuit."
        )


def test_both_youtube_modules_have_init_youtube_symbol():
    """Sanity check: the symbols the test references actually exist on the legacy module."""
    legacy = sys.modules.get("src.youtube_handler")
    if legacy is None:
        import src.youtube_handler  # noqa: F401
        legacy = sys.modules["src.youtube_handler"]
    assert hasattr(legacy, "init_youtube")
    assert hasattr(legacy, "YOUTUBE_AVAILABLE")
    assert hasattr(legacy, "extract_transcript_async")


def test_chat_path_short_circuits_when_legacy_module_not_initialized(monkeypatch):
    """Document the short-circuit behaviour the fix removes.

    With the legacy module's flag at its module-default ``False``,
    ``extract_transcript_async`` returns the 'not available' error without
    ever attempting a transcript fetch. After the fix, ``app.py`` flips the
    flag and the short-circuit is bypassed.
    """
    # Ensure a clean module state and a successful stub for the optional lib
    # (we're not testing the actual transcript fetch here, just the guard).
    fake = types.ModuleType("youtube_transcript_api")
    fake.YouTubeTranscriptApi = object
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", fake)
    for name in (
        "src.youtube_handler",
        "services.youtube",
        "services.youtube.youtube_handler",
    ):
        monkeypatch.delitem(sys.modules, name, raising=False)

    import src.youtube_handler as legacy

    # Pre-condition: the legacy module's flag is False by default because
    # init_youtube() was never called.
    assert legacy.YOUTUBE_AVAILABLE is False

    result = asyncio.run(
        legacy.extract_transcript_async("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ")
    )
    assert result["success"] is False
    assert result["error"] == "YouTube transcript API not available"
    assert result["transcript"] is None
