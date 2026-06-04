import pytest


@pytest.mark.asyncio
async def test_dispatch_reminder_passes_owner_to_resolve_endpoint(monkeypatch):
    """dispatch_reminder must scope the LLM endpoint lookup to the note owner.

    With multi-user deployments the endpoint config is per-owner. If
    resolve_endpoint is called without `owner`, the synthesis LLM (and its
    API key) can be resolved from another user's private endpoint.
    """
    from src import endpoint_resolver
    from src import settings as settings_mod
    from routes import note_routes

    # Enable LLM synthesis so the resolve_endpoint branch runs; keep the
    # browser channel (no SMTP/ntfy needed). _scheduler_ref defaults to None,
    # so the in-app notification push is skipped.
    monkeypatch.setattr(
        settings_mod,
        "load_settings",
        lambda: {"reminder_channel": "browser", "reminder_llm_synthesis": True},
    )

    calls = []

    def fake_resolve_endpoint(setting_prefix, *args, **kwargs):
        calls.append({"prefix": setting_prefix, "owner": kwargs.get("owner")})
        # Return empty so both "utility" and "default" are attempted and
        # llm_call_async is never reached.
        return ("", "", {})

    monkeypatch.setattr(endpoint_resolver, "resolve_endpoint", fake_resolve_endpoint)

    result = await note_routes.dispatch_reminder(
        title="Test",
        note_body="body",
        note_id="n1",
        owner="alice",
    )

    assert calls, "resolve_endpoint was never called"
    assert all(c["owner"] == "alice" for c in calls), (
        f"resolve_endpoint called without owner='alice': {calls}"
    )
    # Sanity: both endpoint tiers were attempted with the owner.
    assert {c["prefix"] for c in calls} == {"utility", "default"}
    assert result["synthesis"] == "[utility model unavailable — no summary generated]"
