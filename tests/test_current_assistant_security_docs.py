from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

TOKEN_SCOPE_PARAGRAPH = "2. **API-token coverage is surface-specific.** Tokens have separate chat, todo, document, email, calendar, memory, and Cookbook scopes. Only routes that explicitly map the token owner and enforce the relevant scope are supported; a token is not a general subset of all UI/session privileges. Companion pairing currently mints a chat-scoped token."
CHAT_URL_PARAGRAPH = "`POST /api/v1/chat` validates a token-supplied direct `base_url` with the public-HTTP URL policy before making a provider request. Admin-configured model endpoints intentionally retain local/LAN support and are a separate trust boundary."


def test_assistant_docs_do_not_claim_checkins_are_seeded():
    assistant = (ROOT / "routes" / "assistant_routes.py").read_text(encoding="utf-8")
    scheduler = (ROOT / "src" / "task_scheduler.py").read_text(encoding="utf-8")

    assert "three daily ScheduledTasks" not in assistant
    assert "daily check-in ScheduledTasks for this owner" not in scheduler
    assert "Check-in tasks are user-created" in scheduler


def test_threat_model_matches_current_token_and_chat_url_boundaries():
    threat_model = (ROOT / "THREAT_MODEL.md").read_text(encoding="utf-8")
    lines = threat_model.splitlines()

    assert "SSRF via `/api/v1/chat`" not in threat_model
    assert TOKEN_SCOPE_PARAGRAPH in lines
    assert CHAT_URL_PARAGRAPH in lines
    assert "`src/search/` partial consolidation" not in threat_model
    assert "still independent copies" not in threat_model


def test_search_compat_modules_point_at_the_canonical_service():
    for name in ("analytics.py", "cache.py", "content.py", "query.py"):
        source = (ROOT / "src" / "search" / name).read_text(encoding="utf-8")
        assert "from services.search" in source
        assert "sys.modules[__name__]" in source

    ranking = (ROOT / "src" / "search" / "ranking.py").read_text(encoding="utf-8")
    assert "from services.search.ranking import" in ranking
