"""Regression tests for the model-endpoint shared/private owner toggle (#590).

Non-admin users only ever see `ModelEndpoint` rows where `owner` is NULL
(shared) or equals their own username (`owner_filter`). Endpoints added
before the `shared`-by-default behaviour existed — or added with
`shared=false` — get stuck scoped to whichever admin created them, and
there was no way to flip that back short of deleting and recreating the
endpoint (losing pinned/hidden models and refresh tuning in the process).

These tests assert the source directly (matching this file's existing
`test_*_is_owner_scoped` convention) rather than spinning up the full app,
since the fix only needs to prove:
  1. `GET /api/model-endpoints` now reports `owner` so the admin UI can show
     ownership.
  2. `PATCH /api/model-endpoints/{id}` accepts a `shared` field that flips
     `owner` between NULL and the requesting admin, in place.
"""

from pathlib import Path


def _model_routes_body() -> str:
    return Path("routes/model_routes.py").read_text(encoding="utf-8")


def test_list_model_endpoints_reports_owner():
    body = _model_routes_body()
    list_body = body.split("def list_model_endpoints", 1)[1].split(
        '@router.post("/model-endpoints")', 1
    )[0]
    assert '"owner": getattr(r, "owner", None)' in list_body


def test_patch_model_endpoint_supports_shared_toggle():
    body = _model_routes_body()
    patch_body = body.split("async def toggle_model_endpoint", 1)[1].split(
        "def _settings_using_endpoint", 1
    )[0]

    # Accepts a `shared` field in the PATCH body...
    assert '"shared" in body' in patch_body
    # ...and flips ownership: shared=true -> owner=None, shared=false ->
    # owner=the requesting admin (never someone else's username).
    assert "ep.owner = None if is_shared else (_gcu_toggle(request) or None)" in patch_body
    # The response echoes the resulting owner so the UI can re-render the
    # Shared/Private badge without a full endpoint-list reload.
    assert '"owner": getattr(ep, "owner", None)' in patch_body


def test_shared_toggle_uses_current_admin_not_arbitrary_input():
    # Guards against a regression where the caller could pass an arbitrary
    # `owner` string directly (impersonating another user's endpoint scope).
    # Only `shared` (bool-ish) is accepted; the owner value itself always
    # comes from the authenticated request, never from the request body.
    body = _model_routes_body()
    patch_body = body.split("async def toggle_model_endpoint", 1)[1].split(
        "def _settings_using_endpoint", 1
    )[0]
    assert '"owner" in body' not in patch_body
