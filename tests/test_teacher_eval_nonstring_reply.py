from src.teacher_escalation import evaluate_turn_regex


def test_evaluate_turn_regex_tolerates_non_string_reply():
    # agent_reply is typed str but is the raw LLM turn output; a non-string
    # (dict / number from a malformed turn) made pat.search(agent_reply) raise
    # TypeError. The tool_results branch already isinstance-guards its rows.
    assert evaluate_turn_regex([], 123) == ("ok", None)
    assert evaluate_turn_regex([], {"text": "I cannot do that"}) == ("ok", None)


def test_evaluate_turn_regex_still_flags_give_up_string():
    status, _ = evaluate_turn_regex([], "I don't have a tool to do that")
    assert status == "failure"
