"""Regression tests for issue #1285 — blind Compare must not leak model
identities through helper-session names or GET /api/sessions.

Two guards are pinned here:

1. Backend: ``routes.session_routes._public_model`` blanks the ``model`` field
   of any ``[CMP] …`` helper session in the session list, so the sidebar /
   ``/api/sessions`` can't be used to map a neutral pane label ("Model A")
   back to its real model.
2. Frontend: every ``[CMP]`` session name built in ``static/js/compare/`` is
   guarded by ``state._blindMode`` so blind sessions are named by slot rather
   than by the real model.

The backend import mirrors tests/test_session_ghost_delete.py: stub the heavy
ORM modules so the real route module imports under conftest's MagicMock
sqlalchemy stub, then restore sys.modules so the stubs don't leak into sibling
test modules.
"""

import sys
import importlib
from pathlib import Path
from unittest.mock import MagicMock

_REPO = Path(__file__).resolve().parent.parent

# Mirror tests/test_session_ghost_delete.py exactly: stub only the ORM *class*
# modules and import the REAL core.session_manager + src.auth_helpers. pytest
# caches routes.session_routes after the first import, so stubbing auth_helpers /
# session_manager here would poison the shared module for the sibling session
# tests (whichever file is collected first wins). Matching their stub set keeps
# the cached module identical regardless of collection order. We restore both
# sys.modules AND the parent `routes` package attribute so the stub-bound module
# never leaks into sibling modules via `import routes.session_routes as X`.
_ABSENT = object()


def _save_module_and_parent_attr(dotted_name):
    """Capture a module's sys.modules entry *and* its parent-package attribute.

    Importing ``routes.session_routes`` also sets ``session_routes`` on the
    parent ``routes`` package object, and ``import routes.session_routes as X``
    resolves ``X`` through that parent attribute — so restoring sys.modules
    alone leaves the stale stub-bound module reachable. Returns a (module, attr)
    pair to hand back to _restore_module_and_parent_attr.
    """
    saved_module = sys.modules.get(dotted_name, _ABSENT)
    pkg_name, _, attr = dotted_name.rpartition(".")
    pkg = sys.modules.get(pkg_name)
    saved_attr = getattr(pkg, attr, _ABSENT) if pkg is not None else _ABSENT
    return saved_module, saved_attr


def _restore_module_and_parent_attr(dotted_name, saved_module, saved_attr):
    """Restore (or remove) both the sys.modules entry and the parent attribute.

    Passing _ABSENT for both clears the cache, which is how we drop any stale
    entry before the stubbed import.
    """
    if saved_module is _ABSENT:
        sys.modules.pop(dotted_name, None)
    else:
        sys.modules[dotted_name] = saved_module
    pkg_name, _, attr = dotted_name.rpartition(".")
    pkg = sys.modules.get(pkg_name)
    if pkg is None:
        return
    if saved_attr is _ABSENT:
        if hasattr(pkg, attr):
            delattr(pkg, attr)
    else:
        setattr(pkg, attr, saved_attr)


_TEMP_STUBS = ("core.database", "core.models")
_saved = {name: sys.modules.get(name, _ABSENT) for name in _TEMP_STUBS}
_saved["core.session_manager"] = sys.modules.get("core.session_manager", _ABSENT)
_sr_saved = _save_module_and_parent_attr("routes.session_routes")
try:
    for _name in _TEMP_STUBS:
        sys.modules[_name] = MagicMock(name=_name)
    if isinstance(sys.modules.get("core.session_manager"), MagicMock):
        del sys.modules["core.session_manager"]
    # Clear the sys.modules entry AND the parent `routes` attribute so the
    # stubbed import below produces a fresh module with no stale binding behind it.
    _restore_module_and_parent_attr("routes.session_routes", _ABSENT, _ABSENT)
    importlib.import_module("core.session_manager")
    import routes.session_routes as SR  # noqa: E402
finally:
    for _name, _val in _saved.items():
        if _val is _ABSENT:
            sys.modules.pop(_name, None)
        else:
            sys.modules[_name] = _val
    _restore_module_and_parent_attr("routes.session_routes", *_sr_saved)


# ── backend: GET /api/sessions model redaction ─────────────────────────────

def test_public_model_blanks_blind_compare_sessions():
    """A blind-compare helper session ("[CMP] Model A") must not expose its
    real model in the session list — that is the de-anonymization vector."""
    assert SR._public_model("[CMP] Model A", "gpt-4o") == ""
    assert SR._public_model("[CMP] Model B", "llama-3.1-70b") == ""


def test_public_model_blanks_any_cmp_prefixed_session():
    """Defense in depth: even a non-blind [CMP] session (named after the real
    model) gets its model field blanked. The name already carries whatever the
    user chose to reveal, and the session list never needs the raw model."""
    assert SR._public_model("[CMP] gpt-4o", "gpt-4o") == ""


def test_public_model_preserves_normal_sessions():
    """Ordinary chats are untouched — only the [CMP] prefix triggers redaction.
    The post-vote "Compare: a vs b" folder is a normal session, not a helper."""
    assert SR._public_model("My research chat", "gpt-4o") == "gpt-4o"
    assert SR._public_model("", "claude-sonnet") == "claude-sonnet"
    assert SR._public_model("Compare: gpt-4o vs llama", "gpt-4o") == "gpt-4o"


def test_compare_prefix_constant_matches_frontend():
    """The redaction prefix must match what the frontend prepends, or the
    guard silently stops matching new sessions."""
    assert SR.COMPARE_SESSION_PREFIX == "[CMP] "


# ── frontend: every [CMP] session name is blind-guarded ────────────────────

def test_compare_session_names_are_blind_guarded():
    """Every line in static/js/compare/ that builds a '[CMP]' session name
    must branch on state._blindMode, so a blind comparison is never named
    after its real model. Pins the #1285 fix against regressions."""
    compare_dir = _REPO / "static" / "js" / "compare"
    assert compare_dir.is_dir(), f"missing {compare_dir}"
    offenders = []
    for path in sorted(compare_dir.glob("*.js")):
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if "'[CMP] '" in line and "_blindMode" not in line:
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Compare session names must be blind-guarded (issue #1285):\n"
        + "\n".join(offenders)
    )
