from src.turn_contract import requested_capabilities
from src.agent_runtime.authority import create_request_authority
from src.text_helpers import strip_think


def test_distance_from_home_grants_memory_and_search_browser():
    prompt = (
        "Tomorrow I'm going to Dreamstate in long beach, can you look up the lineup, "
        "set times and artists playing. Could you also give me the event details and "
        "distance from home to the event as well as the weather"
    )
    caps = requested_capabilities(prompt)
    assert "search_browser" in caps
    assert "memory" in caps

    auth = create_request_authority(prompt, owner="user-1", session_id="sess-1", workspace="/tmp")
    granted_tools = {g.tool for g in auth.grants}
    assert {"web_search", "get_weather", "manage_memory"} <= granted_tools


def test_anaphoric_continue_this_task_inherits_prior_capabilities():
    first_prompt = (
        "Tomorrow I'm going to Dreamstate in long beach, can you look up the lineup, "
        "set times and artists playing. Could you also give me the event details and "
        "distance from home to the event as well as the weather"
    )
    history = [
        {"role": "user", "content": first_prompt},
        {"role": "assistant", "content": "I need your starting location to calculate the distance."},
    ]
    followup = "okay so what do you need from me to continue this task"
    caps = requested_capabilities(followup, history)
    assert "tasks" not in caps
    assert "search_browser" in caps
    assert "memory" in caps

    auth = create_request_authority(
        followup, history=history, owner="user-1", session_id="sess-1", workspace="/tmp"
    )
    granted_tools = {g.tool for g in auth.grants}
    assert {"web_search", "get_weather", "manage_memory"} <= granted_tools


def test_gemma4_channel_thought_and_untagged_plan_prose_are_stripped():
    leaked = (
        "<|channel>thought\nThe user wants information about Dreamstate.\n<channel|>"
        "The user wants information about an event called \"Dreamstate\" in Long Beach.\n"
        "Plan:\n1. Search for Dreamstate Long Beach.\n"
        "Let's start with web_search.\n\n"
        "Dreamstate SoCal takes place at the Queen Mary Waterfront in Long Beach."
    )
    cleaned = strip_think(leaked, prose=True, prompt_echo=True)
    assert "The user wants information" not in cleaned
    assert "Let's start with web_search" not in cleaned
    assert cleaned == "Dreamstate SoCal takes place at the Queen Mary Waterfront in Long Beach."
