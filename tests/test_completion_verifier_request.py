from src.agent_loop import _completion_verifier_request


def test_completion_verifier_keeps_original_request_when_routing_appends_user_context():
    routed_messages = [
        {"role": "user", "content": "Create /workspace/output.html from config.json"},
        {"role": "user", "content": "Current date: 2026-09-04\nTimezone: UTC"},
    ]

    assert _completion_verifier_request(
        "Create /workspace/output.html from config.json",
        routed_messages,
    ) == "Create /workspace/output.html from config.json"


def test_completion_verifier_falls_back_when_original_request_is_missing():
    routed_messages = [{"role": "user", "content": "fallback task"}]

    assert _completion_verifier_request("", routed_messages) == "fallback task"
