"""Owner-scope regression for POST /api/documents/ai-tidy.

`ai_tidy_documents()` captures the caller via `get_current_user(request)` and
already scopes the document query with `_owner_session_filter(q, user)`. But the
LLM endpoint lookup — `resolve_task_endpoint()` then a `resolve_endpoint("default")`
fallback — was called WITHOUT `owner=user`. In a multi-user deployment those
global lookups can return ANOTHER user's private endpoint and its decrypted
api_key, which then drives the tidy LLM call. The endpoint lookup must be scoped
to the caller the same way the document query already is. Mirrors the session /
research / compare endpoint owner-scope fixes.

`ai_tidy_documents` is a nested route handler defined inside
`setup_document_routes`. Building the router pulls in form-upload routes that
need optional packages not present in every environment, so we assert on the
function source directly (same approach as test_ai_interaction_owner_scope.py).
"""

import inspect
import re
import sys
import types
from unittest.mock import MagicMock

# Stub core.database so importing routes.document_routes is cheap under the
# conftest sqlalchemy MagicMock stubs (the real package is not installed here).
if "core.database" not in sys.modules:
    sys.modules["core.database"] = types.ModuleType("core.database")
_cd = sys.modules["core.database"]
for _name in ("SessionLocal", "Document", "DocumentVersion", "Session"):
    if not hasattr(_cd, _name):
        setattr(_cd, _name, MagicMock())

import routes.document_routes as document_routes  # noqa: E402


def _ai_tidy_source() -> str:
    """Source of the nested ai_tidy_documents handler."""
    outer = inspect.getsource(document_routes.setup_document_routes)
    marker = "async def ai_tidy_documents("
    start = outer.index(marker)
    # Slice to the next top-level route decorator after this handler.
    rest = outer[start + len(marker):]
    nxt = rest.find("\n    @router.")
    end = (start + len(marker) + nxt) if nxt != -1 else len(outer)
    return outer[start:end]


def test_task_endpoint_lookup_passes_caller_owner():
    body = _ai_tidy_source()
    # The caller is captured as `user`; the task-endpoint lookup must forward it.
    assert "user = get_current_user(request)" in body
    assert re.search(r"resolve_task_endpoint\(\s*owner\s*=\s*user", body), \
        "resolve_task_endpoint must be called with owner=user"


def test_default_fallback_lookup_passes_caller_owner():
    body = _ai_tidy_source()
    assert re.search(r'resolve_endpoint\(\s*"default"\s*,\s*owner\s*=\s*user', body), \
        'resolve_endpoint("default", ...) must be called with owner=user'


def test_no_unscoped_resolver_call_remains():
    body = _ai_tidy_source()
    # Guard against the regressed forms with no owner argument.
    assert "resolve_task_endpoint()" not in body
    assert 'resolve_endpoint("default")' not in body
