from __future__ import annotations

import json

import pytest

from src.agent_evidence import CompletionRequirements
from src.agent_trace import (
    CanonicalTrace,
    TraceKind,
    audit_completion_trace,
    completion_from_trace,
    decode_typed_trace,
    decode_native_trace,
    decode_nemo_trajectory,
    tool_events_from_trace,
)


REQUIREMENTS = CompletionRequirements(
    required_artifacts=("/workspace/app.py",),
    verifier_required=True,
    executable_verifier_available=True,
    verifier_commands=("./test.sh",),
)


def _sse(value):
    return {"elapsed_s": 1.0, "sse": f"data: {json.dumps(value)}\n\n"}


def test_native_trace_round_trip_and_completion_rehydration(tmp_path):
    records = [
        {"type": "media_ingress", "data": {"artifacts": []}},
        _sse({"type": "tool_start", "tool": "write_file", "command": "/workspace/app.py\nVALUE = 2", "round": 1}),
        _sse({"type": "tool_output", "tool": "write_file", "command": "/workspace/app.py\nVALUE = 2", "output": "wrote", "exit_code": 0}),
        _sse({"type": "tool_start", "tool": "bash", "command": "./test.sh", "round": 1}),
        _sse({"type": "tool_output", "tool": "bash", "command": "./test.sh", "output": "passed", "exit_code": 0}),
        {"elapsed_s": 2.0, "sse": "data: [DONE]\n\n"},
    ]
    trace = decode_native_trace(records, run_id="native-1")
    path = trace.write_jsonl(tmp_path / "trace.jsonl")
    restored = CanonicalTrace.read_jsonl(path)

    assert restored.to_list() == trace.to_list()
    assert completion_from_trace(restored, REQUIREMENTS).to_dict()["status"] == "verified"
    assert restored.summary()["complete"] is True


def test_native_trace_run_start_records_runtime_revision():
    trace = decode_native_trace(
        [{"sse": "data: [DONE]\n\n"}],
        run_id="native-revision",
        runtime_revision="revision-under-test",
    )

    assert trace.events[0].kind == TraceKind.RUN_START
    assert trace.events[0].payload["runtime_revision"] == "revision-under-test"


def test_typed_trace_run_start_records_runtime_revision():
    trace = decode_typed_trace(
        [
            {
                "type": "trace_start",
                "trace_id": "typed-revision",
                "task_id": "task",
                "model": "model",
                "runtime_revision": "revision-under-test",
            },
            {"type": "trace_end", "trace_id": "typed-revision"},
        ]
    )

    assert trace.events[0].kind == TraceKind.RUN_START
    assert trace.events[0].payload["runtime_revision"] == "revision-under-test"


def test_typed_and_native_codecs_rehydrate_same_completion_contract():
    native = decode_native_trace(
        [
            _sse({"type": "tool_start", "tool": "write_file", "command": "/workspace/app.py\nVALUE = 2", "round": 1}),
            _sse({"type": "tool_output", "tool": "write_file", "command": "/workspace/app.py\nVALUE = 2", "output": "wrote", "exit_code": 0}),
            _sse({"type": "tool_start", "tool": "bash", "command": "./test.sh", "round": 1}),
            _sse({"type": "tool_output", "tool": "bash", "command": "./test.sh", "output": "passed", "exit_code": 0}),
            {"sse": "data: [DONE]\n\n"},
        ],
        run_id="native",
    )
    typed = decode_typed_trace(
        [
            {"type": "trace_start", "trace_id": "typed", "task_id": "task", "model": "model"},
            {
                "type": "tool_dispatch",
                "trace_id": "typed",
                "tool_use_id": "write-1",
                "tool_name": "write_file",
                "endpoint_url": "local",
                "request_body": {"path": "/workspace/app.py", "content": "VALUE = 2"},
                "response_status": 200,
                "response_body": {"output": "wrote", "exit_code": 0},
            },
            {
                "type": "tool_dispatch",
                "trace_id": "typed",
                "tool_use_id": "verify-1",
                "tool_name": "bash",
                "endpoint_url": "local",
                "request_body": {"command": "./test.sh"},
                "response_status": 200,
                "response_body": {"output": "passed", "exit_code": 0},
            },
            {"type": "trace_end", "trace_id": "typed", "passed": True},
        ]
    )

    native_decision = completion_from_trace(native, REQUIREMENTS)
    typed_decision = completion_from_trace(typed, REQUIREMENTS)
    assert native_decision.status == typed_decision.status
    assert native_decision.can_complete == typed_decision.can_complete is True
    assert native_decision.missing_artifacts == typed_decision.missing_artifacts == ()


def test_nemo_codec_preserves_structured_arguments_and_observation_gaps():
    trace = decode_nemo_trajectory(
        {
            "schema_version": "1.0",
            "task_id": "task",
            "rollout_id": "rollout-7",
            "invocations": [{"kind": "agent_invocation", "invocation_id": "root", "status": "completed"}],
            "turns": [],
            "model_calls": [],
            "tool_calls": [
                {
                    "kind": "tool_call",
                    "invocation_id": "root",
                    "tool_call_id": "call-1",
                    "tool_name": "write_file",
                    "arguments": {"path": "/workspace/app.py", "content": "VALUE = 2"},
                    "output": {"output": "wrote", "exit_code": 0},
                    "status": "completed",
                }
            ],
            "gaps": [{"code": "model_call_ownership_unavailable", "invocation_id": "root"}],
        }
    )

    [event] = [event for event in trace.events if event.kind == TraceKind.TOOL_CALL]
    assert event.payload["arguments"] == {"path": "/workspace/app.py", "content": "VALUE = 2"}
    assert trace.summary()["observation_gaps"] == ["model_call_ownership_unavailable"]


def test_native_codec_records_unmatched_tool_result_as_gap():
    trace = decode_native_trace(
        [_sse({"type": "tool_output", "tool": "bash", "command": "pwd", "output": "/workspace", "exit_code": 0})],
        run_id="partial",
    )
    assert trace.summary()["observation_gaps"] == ["tool_result_call_unmatched", "trace_end_unavailable"]


def test_native_codec_links_explicit_preexecution_rejection():
    trace = decode_native_trace(
        [
            _sse({
                "type": "tool_output",
                "tool": "inspect_media",
                "command": '{"path":"/workspace/result.png"}',
                "output": "This exact successful call already returned evidence.",
                "exit_code": 1,
                "error": True,
                "execution_attempted": False,
                "blocked": True,
                "round": 4,
            }),
            {"sse": "data: [DONE]\n\n"},
        ],
        run_id="preexecution-rejection",
    )

    summary = trace.summary()
    assert summary["tool_linkage_valid"] is True
    assert summary["observation_gaps"] == []
    [call] = [event for event in trace.events if event.kind == TraceKind.TOOL_CALL]
    [result] = [event for event in trace.events if event.kind == TraceKind.TOOL_RESULT]
    assert call.correlation_id == result.correlation_id
    assert call.payload["execution_attempted"] is False
    assert call.payload["rejected_before_execution"] is True


def test_native_codec_preserves_model_response_reference():
    trace = decode_native_trace(
        [
            _sse({
                "type": "model_response_ref",
                "response_id": "response-neutral-1",
                "model": "policy-model",
                "round": 2,
            }),
            {"sse": "data: [DONE]\n\n"},
        ],
        run_id="model-ref",
    )

    [event] = [event for event in trace.events if event.kind == TraceKind.MODEL_CALL]
    assert event.correlation_id == "response-neutral-1"
    assert event.round == 2
    assert event.payload == {
        "response_id": "response-neutral-1",
        "model": "policy-model",
    }


def test_canonical_reader_rejects_noncontiguous_sequence(tmp_path):
    trace = decode_native_trace([{"sse": "data: [DONE]\n\n"}], run_id="run")
    rows = trace.to_list()
    rows[-1]["sequence"] = 99
    path = tmp_path / "bad.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(ValueError, match="contiguous"):
        CanonicalTrace.read_jsonl(path)


def test_tool_result_rehydration_keeps_call_identity():
    trace = decode_typed_trace(
        [
            {"type": "trace_start", "trace_id": "x", "task_id": "t", "model": "m"},
            {
                "type": "tool_dispatch",
                "trace_id": "x",
                "tool_use_id": "exact-call-id",
                "tool_name": "bash",
                "endpoint_url": "local",
                "request_body": {"command": "pwd"},
                "response_status": 200,
                "response_body": {"output": "/workspace", "exit_code": 0},
            },
            {"type": "trace_end", "trace_id": "x"},
        ]
    )
    [event] = tool_events_from_trace(trace)
    assert event["tool_call_id"] == "exact-call-id"
    assert event["command"] == "pwd"


def test_completion_audit_detects_source_replay_disagreement():
    trace = decode_native_trace(
        [
            _sse({
                "type": "completion_decision",
                "data": {
                    "status": "verified",
                    "can_complete": True,
                    "reason": "claimed",
                    "evidence_ids": [],
                    "missing_artifacts": [],
                },
            }),
            {"sse": "data: [DONE]\n\n"},
        ],
        run_id="disagreement",
    )

    audit = audit_completion_trace(trace, REQUIREMENTS)
    assert audit["persisted_available"] is True
    assert audit["agreement"] is False
    assert audit["differences"] == ["status", "can_complete", "missing_artifacts"]
    assert audit["recomputed"]["status"] == "blocked"


def test_completion_audit_replays_round_exhaustion_from_trace():
    trace = decode_native_trace(
        [
            _sse({"type": "rounds_exhausted", "rounds": 12}),
            _sse({
                "type": "completion_decision",
                "data": {
                    "status": "exhausted",
                    "can_complete": False,
                    "reason": "the run exhausted its model-round budget",
                    "evidence_ids": [],
                    "missing_artifacts": [],
                },
            }),
            {"sse": "data: [DONE]\n\n"},
        ],
        run_id="exhaustion-replay",
    )

    audit = audit_completion_trace(trace)
    assert audit["agreement"] is True
    assert audit["recomputed"]["status"] == "exhausted"


def test_completion_audit_explicit_exhaustion_override_wins():
    trace = decode_native_trace(
        [_sse({"type": "rounds_exhausted", "rounds": 12}), {"sse": "data: [DONE]\n\n"}],
        run_id="exhaustion-override",
    )

    audit = audit_completion_trace(trace, exhausted=False)
    assert audit["recomputed"]["status"] == "unverified"
