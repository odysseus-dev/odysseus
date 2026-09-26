"""AI reply picks the dedicated email-draft model when one is configured.

email_draft_endpoint_id / email_draft_model let users draft replies with a
stronger model than their chat model. When unset, the open chat session's
model is used as before.
"""
import asyncio

from routes import email_routes

DRAFT = ("http://draft.local/v1/chat/completions", "draft-model", None)
SESSION_URL = "http://session.local/v1/chat/completions"


def _run_ai_reply(monkeypatch, settings):
    calls = []

    async def fake_llm(candidates, *a, **k):
        calls.append(candidates[0][:2])
        return "Hi Sara,\n\nSounds good.\n\nBest Regards,\nAbdullah Ali"

    def fake_resolve(prefix, *a, **k):
        if prefix == "email_draft":
            return DRAFT
        return ("http://utility.local/v1/chat/completions", "utility-model", None)

    class _Sess:
        endpoint_url = SESSION_URL
        headers = None
        model = "session-model"

    class _Query:
        def filter(self, *a, **k):
            return self

        def first(self):
            return _Sess()

    class _Db:
        def query(self, *a, **k):
            return _Query()

        def close(self):
            pass

    import core.database as database
    import src.endpoint_resolver as endpoint_resolver
    import src.llm_core as llm_core

    monkeypatch.setattr(email_routes, "_load_settings", lambda: settings)
    monkeypatch.setattr(endpoint_resolver, "resolve_endpoint", fake_resolve)
    monkeypatch.setattr(endpoint_resolver, "resolve_utility_fallback_candidates", lambda **k: [])
    monkeypatch.setattr(llm_core, "llm_call_async_with_fallback", fake_llm)
    monkeypatch.setattr(llm_core, "list_model_ids", lambda *a, **k: [])
    monkeypatch.setattr(database, "SessionLocal", lambda: _Db())

    router = email_routes.setup_email_routes()
    handler = next(r.endpoint for r in router.routes
                   if r.path.endswith("/ai-reply") and "POST" in r.methods)
    result = asyncio.run(handler({
        "to": "sara@example.com", "subject": "Re: Dinner",
        "original_body": "Dinner Saturday?", "session_id": "s1", "fast": True,
    }, owner="alice"))
    return result, calls


def test_ai_reply_uses_email_draft_model_when_configured(monkeypatch):
    result, calls = _run_ai_reply(monkeypatch, {
        "email_draft_endpoint_id": "ep-draft", "email_draft_model": "draft-model",
    })
    assert result.get("reply")
    assert calls[0] == DRAFT[:2]


def test_ai_reply_uses_session_model_when_no_draft_model(monkeypatch):
    result, calls = _run_ai_reply(monkeypatch, {})
    assert result.get("reply")
    assert calls[0] == (SESSION_URL, "session-model")
