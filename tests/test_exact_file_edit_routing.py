import asyncio
import json

import pytest

import src.agent_loop as al


@pytest.mark.parametrize(
    "message",
    [
        "In /app/data/status.txt, change status=old to status=new.",
        "In /app/data/status.txt, replace status=old with status=new.",
        "Replace `June 30` with `July 1` in ./arrivals.md.",
        "In data/fixtures/config.env, update MODE=dev to MODE=prod",
        "Change ETA June 30 to ETA July 1 in ~/notes/arrival.txt",
        "Replace stage=test with stage=production in ./deploy.env.",
        'Replace "old value" with "new value" in "/tmp/my file.txt".',
        'In fixture.py, change VALUE = "before" to VALUE = "after".',
    ],
)
def test_exact_file_replacement_classifier_accepts_direct_edits(message):
    assert al._looks_like_exact_file_replacement(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "Inspect /app/data/status.txt and fix the status.",
        "Read ./arrivals.md first, then change June 30 to July 1.",
        "Fix the bug in src/worker.py and run the tests.",
        "Change the status in /app/data/status.txt.",
        "Replace old with new.",
        "Refactor src/worker.py to use the new scheduler.",
        "Show the contents of /tmp/status.txt, then change old_value to new_value.",
        "Open /tmp/status.txt and replace old_value with new_value.",
        "Review /tmp/status.txt before changing old_value to new_value.",
        "Use cat to inspect /tmp/status.txt, then replace old_value with new_value.",
        "Examine /tmp/status.txt, then update old_value to new_value.",
        "Look at /tmp/status.txt before replacing old_value with new_value.",
        "Change old_value to new_value in /tmp/status.txt and verify the result.",
        "Replace old_value with new_value in /tmp/status.txt, then run the tests.",
    ],
)
def test_exact_file_replacement_classifier_rejects_ambiguous_or_broad_work(message):
    assert al._looks_like_exact_file_replacement(message) is False


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "Read /tmp/status.txt first, then change old_value to new_value.",
            {"path": "/tmp/status.txt", "old_string": "old_value", "new_string": "new_value"},
        ),
        (
            "Open /tmp/status.txt and replace old_value with new_value.",
            {"path": "/tmp/status.txt", "old_string": "old_value", "new_string": "new_value"},
        ),
        (
            "Use cat to inspect ./status.txt, then replace `old value` with `new value`.",
            {"path": "./status.txt", "old_string": "old value", "new_string": "new value"},
        ),
        (
            "Review /tmp/status.txt before changing old_value to new_value.",
            {"path": "/tmp/status.txt", "old_string": "old_value", "new_string": "new_value"},
        ),
        (
            'Read fixture.py first, then change VALUE = "before" to VALUE = "after".',
            {"path": "fixture.py", "old_string": 'VALUE = "before"', "new_string": 'VALUE = "after"'},
        ),
    ],
)
def test_inspection_file_replacement_extracts_explicit_mutation(message, expected):
    assert al._parse_inspection_file_replacement(message) == expected


def test_inspection_file_replacement_rejects_broad_fix_request():
    assert al._parse_inspection_file_replacement(
        "Inspect /tmp/status.txt and fix the status."
    ) is None


def test_inspection_file_replacement_prefers_backtick_literals():
    assert al._parse_inspection_file_replacement(
        'Read fixture.py, replace the exact text `VALUE = "before"` '
        'with `VALUE = "after"`, then verify it.'
    ) == {
        "path": "fixture.py",
        "old_string": 'VALUE = "before"',
        "new_string": 'VALUE = "after"',
    }


def test_semantic_inspection_edit_is_reconciled_to_exact_assignment():
    edit = {
        "path": "fixture.py",
        "old_string": "VALUE from before",
        "new_string": "after",
    }

    repaired = al._reconcile_inspection_edit_with_read(
        edit,
        'VALUE = "before"\n',
    )

    assert repaired == {
        "path": "fixture.py",
        "old_string": 'VALUE = "before"\n',
        "new_string": 'VALUE = "after"\n',
    }


def test_semantic_inspection_edit_does_not_guess_unrelated_source():
    edit = {
        "path": "fixture.py",
        "old_string": "VALUE from before",
        "new_string": "after",
    }

    assert al._reconcile_inspection_edit_with_read(edit, "OTHER = 'before'\n") == edit


def _run_agent(monkeypatch, *, message, workspace):
    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *args, **kwargs: 10, raising=False)
    monkeypatch.setattr(al, "blocked_tools_for_owner", lambda owner: set(), raising=False)

    calls = []

    async def fake_stream(_candidates, messages, **kwargs):
        calls.append({"messages": messages, "tools": kwargs.get("tools")})
        yield "data: " + json.dumps({"delta": "No action"}) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", fake_stream, raising=False)

    async def run():
        stream = al.stream_agent_loop(
            "https://api.openai.com/v1",
            "gpt-test",
            [{"role": "user", "content": message}],
            max_rounds=2,
            owner="pewds",
            workspace=workspace,
            relevant_tools={"edit_file", "read_file"},
        )
        return [chunk async for chunk in stream]

    return calls, asyncio.run(run())


def test_exact_edit_is_dispatched_without_calling_model(monkeypatch, tmp_path):
    path = tmp_path / "status.txt"
    path.write_text("status=old\n")
    message = f"In {path}, change status=old to status=new."

    async def fake_execute(block, **_kwargs):
        args = json.loads(block.content)
        assert block.tool_type == "edit_file"
        assert args == {"path": str(path), "old_string": "status=old", "new_string": "status=new"}
        path.write_text("status=new\n")
        return "edit_file: updated", {"output": f"Edited {path}", "exit_code": 0}

    monkeypatch.setattr(al, "execute_tool_block", fake_execute, raising=False)

    calls, chunks = _run_agent(monkeypatch, message=message, workspace=str(tmp_path))
    events = [
        json.loads(chunk[6:])
        for chunk in chunks
        if chunk.startswith("data: {")
    ]

    assert calls == []
    assert path.read_text() == "status=new\n"
    assert [event.get("tool") for event in events if event.get("type") == "tool_start"] == ["edit_file"]
    assert any(event.get("delta") == "Done." for event in events)
    assert any(event.get("data", {}).get("direct_exact_file_edit") for event in events)


def test_exact_edit_failure_stops_without_model_or_shell(monkeypatch, tmp_path):
    path = tmp_path / "status.txt"
    path.write_text("status=other\n")
    message = f"In {path}, change status=old to status=new."

    async def fake_execute(block, **_kwargs):
        assert block.tool_type == "edit_file"
        return "edit_file: failed", {"error": "old_string was not found", "exit_code": 1}

    monkeypatch.setattr(al, "execute_tool_block", fake_execute, raising=False)

    calls, chunks = _run_agent(monkeypatch, message=message, workspace=str(tmp_path))
    output = "".join(chunks)

    assert calls == []
    assert path.read_text() == "status=other\n"
    assert "not found" in output
    assert "clarify which occurrence" in output


def test_inspect_first_control_still_reaches_model(monkeypatch, tmp_path):
    path = tmp_path / "status.txt"
    path.write_text("status=old\n")
    message = f"Read {path} first, then change status=old to status=new."

    calls, _chunks = _run_agent(monkeypatch, message=message, workspace=str(tmp_path))

    assert len(calls) == 1
    assert path.read_text() == "status=old\n"


def test_edit_file_counts_as_effectful_work():
    assert "edit_file" in al._VERIFIER_EFFECTFUL_TOOLS
