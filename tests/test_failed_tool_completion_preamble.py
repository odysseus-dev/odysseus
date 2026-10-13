from src import agent_loop as al


def _failed_record():
    return [{"result": {"error": "blocked redundant inspection", "exit_code": 2}}]


def test_continue_inspecting_is_not_a_substantive_answer_after_failed_tool():
    text = (
        "I reviewed the available frames and described the scoreboard in detail. " * 8
        + "Let me continue inspecting this section to see the complete rally."
    )

    assert al._is_tool_preamble(
        "Let me continue inspecting this section to see the complete rally."
    )
    assert not al._substantive_answer_after_failed_tools(text, _failed_record())
    assert al._looks_like_agent_reasoning_preamble(text)


def test_im_continuing_is_recognized_as_unfinished_action():
    assert al._is_tool_preamble(
        "I'm continuing to analyze the shot sequence."
    )


def test_examine_after_failed_tool_is_not_mistaken_for_final_answer():
    text = (
        "I identified the players and scoreboard from the overview frames. " * 8
        + "Let me examine this video more carefully with focused segments "
        "to track the ball movement and count shots precisely."
    )

    assert al._is_tool_preamble(
        "Let me examine this video more carefully with focused segments "
        "to track the ball movement and count shots precisely."
    )
    assert not al._substantive_answer_after_failed_tools(text, _failed_record())


def test_now_let_me_read_after_failed_tool_is_not_a_final_answer():
    text = (
        "I have gathered some relevant background from the source material. " * 7
        + "Now let me read around the relevant section:"
    )

    assert al._is_tool_preamble("Now let me read around the relevant section:")
    assert not al._substantive_answer_after_failed_tools(text, _failed_record())


def test_orphan_think_closer_before_media_preamble_is_not_a_final_answer():
    text = (
        "I extracted several candidate words from the timestamped frames. " * 8
        + "</think>\n\nLet me look at all the frames more carefully with higher "
        "resolution to see any additional text I might be missing:"
    )

    assert al._is_tool_preamble(
        "</think>\n\nLet me look at all the frames more carefully with higher "
        "resolution to see any additional text I might be missing:"
    )
    assert not al._substantive_answer_after_failed_tools(text, _failed_record())


def test_let_me_watch_after_failed_tool_is_not_a_final_answer():
    text = (
        "The recording is several minutes long and needs systematic review. " * 8
        + "Let me watch it more systematically to understand what's happening."
    )

    assert al._is_tool_preamble(
        "Let me watch it more systematically to understand what's happening."
    )
    assert not al._substantive_answer_after_failed_tools(text, _failed_record())


def test_let_me_refine_after_failed_tool_is_not_a_final_answer():
    text = (
        "The initial search results use an unrelated meaning of the term. " * 8
        + "Let me refine my search queries to find the relevant sources."
    )

    assert al._is_tool_preamble(
        "Let me refine my search queries to find the relevant sources."
    )
    assert not al._substantive_answer_after_failed_tools(text, _failed_record())


def test_let_me_try_different_search_is_not_a_final_answer():
    text = "Let me try a different search approach to find the official sources."

    assert al._is_tool_preamble(text)


def test_let_me_request_export_is_not_a_final_answer():
    text = (
        "I reviewed the overview frames but still need timestamp-level evidence. " * 8
        + "Let me request exports of key frames showing the scoreboard and action."
    )

    assert al._is_tool_preamble(
        "Let me request exports of key frames showing the scoreboard and action."
    )
    assert not al._substantive_answer_after_failed_tools(text, _failed_record())
    assert al._looks_like_agent_reasoning_preamble(text)


def test_preparing_to_review_is_recognized_as_unfinished_action():
    text = (
        "The available frames do not establish the event count with confidence. " * 7
        + "I'm preparing to methodically review the video with more specific focus."
    )

    assert al._is_tool_preamble(
        "I'm preparing to methodically review the video with more specific focus."
    )
    assert al._looks_like_agent_reasoning_preamble(text)


def test_long_visual_observations_ending_in_need_to_scan_are_not_final():
    text = (
        "The contact sheets show several scoreboards and player close-ups. " * 20
        + "I need to scan through the video more carefully."
    )

    assert al._looks_like_agent_reasoning_preamble(text)


def test_plan_heading_with_partial_bullets_is_not_a_final_answer():
    text = (
        "The sparse observations do not yet establish where the event begins.\n\n"
        "Let me track the sequence:\n"
        "- Frames 1-4: the first player serves\n"
        "- Frames 5-8: the second player returns"
    )

    assert al._is_tool_preamble("Let me track the sequence:")
    assert al._looks_like_agent_reasoning_preamble(text)


def test_unfinished_plan_after_unpunctuated_paragraph_break_is_detected():
    text = (
        "The prior frame appears to show a possible event but remains ambiguous\n\n"
        "Let me request exports of key frames showing the action."
    )

    assert al._looks_like_agent_reasoning_preamble(text)


def test_unrequested_summary_file_does_not_authorize_mutation_completion():
    request = (
        "The container includes /workspace/fixtures/video.mp4 and related code. "
        "Carefully inspect the video and explain the paper's approach."
    )

    assert not al._request_authorizes_workspace_mutation_completion(
        request,
        artifact_creation_requested=False,
        explicit_file_creation=None,
        inspection_file_edit=None,
    )


def test_explicit_workspace_change_authorizes_mutation_completion():
    assert al._request_authorizes_workspace_mutation_completion(
        "Fix the parser in this workspace and update parser.py.",
        artifact_creation_requested=False,
        explicit_file_creation=None,
        inspection_file_edit=None,
    )


def test_optioned_followup_question_is_unattended_clarification():
    text = (
        "The available clip appears incomplete. Would you like me to:\n"
        "1. Count the rallies in a particular segment?\n"
        "2. Search for the complete recording?"
    )

    assert al._looks_like_unattended_clarification(text)
    assert not al._looks_like_unattended_clarification(
        "I found two rallies. The second is longer than the first."
    )


def test_request_to_reshare_loaded_media_is_unattended_clarification():
    text = (
        "I should ask the user for the actual video file to properly analyze it. "
        "Could you share the video file so I can watch it directly, or is there "
        "another way to access the frame content?"
    )

    assert al._looks_like_unattended_clarification(text)
