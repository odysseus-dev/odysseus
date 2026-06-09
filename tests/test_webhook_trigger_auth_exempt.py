"""Pin the auth exemption for task webhook-trigger URLs.

The task router exposes ``POST /api/tasks/{task_id}/webhook/{token}`` as a
public webhook entrypoint — the path-embedded ``webhook_token`` is the
credential, and the route handler in ``routes/task_routes.py`` validates
it against the row and returns 404 on mismatch. The UI advertises the
URL as "no auth needed" because external callers (Zapier, n8n, curl)
can't supply a session cookie.

Without an entry in ``AUTH_EXEMPT_PATTERNS`` ``AuthMiddleware`` rejected
every POST with 401 before the token was ever checked (issue #621).
This test re-reads the exemption logic out of ``app.py`` and confirms a
representative webhook path is treated as exempt, while neighbouring
non-public task paths are NOT.
"""

import os
import re


def _read_app_source() -> str:
    app_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "app.py",
    )
    with open(app_path, encoding="utf-8") as fh:
        return fh.read()


def test_webhook_trigger_path_is_in_exempt_patterns():
    """The dynamic webhook trigger path must match an AUTH_EXEMPT_PATTERNS
    entry. Pull every regex literal compiled inside the block out of the
    source and apply it directly — extraction has to tolerate nested
    brackets inside each character class (e.g. ``[^/]+``)."""
    src = _read_app_source()
    # Find the start of the list, then walk character-by-character to the
    # matching closing bracket. A regex would have to count brackets,
    # which is more painful than just doing the count by hand.
    start = src.find("AUTH_EXEMPT_PATTERNS")
    assert start != -1, "AUTH_EXEMPT_PATTERNS not declared in app.py"
    lb = src.find("[", start)
    assert lb != -1
    depth = 0
    end = -1
    for i in range(lb, len(src)):
        ch = src[i]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i
                break
    assert end != -1, "could not find closing bracket for AUTH_EXEMPT_PATTERNS"
    body = src[lb + 1 : end]
    # Pull each compiled regex literal: _re.compile(r"...").
    patterns = re.findall(r'_re\.compile\(\s*r"([^"]+)"\s*\)', body)
    assert patterns, (
        "expected at least one compiled regex in AUTH_EXEMPT_PATTERNS"
    )
    compiled = [re.compile(p) for p in patterns]

    sample = "/api/tasks/abc123/webhook/" + "x" * 43
    assert any(c.match(sample) for c in compiled), (
        f"webhook trigger path {sample!r} must be auth-exempt - issue #621"
    )

    # Negative: routes that are NOT meant to be public must not match.
    for not_public in (
        "/api/tasks",
        "/api/tasks/abc123",
        "/api/tasks/abc123/webhook-regenerate",
        "/api/tasks/abc123/run",
    ):
        assert not any(c.match(not_public) for c in compiled), (
            f"{not_public!r} must NOT be auth-exempt"
        )


def test_webhook_trigger_handler_still_validates_token():
    """The exemption is only safe because the route handler in
    routes/task_routes.py still checks the token against the row and
    returns 404 on mismatch. Pin that behaviour so a refactor of the
    handler doesn't quietly make the endpoint truly anonymous. Read the
    source directly — importing task_routes pulls in SQLAlchemy and
    fails under the conftest stubs."""
    routes_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "routes",
        "task_routes.py",
    )
    with open(routes_path, encoding="utf-8") as fh:
        src = fh.read()
    assert "ScheduledTask.webhook_token == token" in src
    assert '@router.post("/{task_id}/webhook/{token}")' in src
