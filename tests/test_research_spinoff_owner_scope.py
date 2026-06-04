"""Owner-scope regression for /api/research/spinoff endpoint resolution.

`research_spinoff()` correctly checks that the caller owns the source research
report before creating the follow-up chat session, but its endpoint fallback
chain was not fully owner-scoped: every `resolve_endpoint(...)` call ran
without an `owner` argument and the last-resort `first()` query against
`ModelEndpoint` had no owner filter at all. `ModelEndpoint` is a per-user
private resource that carries a decrypted `api_key`, so an unscoped lookup
let the spinoff path pick up ANOTHER user's endpoint and silently spend that
owner's API key / quota (and reach whatever internal `base_url` they had
configured). Same class as #870 / #1045 / #1099 / #2254 / #2255; #2409
addresses the spinoff path.

These tests pin the two parts of the fix at the source level:

  1. Every `resolve_endpoint(...)` call inside `research_spinoff` passes
     `owner=` so the settings-driven fallback chain (chat -> research ->
     utility) is scoped to the caller.
  2. The last-resort first-enabled endpoint fallback goes through
     `_owned_enabled_endpoint(db, user)` instead of a bare
     `db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).first()`.

A future change that reintroduces an unscoped lookup on this path will fail
one of these tests and force the contributor to either re-thread the owner
or justify the scope change.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RESEARCH_ROUTES = REPO / "routes" / "research_routes.py"


def _spinoff_body() -> str:
    """Return the source of the research_spinoff handler only.

    Slicing from the @router.post for /api/research/spinoff up to the next
    @router. decorator (or the end of setup_research_routes) keeps the
    assertions scoped to the handler under test and avoids matching the
    siblings (`/api/research/start`, the `_resolve_research_endpoint` helper,
    etc.) that already had their own owner-scope fix.
    """
    src = RESEARCH_ROUTES.read_text(encoding="utf-8")
    start = src.find('@router.post("/api/research/spinoff/{session_id}")')
    assert start != -1, "research_spinoff route not found"
    rest = src[start:]
    # Stop at the next route decorator or the trailing `return router`.
    end_candidates = [
        rest.find("\n    @router.", 1),
        rest.find("\n    return router", 1),
    ]
    end_candidates = [c for c in end_candidates if c != -1]
    assert end_candidates, "could not bound research_spinoff handler"
    return rest[: min(end_candidates)]


def test_research_spinoff_resolve_endpoint_calls_pass_owner():
    """Every resolve_endpoint(...) call inside research_spinoff must
    pass `owner=`. Otherwise the settings-driven chat/research/utility
    fallback can resolve to another tenant's private endpoint and the
    follow-up chat will silently run against their key (issue #2409).
    """
    body = _spinoff_body()
    calls = re.findall(r"resolve_endpoint\s*\([^)]*\)", body)
    assert calls, "expected resolve_endpoint(...) calls inside research_spinoff"
    unscoped = [c for c in calls if "owner=" not in c]
    assert not unscoped, (
        "research_spinoff has unscoped resolve_endpoint call(s): "
        f"{unscoped}. Pass owner=user so the fallback chain only resolves "
        "to endpoints the caller owns (own rows + legacy null-owner shared)."
    )


def test_research_spinoff_first_enabled_fallback_is_owner_scoped():
    """The last-resort first-enabled fallback must go through the
    `_owned_enabled_endpoint(db, owner, ...)` helper, NOT a raw
    `db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).first()`.

    A raw query lets a research-capable user spin off a follow-up chat
    using another user's decrypted api_key + base_url (cross-tenant key
    spend + SSRF on the configured base). #2409 / class of #870, #1045,
    #1099, #2254, #2255.
    """
    body = _spinoff_body()
    assert "ModelEndpoint.is_enabled == True" not in body, (
        "research_spinoff still contains an unscoped first-enabled "
        "ModelEndpoint query. Use _owned_enabled_endpoint(db, user) so "
        "the fallback never picks another tenant's private endpoint."
    )
    assert "_owned_enabled_endpoint" in body, (
        "research_spinoff no longer calls _owned_enabled_endpoint. The "
        "owner-scoped fallback must remain in the resolution chain so a "
        "panel-launched spinoff with no configured endpoints still has a "
        "safe last resort."
    )
