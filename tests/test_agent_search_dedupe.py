from src.agent_loop import (
    _is_tool_preamble,
    _memory_list_summary_from_tool_output,
    _session_list_summary_from_tool_output,
    _web_search_queries_overlap,
    _web_search_query_from_block,
)
from src.tool_types import ToolBlock


def test_near_duplicate_web_search_queries_are_detected():
    assert _web_search_queries_overlap(
        "latest Python release version",
        "Python 3.14 release python.org latest version",
    )


def test_distinct_web_search_topics_are_not_collapsed():
    assert not _web_search_queries_overlap(
        "latest Python release",
        "how to install Python on Arch Linux",
    )


def test_different_explicit_versions_are_not_collapsed():
    assert not _web_search_queries_overlap(
        "Python 3.13 release notes",
        "Python 3.14 release notes",
    )


def test_web_search_query_extractor_accepts_json_and_plain_blocks():
    assert _web_search_query_from_block(
        ToolBlock("web_search", '{"query":"latest Python release"}')
    ) == "latest Python release"
    assert _web_search_query_from_block(
        ToolBlock("web_search", "latest Python release")
    ) == "latest Python release"


def test_tool_preamble_is_separate_from_a_substantive_tool_round_answer():
    assert _is_tool_preamble("I'll fetch the official Python documentation now.")
    assert _is_tool_preamble("Let me check the source.")
    assert not _is_tool_preamble(
        "I'll check the source, then compare the supported versions and explain the difference. "
        "The answer depends on the installed runtime."
    )


def test_broad_memory_listing_keeps_bounded_reviewable_items():
    raw = (
        "Found 257 memory entries:\n\n"
        "- [fact] `a1` — private detail\n"
        "- [fact] `a2` — another detail\n"
        "- [preference] `b1` — hidden preference\n"
    )

    summary = _memory_list_summary_from_tool_output(raw)

    assert summary.startswith("Memory: 257 saved entries (fact 2, preference 1).")
    assert "- [fact a1](#memory-a1) — private detail" in summary
    assert "- [preference b1](#memory-b1) — hidden preference" in summary
    assert "...and 254 more saved memories." in summary
    assert "[Open Memory to browse all](#memory)" in summary


def test_compact_memory_listing_is_already_a_complete_summary():
    raw = "Memory: 257 saved entries (contact 9, fact 98, preference 63)."

    assert _memory_list_summary_from_tool_output(raw) == raw


def test_session_listing_is_bounded_for_terminal_router():
    raw = "Found 20 session(s), sorted most-recent first:\n" + "\n".join(
        f"- session {index}" for index in range(20)
    )
    summary = _session_list_summary_from_tool_output(raw)

    assert summary.startswith("Found 20 session(s)")
    assert sum(line.startswith("- session ") for line in summary.splitlines()) == 12
    assert "...and more sessions" in summary
    assert "session 19" not in summary
