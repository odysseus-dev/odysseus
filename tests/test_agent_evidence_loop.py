import asyncio
import json

import src.agent_loop as agent_loop
from src.tool_parsing import ToolBlock
from src.tool_capabilities import ToolGateDecision
from tests.runtime_evidence_helpers import authoritative_executor


def _events(chunks):
    parsed = []
    for chunk in chunks:
        if not chunk.startswith("data: ") or chunk.startswith("data: [DONE]"):
            continue
        parsed.append(json.loads(chunk[6:]))
    return parsed


def test_deepseek_flash_visual_continuation_flattens_only_tool_visual_history():
    messages = [
        {"role": "system", "content": "system contract"},
        {"role": "user", "content": "original task"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c1", "type": "function", "function": {
                "name": "inspect_media", "arguments": "{}",
            }}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "preview follows"},
        {
            "role": "user",
            "metadata": {"source": "tool visual evidence", "trusted": False},
            "content": [
                {"type": "text", "text": "Visual evidence returned by tool execution."},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}},
            ],
        },
    ]

    flattened = agent_loop._deepseek_flash_visual_continuation(messages, "original task")

    assert [message["role"] for message in flattened] == ["system", "user"]
    assert "original task" in flattened[-1]["content"][0]["text"]
    assert flattened[-1]["content"][1]["image_url"]["url"].endswith("AAA")
    assert agent_loop._deepseek_flash_visual_continuation(
        messages[:-1], "original task"
    ) is None


def test_deepseek_flash_vision_compatibility_is_exact_model_only():
    assert agent_loop._is_deepseek_flash_vision_model("deepseek-flash")
    assert agent_loop._is_deepseek_flash_vision_model("provider/deepseek-flash")
    assert not agent_loop._is_deepseek_flash_vision_model("deepseek-v4-pro")
    assert not agent_loop._is_deepseek_flash_vision_model("deepseek-flash-preview")


def _patch_loop(monkeypatch, responses, captured_kwargs=None):
    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda owner: set())
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *args, **kwargs: 10)
    monkeypatch.setattr(agent_loop, "tool_result_should_arm_gate", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        agent_loop.ToolRunSecurityContext,
        "decision_for",
        lambda self, *args, **kwargs: ToolGateDecision(True),
    )

    async def execute(block, *args, **kwargs):
        return block.tool_type, {
            "output": f"executed {block.tool_type}",
            "exit_code": 0,
        }

    call_index = 0

    async def stream(_candidates, messages, **kwargs):
        nonlocal call_index
        if captured_kwargs is not None:
            captured_kwargs.append(kwargs)
        response = responses[min(call_index, len(responses) - 1)]
        call_index += 1
        yield f'data: {json.dumps({"delta": response})}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "execute_tool_block", authoritative_executor(execute))
    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", stream)
    return lambda: call_index


def _run_chunks(instruction, *, max_rounds=4, relevant_tools=None, runtime_context=None):
    async def collect():
        return [
            chunk
            async for chunk in agent_loop.stream_agent_loop(
                "http://unused.test/v1",
                "test-model",
                [{"role": "user", "content": instruction}],
                max_rounds=max_rounds,
                relevant_tools=relevant_tools or {"write_file"},
                owner="pewds",
                client_runtime_context=runtime_context or {"terminal_agent": True},
            )
        ]

    return asyncio.run(collect())


def _run(instruction, **kwargs):
    return _events(_run_chunks(instruction, **kwargs))


def test_failed_workspace_mutation_attempts_are_not_hidden_by_successful_probe():
    failed = ToolBlock("python", 'open("/workspace/answer.png", "wb")')
    probe = ToolBlock("inspect_media", '{"path":"/workspace/input.mp4"}')
    records = [
        {"tool_name": "python", "result": {"error": "conversion failed", "exit_code": 1}},
        {"tool_name": "inspect_media", "result": {"output": "evidence", "exit_code": 0}},
    ]

    assert agent_loop._failed_workspace_mutation_attempts([failed, probe], records) == 1


def test_terminal_completion_missing_artifact_does_not_add_model_rounds(monkeypatch):
    calls = _patch_loop(monkeypatch, ["Done without writing anything."])

    events = _run("Write answer.json", max_rounds=4)

    blocked = [event for event in events if event.get("type") == "completion_blocked"]
    assert blocked == []
    assert calls() == 1
    decision = next(event["data"] for event in events if event.get("type") == "completion_decision")
    assert decision["status"] == "blocked"
    assert decision["missing_artifacts"] == ["answer.json"]


def test_slice2_explanatory_request_does_not_add_verification_or_model_rounds(monkeypatch):
    calls = _patch_loop(monkeypatch, ['Tests pass when the command exits zero.'])
    events = _run('Explain how to write code and then test it.', max_rounds=1)
    assert calls() == 1
    decision = next(event['data'] for event in events if event.get('type') == 'completion_decision')
    assert decision['can_complete'] is True
    assert 'The task is incomplete' not in json.dumps(events)
    metrics = next(event['data'] for event in events if event.get('type') == 'metrics')
    assert not metrics['completion_requirements']['verifier_required']
    assert metrics['completion_gate']['additional_provider_calls'] == 0


def test_slice2_fabricated_execution_on_conversational_turn_does_not_add_rounds(monkeypatch):
    calls = _patch_loop(monkeypatch, ['I ran pytest and all tests passed.'])
    events = _run('Explain what pytest does.', max_rounds=1)
    assert calls() == 1
    final = next(event['content'] for event in events if event.get('type') == 'final_response')
    assert 'I ran pytest' not in final
    assert 'The task is incomplete' not in final
    metrics = next(event['data'] for event in events if event.get('type') == 'metrics')
    assert metrics['completion_gate']['additional_provider_calls'] == 0


def test_slice2_unsupported_test_report_does_not_add_model_rounds(monkeypatch):
    calls = _patch_loop(monkeypatch, ['I ran pytest and all 42 tests passed.'])
    events = _run('Run pytest.', max_rounds=4, relevant_tools={'bash'})
    assert calls() == 1
    decision = next(event['data'] for event in events if event.get('type') == 'completion_decision')
    assert decision['can_complete'] is False
    final = next(event['content'] for event in events if event.get('type') == 'final_response')
    assert final.startswith('The task is incomplete:')
    assert '42' not in final


def test_failed_trailing_tool_with_planning_prose_continues_artifact_task(monkeypatch):
    calls = _patch_loop(
        monkeypatch,
        [
            "I found the source and will inspect it now.\n```bash\nfalse\n```",
            "Done without creating the required artifact.",
        ],
    )

    async def fail_execute(block, *args, **kwargs):
        return block.tool_type, {
            "output": "no matches",
            "exit_code": 1,
        }

    monkeypatch.setattr(agent_loop, "execute_tool_block", authoritative_executor(fail_execute))

    events = _run(
        "Create answer.json after inspecting the source",
        max_rounds=3,
        relevant_tools={"bash", "write_file"},
    )

    assert calls() > 1
    assert any(
        event.get("type") == "completion_decision"
        and event.get("data", {}).get("missing_artifacts") == ["answer.json"]
        for event in events
    )


def test_exact_failed_call_is_blocked_across_planning_and_intervening_failure(monkeypatch):
    repeated = "python3 /tmp_workspace/classify_and_copy.py"
    executed = []
    _patch_loop(
        monkeypatch,
        [
            f'I will run the classifier now.\n<tool_call><invoke name="bash"><parameter name="command">{repeated}</parameter></invoke></tool_call>',
            "I will repair the variable first.\n<tool_call><invoke name=\"bash\"><parameter name=\"command\">python3 -c 'print(classifications)'</parameter></invoke></tool_call>",
            f'I will retry the classifier.\n<tool_call><invoke name="bash"><parameter name="command">{repeated}</parameter></invoke></tool_call>',
            "The work is complete.",
        ],
    )

    async def fail_execute(block, *args, **kwargs):
        executed.append(block.content)
        return block.tool_type, {"output": f"failed: {block.content}", "exit_code": 1}

    monkeypatch.setattr(agent_loop, "execute_tool_block", authoritative_executor(fail_execute))

    events = _run(
        "Create /tmp_workspace/results after classifying the files",
        max_rounds=4,
        relevant_tools={"bash", "write_file"},
        runtime_context={
            "terminal_agent": True,
            "completion_requirements": {
                "required_artifacts": ["/tmp_workspace/results"],
            },
        },
    )

    assert executed.count(repeated) == 1, events
    blocked = [event for event in events if event.get("type") == "tool_retry_blocked"]
    assert len(blocked) == 1
    assert blocked[0]["previous_round"] == 1
    assert blocked[0]["command"] == repeated


def test_exact_failed_call_can_retry_after_successful_workspace_mutation(monkeypatch):
    repeated = "python3 /tmp_workspace/classify_and_copy.py"
    executed = []
    _patch_loop(
        monkeypatch,
        [
            f'<tool_call><invoke name="bash"><parameter name="command">{repeated}</parameter></invoke></tool_call>',
            '<tool_call><invoke name="write_file"><parameter name="path">/tmp_workspace/repair.py</parameter><parameter name="content">fixed = True</parameter></invoke></tool_call>',
            f'<tool_call><invoke name="bash"><parameter name="command">{repeated}</parameter></invoke></tool_call>',
            "Repair attempted.",
        ],
    )

    async def execute(block, *args, **kwargs):
        executed.append((block.tool_type, block.content))
        if block.tool_type == "write_file":
            return block.tool_type, {"output": "written", "exit_code": 0}
        return block.tool_type, {"output": "classifier failed", "exit_code": 1}

    monkeypatch.setattr(agent_loop, "execute_tool_block", authoritative_executor(execute))

    events = _run(
        "Create /tmp_workspace/results after repairing and running the classifier",
        max_rounds=4,
        relevant_tools={"bash", "write_file"},
        runtime_context={
            "terminal_agent": True,
            "completion_requirements": {
                "required_artifacts": ["/tmp_workspace/results"],
            },
        },
    )

    assert sum(content == repeated for _, content in executed) == 2
    assert not any(event.get("type") == "tool_retry_blocked" for event in events)


def test_terminal_artifact_task_repairs_after_consecutive_failed_batches(monkeypatch):
    executed = []
    _patch_loop(
        monkeypatch,
        [
            '<tool_call><invoke name="python"><parameter name="code">print(missing_one)</parameter></invoke></tool_call>',
            '<tool_call><invoke name="python"><parameter name="code">print(missing_two)</parameter></invoke></tool_call>',
            '<tool_call><invoke name="python"><parameter name="code">print(missing_three)</parameter></invoke></tool_call>',
            '<tool_call><invoke name="python"><parameter name="code">print(missing_four)</parameter></invoke></tool_call>',
            '<tool_call><invoke name="python"><parameter name="code">print(missing_five)</parameter></invoke></tool_call>',
            '<tool_call><invoke name="write_file"><parameter name="path">answer.json</parameter><parameter name="content">{\"ok\": true}</parameter></invoke></tool_call>',
            "Created answer.json.",
        ],
    )

    async def execute(block, *args, **kwargs):
        executed.append((block.tool_type, block.content))
        if block.tool_type == "write_file":
            return block.tool_type, {"output": "written", "exit_code": 0}
        return block.tool_type, {
            "error": f"NameError from {block.content}",
            "exit_code": 1,
        }

    monkeypatch.setattr(agent_loop, "execute_tool_block", authoritative_executor(execute))

    events = _run(
        "Create answer.json and verify it",
        max_rounds=7,
        relevant_tools={"python", "write_file"},
        runtime_context={
            "terminal_agent": True,
            "failed_tool_round_limit": 5,
            "completion_requirements": {
                "required_artifacts": ["answer.json"],
            },
        },
    )

    repairs = [event for event in events if event.get("type") == "artifact_repair_required"]
    assert len(repairs) == 1
    assert repairs[0]["attempt"] == 1
    assert "missing_five" in repairs[0]["last_failure"]
    assert not any(
        event.get("type") == "loop_breaker_triggered"
        and event.get("reason") == "consecutive_tool_failures"
        for event in events
    )
    assert any(tool == "write_file" for tool, _ in executed)
    decision = next(
        event["data"]
        for event in events
        if event.get("type") == "completion_decision"
    )
    assert decision["can_complete"] is True


def test_varied_failed_artifact_mutations_have_cumulative_cap(monkeypatch):
    executed = []
    _patch_loop(
        monkeypatch,
        [
            '<tool_call><invoke name="python"><parameter name="code">'
            f'open("/workspace/answer.json", "w").write(missing_{index})'
            '</parameter></invoke></tool_call>'
            for index in range(1, 9)
        ],
    )

    async def execute(block, *args, **kwargs):
        executed.append((block.tool_type, block.content))
        return block.tool_type, {
            "error": f"NameError from {block.content}",
            "exit_code": 1,
        }

    monkeypatch.setattr(agent_loop, "execute_tool_block", authoritative_executor(execute))

    events = _run(
        "Create answer.json and verify it",
        max_rounds=8,
        relevant_tools={"python", "write_file"},
        runtime_context={
            "terminal_agent": True,
            "failed_tool_round_limit": 99,
            "completion_requirements": {
                "required_artifacts": ["answer.json"],
            },
        },
    )

    guard = next(
        event for event in events
        if event.get("type") == "loop_breaker_triggered"
        and event.get("reason") == "cumulative_artifact_mutation_failures"
    )
    assert guard["failed_batches"] == 6
    assert len(executed) == 6


def test_exact_successful_read_is_blocked_until_workspace_changes(monkeypatch):
    inspection = "find /tmp_workspace/results -type f | wc -l"
    executed = []
    _patch_loop(
        monkeypatch,
        [
            f'I found partial output and will count it.\n<tool_call><invoke name="bash"><parameter name="command">{inspection}</parameter></invoke></tool_call>',
            f'I should check the count once more.\n<tool_call><invoke name="bash"><parameter name="command">{inspection}</parameter></invoke></tool_call>',
            '<tool_call><invoke name="write_file"><parameter name="path">answer.json</parameter><parameter name="content">{\"ok\": true}</parameter></invoke></tool_call>',
            "Created answer.json.",
        ],
    )

    async def execute(block, *args, **kwargs):
        executed.append((block.tool_type, block.content))
        if block.tool_type == "write_file":
            return block.tool_type, {"output": "written", "exit_code": 0}
        return block.tool_type, {"output": "7", "exit_code": 0}

    monkeypatch.setattr(agent_loop, "execute_tool_block", authoritative_executor(execute))

    events = _run(
        "Create answer.json from the inspected workspace",
        max_rounds=4,
        relevant_tools={"bash", "write_file"},
        runtime_context={
            "terminal_agent": True,
            "completion_requirements": {"required_artifacts": ["answer.json"]},
        },
    )

    assert sum(content == inspection for _, content in executed) == 1
    blocked = [
        event for event in events
        if event.get("type") == "tool_retry_blocked"
        and event.get("reason") == "repeated_read_only_call"
    ]
    assert len(blocked) == 1
    assert blocked[0]["previous_round"] == 1
    assert any(tool == "write_file" for tool, _ in executed)


def test_missing_evidence_does_not_generate_later_fenced_body_or_mutation(monkeypatch):
    executed = []
    calls = _patch_loop(
        monkeypatch,
        [
            "Done without writing anything.",
            "Still done without writing anything.",
            '```json\n{"ok": true}\n```',
            "Created answer.json.",
        ],
    )
    original_execute = agent_loop.execute_tool_block

    async def record_execute(block, *args, **kwargs):
        executed.append(block)
        return await original_execute(block, *args, **kwargs)

    monkeypatch.setattr(agent_loop, "execute_tool_block", authoritative_executor(record_execute))

    events = _run("Write answer.json", max_rounds=5)

    assert executed == []
    assert calls() == 1
    decision = next(
        event["data"]
        for event in events
        if event.get("type") == "completion_decision"
    )
    assert decision["status"] == "blocked"
    assert decision["can_complete"] is False


def test_successful_artifact_write_emits_satisfied_completion(monkeypatch):
    calls = _patch_loop(
        monkeypatch,
        [
            '```write_file\nanswer.json\n{"ok": true}\n```',
            "Done. Created answer.json.",
        ],
    )

    events = _run("Write answer.json")

    assert calls() == 2, events
    assert not any(event.get("type") == "completion_blocked" for event in events)
    decision = next(event["data"] for event in events if event.get("type") == "completion_decision")
    assert decision["status"] == "satisfied"
    assert decision["can_complete"] is True
    metrics = next(event["data"] for event in events if event.get("type") == "metrics")
    assert metrics["completion_decision"] == decision
    assert metrics["completion_requirements"] == {
        "required_artifacts": ["answer.json"],
        "verifier_required": False,
        "executable_verifier_available": False,
        "verifier_commands": [],
        "workspace_root": "",
    }
    assert any(
        evidence["kind"] == "artifact_mutation"
        for evidence in metrics["evidence_events"]
    )


def test_current_artifact_inspection_requires_post_mutation_matching_path():
    write = {
        "tool": "write_file", "command": "/workspace/output.html\n<body/>",
        "exit_code": 0,
    }
    source_inspection = {
        "tool": "private_browser", "command": '{"url":"file:///workspace/source.html"}',
        "exit_code": 0,
    }
    output_inspection = {
        "tool": "private_browser", "command": '{"url":"file:///workspace/output.html"}',
    }

    assert not agent_loop._artifact_has_current_inspection(
        [source_inspection, write], ["/workspace/output.html"]
    )
    assert agent_loop._artifact_has_current_inspection(
        [write, output_inspection], ["/workspace/output.html"]
    )
    assert not agent_loop._artifact_has_current_inspection(
        [write, output_inspection, write], ["/workspace/output.html"]
    )


def test_verified_artifact_survives_provider_error_during_finish_round(monkeypatch):
    calls = 0
    _patch_loop(monkeypatch, [""])

    async def stream(_candidates, messages, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            tool_calls = [
                {
                    "name": "write_file",
                    "arguments": json.dumps({
                        "path": "/workspace/output.html",
                        "content": "<main>complete</main>",
                    }),
                },
                {
                    "name": "private_browser",
                    "arguments": json.dumps({
                        "action": "open",
                        "url": "file:///workspace/output.html",
                    }),
                },
            ]
            yield f'data: {json.dumps({"type": "tool_calls", "calls": tool_calls})}\n\n'
            yield "data: [DONE]\n\n"
            return
        yield 'event: error\ndata: {"status": 504, "error": "stream timeout"}\n\n'

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", stream)

    chunks = _run_chunks(
        "Create /workspace/output.html",
        max_rounds=5,
        relevant_tools={"write_file", "private_browser"},
        runtime_context={
            "terminal_agent": True,
            "surface": "odysseus-native",
            "completion_requirements": {
                "required_artifacts": ["/workspace/output.html"],
            },
        },
    )

    events = _events(chunks)
    assert calls == 2
    assert chunks[-1] == 'event: error\ndata: {"status": 504, "error": "stream timeout"}\n\n'
    assert not any(chunk.strip() == 'data: [DONE]' for chunk in chunks)
    decision = next(event['data'] for event in events if event.get('type') == 'completion_decision')
    assert decision['can_complete'] is False
    assert decision['status'] == 'failed'
    assert not any(event.get("type") == "agent_terminal" for event in events)
    final = next(event for event in events if event.get("type") == "final_response")
    assert "output.html" in final["content"]
    assert final['content'].startswith('The task is incomplete:')


def test_uninspected_artifact_still_fails_on_provider_error(monkeypatch):
    calls = 0
    _patch_loop(monkeypatch, [""])

    async def stream(_candidates, messages, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            tool_call = {
                "name": "write_file",
                "arguments": json.dumps({
                    "path": "/workspace/answer.json",
                    "content": '{"status": "unverified"}',
                }),
            }
            yield f'data: {json.dumps({"type": "tool_calls", "calls": [tool_call]})}\n\n'
            yield "data: [DONE]\n\n"
            return
        yield 'event: error\ndata: {"status": 504, "error": "stream timeout"}\n\n'

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", stream)

    chunks = _run_chunks(
        "Create /workspace/answer.json",
        max_rounds=4,
        relevant_tools={"write_file"},
        runtime_context={
            "terminal_agent": True,
            "surface": "odysseus-native",
            "completion_requirements": {
                "required_artifacts": ["/workspace/answer.json"],
            },
        },
    )

    events = _events(chunks)
    assert calls == 2
    assert chunks[-1] == 'event: error\ndata: {"status": 504, "error": "stream timeout"}\n\n'
    assert not any(chunk.strip() == 'data: [DONE]' for chunk in chunks)
    decision = next(event['data'] for event in events if event.get('type') == 'completion_decision')
    assert decision['can_complete'] is False
    assert decision['status'] == 'failed'
    terminal = next(
        (event for event in events if event.get("type") == "agent_terminal"),
        None,
    )
    assert terminal is not None, events
    assert terminal["data"]["failed"] is True
    assert terminal["data"]["failure"]["status"] == 504
    assert '[Agent stopped: Model request failed (HTTP 504)]' in terminal['data']['round_texts'][-1]


def test_verified_artifact_gets_only_one_finish_nudge(monkeypatch):
    _patch_loop(
        monkeypatch,
        [
            '```write_file\noutput.html\n<body>done</body>\n```',
            '<tool_call><invoke name="private_browser"><parameter name="action">open</parameter><parameter name="url">file:///workspace/output.html</parameter></invoke></tool_call>',
            '<tool_call><invoke name="read_file"><parameter name="path">output.html</parameter></invoke></tool_call>',
            "Done. Created and checked output.html.",
        ],
    )

    events = _run(
        "Create output.html",
        max_rounds=5,
        relevant_tools={"write_file", "private_browser", "read_file"},
    )

    nudges = [event for event in events if event.get("type") == "artifact_finish_nudge"]
    assert len(nudges) == 1
    assert nudges[0]["reason"] == "artifact_complete_and_currently_inspected"


def test_verified_artifact_finish_round_preserves_correction_budget(monkeypatch):
    captured = []
    _patch_loop(
        monkeypatch,
        [
            '```write_file\noutput.html\n<body>done</body>\n```',
            '<tool_call><invoke name="private_browser"><parameter name="action">open</parameter><parameter name="url">file:///workspace/output.html</parameter></invoke></tool_call>',
            "Done. Created and checked output.html.",
        ],
        captured_kwargs=captured,
    )
    events = _run(
        "Create output.html",
        max_rounds=5,
        relevant_tools={"write_file", "private_browser"},
    )

    assert any(event.get("type") == "artifact_finish_nudge" for event in events)
    assert captured[-1]["candidate_request_factory"]
    # Request kwargs are resolved lazily for each endpoint; exercise the
    # primary factory exactly as the fallback streamer does.
    request = asyncio.run(captured[-1]["candidate_request_factory"](0, "http://unused.test/v1", "test-model", {}))
    # The first post-inspection response may need to rewrite the complete
    # artifact.  Only the response after that correction is verified is
    # bounded to a short final answer.
    assert request["kwargs"]["max_tokens"] > 2048


def test_finish_nudge_does_not_accept_unfinished_correction_promise(monkeypatch):
    calls = _patch_loop(
        monkeypatch,
        [
            '```write_file\n/workspace/output.html\n<body>draft</body>\n```',
            "The draft has a defect. I should complete output.html by adding labels.",
            '```write_file\n/workspace/output.html\n<body>corrected</body>\n```',
            "Done. Corrected and checked output.html.",
        ],
    )

    events = _run(
        "Create /workspace/output.html",
        max_rounds=6,
        relevant_tools={"write_file", "private_browser"},
        runtime_context={
            "terminal_agent": True,
            "surface": "odysseus-native",
            "completion_requirements": {
                "required_artifacts": ["/workspace/output.html"],
            },
        },
    )

    assert calls() == 4, events
    assert len([
        event for event in events if event.get("type") == "artifact_finish_nudge"
    ]) == 1
    assert len([
        event for event in events
        if event.get("type") == "artifact_finish_after_verified_correction"
    ]) == 1
    decision = next(
        event["data"] for event in events
        if event.get("type") == "completion_decision"
    )
    assert decision["can_complete"] is True


def test_discovered_verifier_runs_in_same_batch_after_mutation(monkeypatch):
    calls = _patch_loop(
        monkeypatch,
        [
            '```write_file\nanswer.json\n{"ok": true}\n```',
            "Done without running the task check.",
            "Done again without selecting a tool.",
        ],
    )

    events = _run(
        "Write answer.json",
        max_rounds=6,
        relevant_tools={"write_file", "bash"},
        runtime_context={
            "terminal_agent": True,
            "completion_requirements": {
                "required_artifacts": ["answer.json"],
                "verifier_required": True,
                "executable_verifier_available": True,
                "verifier_commands": ["./test.sh"],
            },
        },
    )

    blocked = [event for event in events if event.get("type") == "completion_blocked"]
    assert blocked == []
    decision = next(event["data"] for event in events if event.get("type") == "completion_decision")
    assert decision["status"] == "verified"
    assert calls() == 1
    assert any(
        event.get("type") == "tool_start"
        and event.get("tool") == "bash"
        and event.get("command") == "./test.sh"
        for event in events
    )


def test_terminal_shell_wrapped_write_file_is_recovered_without_shell_execution():
    recovered = agent_loop._recover_shell_wrapped_file_tool(
        ToolBlock("bash", 'write_file /workspace/app.py "VALUE = 2"')
    )

    assert recovered.tool_type == "write_file"
    assert recovered.content == "/workspace/app.py\nVALUE = 2"


def test_terminal_multiline_shell_wrapped_write_file_is_recovered():
    recovered = agent_loop._recover_shell_wrapped_file_tool(
        ToolBlock("bash", "write_file\n/workspace/app.py\nVALUE = '<ok>'\n")
    )

    assert recovered.tool_type == "write_file"
    assert recovered.content == "/workspace/app.py\nVALUE = '<ok>'"


def test_terminal_shell_wrapped_edit_file_json_is_recovered_with_literal_operators():
    recovered = agent_loop._recover_shell_wrapped_file_tool(
        ToolBlock(
            "bash",
            'edit_file {"path": "/workspace/app.py", "old_string": "x >> y", '
            '"new_string": "x < y"}',
        )
    )

    assert recovered.tool_type == "edit_file"
    assert json.loads(recovered.content) == {
        "path": "/workspace/app.py",
        "old_string": "x >> y",
        "new_string": "x < y",
        "replace_all": False,
    }


def test_ambiguous_shell_wrapped_file_tool_is_not_recovered():
    original = ToolBlock("bash", "write_file app.py value && ./test.sh")

    assert agent_loop._recover_shell_wrapped_file_tool(original) == original


def test_terminal_adjacent_fenced_write_body_is_recovered_for_required_artifact():
    recovered = agent_loop._recover_adjacent_fenced_write_file(
        """```bash
write_file /workspace/report.md
```

```markdown
# Report

Verified result.
```""",
        ["/workspace/report.md"],
    )

    assert recovered == ToolBlock(
        "write_file",
        "/workspace/report.md\n# Report\n\nVerified result.",
    )


def test_terminal_fenced_body_before_name_only_write_uses_single_required_artifact():
    recovered = agent_loop._recover_adjacent_fenced_write_file(
        """```html
<main>Complete output</main>
```

```bash
write_file
```""",
        ["/workspace/output.html"],
    )

    assert recovered == ToolBlock(
        "write_file",
        "/workspace/output.html\n<main>Complete output</main>",
    )


def test_terminal_adjacent_fenced_write_rejects_unrequired_path():
    recovered = agent_loop._recover_adjacent_fenced_write_file(
        """```bash
write_file /workspace/unrequested.md
```
```text
content
```""",
        ["/workspace/report.md"],
    )

    assert recovered is None


def test_terminal_recovers_unlabeled_ffmpeg_fence_for_required_media_artifact():
    recovered = agent_loop._recover_fenced_media_shell_command(
        """I will create the requested video now.
```
ffmpeg -y -i /workspace/in.mp4 -vf scale=640:360 /workspace/merged.mp4
```""",
        ["/workspace/merged.mp4"],
    )

    assert recovered == ToolBlock(
        "bash",
        "ffmpeg -y -i /workspace/in.mp4 -vf scale=640:360 /workspace/merged.mp4",
    )


def test_terminal_unlabeled_shell_fence_must_target_required_media_artifact():
    assert agent_loop._recover_fenced_media_shell_command(
        """```
ffmpeg -y -i /workspace/in.mp4 /workspace/other.mp4
```""",
        ["/workspace/merged.mp4"],
    ) is None


def test_terminal_unlabeled_non_media_command_remains_inert():
    assert agent_loop._recover_fenced_media_shell_command(
        """```
python -c 'print(1)'
```""",
        ["/workspace/result.txt"],
    ) is None


def test_explicit_run_the_named_script_is_a_verification_command():
    request = (
        "Write a Python script named /workspace/calculate_ratio.py that loads "
        "the JSON and saves its result. Then run the script."
    )

    assert agent_loop._requested_verification_command(request) == (
        "python /workspace/calculate_ratio.py"
    )
    assert agent_loop._requested_post_edit_verification(request)


def test_run_the_script_is_not_inferred_when_multiple_scripts_are_named():
    request = (
        "Write /workspace/prepare.py and /workspace/calculate.py, then run the script."
    )

    assert agent_loop._requested_verification_command(request) == ""


def test_inspect_saved_file_requests_artifact_readback_verification():
    request = (
        "Create /workspace/output/results.csv, inspect the saved file, "
        "then summarize completion."
    )

    assert agent_loop._requested_post_edit_verification(request)
    assert agent_loop._requested_artifact_readback(request)
