"""Opt-in ``utility_reasoning_effort`` for utility/background LLM calls.

The setting is resolved in ``task_llm_call_async`` and sent only to candidates
on the utility chain whose model advertises the level.  Explicit caller values
win; with the setting empty nothing changes.
"""
import asyncio

import pytest

from src import llm_core, task_endpoint
from src.chatgpt_subscription import validate_reasoning_effort

UTIL = ("https://api.openai.example/v1", "gpt-5.5", {})
UTIL_FALLBACK = ("https://fallback.example/v1", "gpt-5.5-mini", {})
OVERRIDE = ("https://override.example/v1", "gpt-5.5", {})
NO_EVIDENCE = ("https://local.example/v1", "tiny-local-model", {})


def _stub_resolvers(monkeypatch, utility):
    """Stub the resolvers in both modules that look the utility chain up."""
    from src import endpoint_resolver

    resolve = lambda prefix, *a, **k: {  # noqa: E731
        "utility": utility, "task": OVERRIDE, "default": NO_EVIDENCE,
    }[prefix]
    fallbacks = lambda owner=None: [UTIL_FALLBACK]  # noqa: E731
    for mod in (task_endpoint, endpoint_resolver):
        monkeypatch.setattr(mod, "resolve_endpoint", resolve)
        monkeypatch.setattr(mod, "resolve_utility_fallback_candidates", fallbacks)


@pytest.fixture
def chain(monkeypatch):
    """Stub the resolvers and capture what reaches llm_call_async per model."""
    _stub_resolvers(monkeypatch, UTIL)

    async def _quiet(*_a, **_k):
        return None
    monkeypatch.setattr(task_endpoint, "wait_for_interactive_quiet", _quiet)

    calls = {}

    async def fake_call(url, model, messages, **kw):
        calls[url] = kw.get("reasoning_effort")
        if url != UTIL_FALLBACK[0]:
            raise RuntimeError("force walk down the chain")
        return "ok"

    monkeypatch.setattr(llm_core, "llm_call_async", fake_call)
    return calls


def _set_effort(monkeypatch, value):
    monkeypatch.setattr(
        "src.settings.get_user_setting",
        lambda key, owner="", default=None: value if key == "utility_reasoning_effort" else default,
    )


def _run(**kw):
    return asyncio.run(task_endpoint.task_llm_call_async(
        [{"role": "user", "content": "hi"}], **kw,
    ))


def test_setting_applies_only_to_utility_chain(chain, monkeypatch):
    _set_effort(monkeypatch, "high")
    _run()
    # The Background Tasks route shares the model id with the utility model but
    # is a different (url, model) route, so it gets no effort.
    assert chain[OVERRIDE[0]] is None
    assert chain[UTIL[0]] == "high"
    assert chain[UTIL_FALLBACK[0]] == "high"


def test_utility_candidate_gets_validated_effort(chain, monkeypatch):
    _set_effort(monkeypatch, "HIGH")
    _run()
    assert chain[UTIL[0]] == "high"


def test_effort_without_evidence_is_dropped(chain, monkeypatch):
    _set_effort(monkeypatch, "high")
    _stub_resolvers(monkeypatch, ("https://local.example/v1", "tiny-local-model", {}))
    _run()
    assert chain["https://local.example/v1"] is None


def test_unsupported_level_is_dropped(chain, monkeypatch):
    _set_effort(monkeypatch, "bogus")
    _run()
    assert chain[UTIL[0]] is None


def test_empty_setting_changes_nothing(chain, monkeypatch):
    _set_effort(monkeypatch, "")
    _run()
    assert chain and all(effort is None for effort in chain.values())


def test_explicit_caller_effort_wins(chain, monkeypatch):
    _set_effort(monkeypatch, "high")
    _run(reasoning_effort="low")
    assert chain and all(effort == "low" for effort in chain.values())


def test_fallback_factory_can_override_messages_and_kwargs(monkeypatch):
    seen = []

    async def fake_call(url, model, messages, **kw):
        seen.append((model, messages, kw.get("reasoning_effort"), kw.get("temperature")))
        if len(seen) == 1:
            raise RuntimeError("first fails")
        return "ok"

    monkeypatch.setattr(llm_core, "llm_call_async", fake_call)

    def factory(index, url, model, headers):
        return {"kwargs": {"reasoning_effort": f"e{index}"}}

    out = asyncio.run(llm_core.llm_call_async_with_fallback(
        [("http://a/v1", "m1", {}), ("http://b/v1", "m2", {})],
        [{"role": "user", "content": "x"}],
        temperature=0.1,
        candidate_request_factory=factory,
    ))
    assert out == "ok"
    assert [(s[0], s[2], s[3]) for s in seen] == [("m1", "e0", 0.1), ("m2", "e1", 0.1)]


def test_validate_reasoning_effort_needs_evidence():
    assert validate_reasoning_effort("gpt-5.5", "high") == "high"
    assert validate_reasoning_effort("tiny-local-model", "high") is None
    assert validate_reasoning_effort("gpt-5.5", "") is None


def test_chatgpt_wire_carries_effort_and_default_omits_it():
    sent = llm_core._build_chatgpt_responses_payload(
        "gpt-5.5", [{"role": "user", "content": "hi"}], 0.3, 64, reasoning_effort="high",
    )
    assert sent["reasoning"] == {"effort": "high"}
    plain = llm_core._build_chatgpt_responses_payload(
        "gpt-5.5", [{"role": "user", "content": "hi"}], 0.3, 64,
    )
    assert "reasoning" not in plain


def test_sync_llm_call_cache_key_includes_effort(monkeypatch):
    keys = []

    def fake_cached(key):
        keys.append(key)
        return "cached"

    monkeypatch.setattr(llm_core, "_get_cached_response", fake_cached)
    msgs = [{"role": "user", "content": "hi"}]
    llm_core.llm_call("http://x/v1", "m", msgs)
    llm_core.llm_call("http://x/v1", "m", msgs, reasoning_effort="high")
    llm_core.llm_call("http://x/v1", "m", msgs, reasoning_effort="HIGH")
    assert keys[0] != keys[1]
    assert keys[1] == keys[2]


def test_setting_is_registered_default_and_per_user():
    from src.settings import DEFAULT_SETTINGS, _PER_USER_KEYS

    assert DEFAULT_SETTINGS["utility_reasoning_effort"] == ""
    assert "utility_reasoning_effort" in _PER_USER_KEYS


# --- effort_for_call: setting, then the chat session's own effort ---

CHAT = ("https://chat.example/v1", "gpt-5.5", {})


class _Sess:
    def __init__(self, thinking_mode="effort:low", url=CHAT[0], model=CHAT[1], owner="alice"):
        self.thinking_mode = thinking_mode
        self.endpoint_url = url
        self.model = model
        self.owner = owner
        self.headers = {}


@pytest.fixture
def util(monkeypatch):
    """Utility chain = {UTIL}; the setting is controlled via ``settings[owner]``."""
    from src import utility_effort

    settings = {}
    monkeypatch.setattr(utility_effort, "utility_routes", lambda owner=None: {(UTIL[0], UTIL[1])})
    monkeypatch.setattr(
        "src.settings.get_user_setting",
        lambda key, owner="", default=None: (
            settings.get(owner, "") if key == "utility_reasoning_effort" else default
        ),
    )
    util_mod = utility_effort
    util_mod.settings = settings
    return util_mod


def test_setting_applies_to_utility_route_and_wins_over_chat_effort(util):
    util.settings["alice"] = "medium"
    assert util.effort_for_call(UTIL[0], UTIL[1], "alice", _Sess("effort:low")) == "medium"
    assert util.effort_for_call(UTIL[0], UTIL[1], "alice") == "medium"


def test_setting_is_per_user(util):
    util.settings["alice"] = "high"
    assert util.effort_for_call(UTIL[0], UTIL[1], "alice") == "high"
    assert util.effort_for_call(UTIL[0], UTIL[1], "bob") is None


def test_session_effort_inherited_on_the_session_route(util):
    assert util.effort_for_call(CHAT[0], CHAT[1], "alice", _Sess("effort:low")) == "low"
    # Endpoint spelling differences (trailing slash) are still the same route.
    assert util.effort_for_call(CHAT[0] + "/", CHAT[1], "alice", _Sess("effort:low")) == "low"


def test_session_effort_not_inherited_on_another_route(util):
    assert util.effort_for_call(UTIL[0], UTIL[1], "alice", _Sess("effort:low")) is None
    assert util.effort_for_call(CHAT[0], "gpt-5.5-mini", "alice", _Sess("effort:low")) is None


def test_session_effort_off_or_missing_inherits_nothing(util):
    assert util.effort_for_call(CHAT[0], CHAT[1], "alice", _Sess("off")) is None
    assert util.effort_for_call(CHAT[0], CHAT[1], "alice", _Sess("")) is None


def test_no_session_means_no_inheritance(util):
    assert util.effort_for_call(CHAT[0], CHAT[1], "alice") is None


def test_explicit_off_setting_blocks_session_effort_inheritance(util):
    util.settings["alice"] = "off"
    assert util.effort_for_call(CHAT[0], CHAT[1], "alice", _Sess("effort:low")) is None
    assert util.candidate_effort_factory("alice") is None

    util.settings["alice"] = "default"
    assert util.effort_for_call(CHAT[0], CHAT[1], "alice", _Sess("effort:low")) is None
    assert util.candidate_effort_factory("alice") is None


def test_inherited_effort_is_validated_against_the_model(util):
    assert util.effort_for_call(
        NO_EVIDENCE[0], NO_EVIDENCE[1], "alice",
        _Sess("effort:low", url=NO_EVIDENCE[0], model=NO_EVIDENCE[1]),
    ) is None
    assert util.effort_for_call(CHAT[0], CHAT[1], "alice", _Sess("effort:bogus")) is None


def test_effort_for_call_never_raises(util, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(util, "effort_for_route", boom)
    assert util.effort_for_call(UTIL[0], UTIL[1], "alice", _Sess()) is None


# --- no global fill: callers that are not utility call sites never get an effort ---

def test_llm_call_async_adds_no_default_effort(util, monkeypatch):
    # The owner-less helpers resolve at global scope; set it there and per user.
    util.settings[""] = util.settings["alice"] = "high"
    seen = []

    def fake_key(url, model, messages, temperature, max_tokens, headers=None,
                 thinking_mode=None, reasoning_effort=None):
        seen.append(reasoning_effort)
        return "k"

    monkeypatch.setattr(llm_core, "_get_cache_key", fake_key)
    monkeypatch.setattr(llm_core, "_get_cached_response", lambda key: "cached")
    msgs = [{"role": "user", "content": "hi"}]
    # Same (url, model) as the utility route, but not a utility call site.
    asyncio.run(llm_core.llm_call_async(UTIL[0], UTIL[1], msgs))
    llm_core.llm_call(UTIL[0], UTIL[1], msgs)
    asyncio.run(llm_core.llm_call_async(UTIL[0], UTIL[1], msgs, reasoning_effort="low"))
    assert seen == [None, None, "low"]


# --- call sites pass the effort explicitly ---

def _capture_llm(monkeypatch):
    sent = []

    async def fake_call(url, model, messages, **kw):
        sent.append((url, model, kw.get("reasoning_effort")))
        return "A title"

    monkeypatch.setattr(llm_core, "llm_call_async", fake_call)
    return sent


class _History:
    role = "user"
    content = "hello there"


def _title_session(**kw):
    sess = _Sess(**kw)
    sess.id = "s1"
    sess.history = [_History()]
    return sess


class _Manager:
    def update_session_name(self, *_a):
        pass


def test_auto_title_inherits_chat_effort_when_utility_is_blank(util, monkeypatch):
    from routes import chat_helpers

    sent = _capture_llm(monkeypatch)
    # Blank utility/task resolves to the session's own route.
    monkeypatch.setattr(task_endpoint, "resolve_task_endpoint", lambda *a, **k: (CHAT[0], CHAT[1], {}))
    asyncio.run(chat_helpers.auto_name_session(_Manager(), _title_session()))
    assert sent == [(CHAT[0], CHAT[1], "low")]


def test_auto_title_setting_beats_chat_effort(util, monkeypatch):
    from routes import chat_helpers

    util.settings["alice"] = "medium"
    sent = _capture_llm(monkeypatch)
    monkeypatch.setattr(task_endpoint, "resolve_task_endpoint", lambda *a, **k: (UTIL[0], UTIL[1], {}))
    asyncio.run(chat_helpers.auto_name_session(_Manager(), _title_session()))
    assert sent == [(UTIL[0], UTIL[1], "medium")]


def test_auto_title_on_a_different_utility_model_gets_no_chat_effort(util, monkeypatch):
    from routes import chat_helpers

    sent = _capture_llm(monkeypatch)
    monkeypatch.setattr(task_endpoint, "resolve_task_endpoint", lambda *a, **k: (UTIL[0], UTIL[1], {}))
    asyncio.run(chat_helpers.auto_name_session(_Manager(), _title_session()))
    assert sent == [(UTIL[0], UTIL[1], None)]


def test_memory_extraction_passes_chat_effort(util, monkeypatch):
    from services.memory import memory_extractor

    sent = []

    async def fake_call(url, model, messages, **kw):
        sent.append(kw.get("reasoning_effort"))
        return "[]"

    monkeypatch.setattr(llm_core, "llm_call_async", fake_call)

    class Sess(_Sess):
        def get_context_messages(self):
            return [{"role": "user", "content": "I live in Oslo"},
                    {"role": "assistant", "content": "Noted"}]

    asyncio.run(memory_extractor.extract_and_store(
        Sess(), object(), object(), CHAT[0], CHAT[1], {},
    ))
    assert sent and sent[0] == "low"


def test_email_summary_sends_setting_effort_for_owner_only(util, monkeypatch):
    from routes.email import email_helpers

    util.settings["alice"] = "medium"
    sent = []

    async def fake_call(**kw):
        sent.append(kw.get("reasoning_effort"))
        return "- point"

    monkeypatch.setattr(llm_core, "llm_call_async", fake_call)
    for owner in ("alice", "bob", None):
        asyncio.run(email_helpers._generate_email_summary(
            UTIL[0], UTIL[1], "a@x", "s", "body", owner=owner,
        ))
    assert sent == ["medium", None, None]


def test_candidate_effort_factory_scopes_to_utility_route(util):
    from src.utility_effort import candidate_effort_factory

    assert candidate_effort_factory("alice") is None
    util.settings["alice"] = "medium"
    factory = candidate_effort_factory("alice")
    assert factory(0, UTIL[0], UTIL[1], {}) == {"kwargs": {"reasoning_effort": "medium"}}
    assert factory(1, NO_EVIDENCE[0], NO_EVIDENCE[1], {}) == {}
    assert candidate_effort_factory("bob") is None


def test_fallback_route_efforts_and_factory(monkeypatch):
    from src.utility_effort import candidate_effort_factory, effort_for_route

    def fake_resolve_id(ep_id, model, owner=None):
        if ep_id == "ep-fallback":
            return ("https://fallback.example/v1", model, {})
        return None

    monkeypatch.setattr("src.endpoint_resolver.resolve_endpoint_by_id", fake_resolve_id)
    monkeypatch.setattr(
        "src.settings.load_settings",
        lambda: {"utility_model_fallbacks": [{"endpoint_id": "ep-fallback", "model": "gpt-5.5-mini", "reasoning_effort": "low"}]},
    )
    monkeypatch.setattr(
        "src.settings.get_user_setting",
        lambda key, owner="", default=None: default,
    )

    factory = candidate_effort_factory("alice")
    assert factory is not None
    assert effort_for_route("https://api.openai.example/v1", "gpt-5.5", "alice") is None
    assert effort_for_route("https://fallback.example/v1", "gpt-5.5-mini", "alice") == "low"
    assert factory(0, "https://fallback.example/v1", "gpt-5.5-mini", {}) == {"kwargs": {"reasoning_effort": "low"}}

