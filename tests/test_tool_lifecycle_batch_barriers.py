from pathlib import Path

from src.agent_tools import TOOL_TAGS, parse_tool_blocks


def test_tail_serve_output_is_parseable_fenced_tool():
    assert "tail_serve_output" in TOOL_TAGS

    blocks = parse_tool_blocks(
        '```' + 'tail_serve_output\n{"session_id": "abc", "tail": 400}\n' + '```'
    )

    assert len(blocks) == 1
    assert blocks[0].tool_type == "tail_serve_output"
    assert '"session_id": "abc"' in blocks[0].content
    assert '"tail": 400' in blocks[0].content


def test_ask_user_branch_sets_same_batch_barrier():
    source = Path("src/agent_loop.py").read_text(encoding="utf-8")

    ask_start = source.index('if "ask_user" in result:')
    round_stop = source.index("if _awaiting_user:", ask_start)
    branch = source[ask_start:round_stop]

    assert "ASK_USER_BATCH_BARRIER" in branch
    assert "break" in branch
