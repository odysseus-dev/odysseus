from src.agent_loop import _classify_agent_request, _is_action_continuation


LIVE_TUI_PROMPT = (
    "Create probe.txt containing exactly ODYSSEUS_BRIDGE_OK, then verify the "
    "file contains exactly that text. Do not change any other file."
)


def test_self_contained_file_sequence_does_not_inherit_prior_context() -> None:
    messages = [
        {"role": "system", "content": "You are an assistant."},
        {"role": "user", "content": "Search my email for an invoice."},
        {"role": "assistant", "content": "I found the invoice."},
        {"role": "user", "content": LIVE_TUI_PROMPT},
    ]

    intent = _classify_agent_request(messages, LIVE_TUI_PROMPT)

    assert _is_action_continuation(LIVE_TUI_PROMPT) is True
    assert intent["continuation"] is False
    assert intent["retrieval_query"] == LIVE_TUI_PROMPT
    assert intent["domains"] == {"files"}


def test_short_action_reference_remains_a_continuation() -> None:
    messages = [
        {"role": "user", "content": "The obsolete file is tmp.txt."},
        {"role": "assistant", "content": "Should I remove it?"},
        {"role": "user", "content": "delete that file"},
    ]

    intent = _classify_agent_request(messages, "delete that file")

    assert intent["continuation"] is True
    assert "The obsolete file is tmp.txt." in intent["retrieval_query"]


def test_current_state_is_not_mistaken_for_a_web_lookup() -> None:
    prompt = "Inspect the current state."

    intent = _classify_agent_request([{"role": "user", "content": prompt}], prompt)

    assert "web" not in intent["domains"]


def test_current_release_remains_a_fresh_web_lookup() -> None:
    prompt = "What is the current Kubernetes release?"

    intent = _classify_agent_request([{"role": "user", "content": prompt}], prompt)

    assert "web" in intent["domains"]
