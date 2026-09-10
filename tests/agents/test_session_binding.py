from tests.agents._import_agents import ensure_agents_package

ensure_agents_package()

from services.agents.session_binding import (
    DEFAULT_AGENT_PROFILE,
    MemoryBindingStore,
    SessionBinding,
    conversation_kwarg,
    normalize_agent_profile_id,
)


def test_new_session_has_no_conversation_and_native_profile():
    store = MemoryBindingStore()
    binding = store.get("sess-1")
    assert binding.conversation_id is None
    assert binding.agent_profile_id == DEFAULT_AGENT_PROFILE


def test_put_round_trips_conversation_and_profile():
    store = MemoryBindingStore()
    store.put("sess-1", SessionBinding(conversation_id="conv-1", agent_profile_id="opencode"))
    assert store.get("sess-1") == SessionBinding("conv-1", "opencode")


def test_hermes_is_not_a_selectable_profile():
    assert normalize_agent_profile_id("hermes") == "odysseus"
    assert normalize_agent_profile_id("opencode") == "opencode"


def test_conversation_kwarg_rejects_odysseus_session_id():
    session_id = "sess-1"
    assert conversation_kwarg(session_id, SessionBinding(None, DEFAULT_AGENT_PROFILE)) is None
    assert conversation_kwarg(
        session_id, SessionBinding(session_id, DEFAULT_AGENT_PROFILE)
    ) is None
    assert conversation_kwarg(
        session_id, SessionBinding("conv-oh-1", DEFAULT_AGENT_PROFILE)
    ) == "conv-oh-1"
