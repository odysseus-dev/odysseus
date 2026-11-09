"""Canonical append-only traces and codecs for Odysseus agent adapters."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from src.agent_evidence import CompletionDecision, CompletionRequirements, EvidenceLedger


TRACE_SCHEMA_VERSION = "1.0"


class TraceKind(str, Enum):
    RUN_START = "run_start"
    INVOCATION = "invocation"
    MODEL_TURN = "model_turn"
    MODEL_DELTA = "model_delta"
    MODEL_CALL = "model_call"
    MESSAGE = "message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    MEDIA_INGRESS = "media_ingress"
    COMPACTION = "compaction"
    EVIDENCE = "evidence"
    COMPLETION = "completion"
    USAGE = "usage"
    GRADING = "grading"
    RUNTIME = "runtime"
    OBSERVATION_GAP = "observation_gap"
    RUN_END = "run_end"


@dataclass(frozen=True)
class CanonicalTraceEvent:
    event_id: str
    run_id: str
    sequence: int
    kind: TraceKind
    source: str
    invocation_id: str = "root"
    timestamp: str = ""
    timestamp_s: float | None = None
    round: int | None = None
    correlation_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    schema_version: str = TRACE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["kind"] = self.kind.value
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CanonicalTraceEvent":
        if value.get("schema_version") != TRACE_SCHEMA_VERSION:
            raise ValueError(f"unsupported canonical trace schema: {value.get('schema_version')!r}")
        sequence = value.get("sequence")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
            raise ValueError("canonical trace sequence must be a non-negative integer")
        payload = value.get("payload")
        if not isinstance(payload, Mapping):
            raise ValueError("canonical trace payload must be an object")
        return cls(
            event_id=str(value.get("event_id") or ""),
            run_id=str(value.get("run_id") or ""),
            sequence=sequence,
            kind=TraceKind(str(value.get("kind") or "")),
            source=str(value.get("source") or ""),
            invocation_id=str(value.get("invocation_id") or "root"),
            timestamp=str(value.get("timestamp") or ""),
            timestamp_s=(float(value["timestamp_s"]) if isinstance(value.get("timestamp_s"), (int, float)) else None),
            round=(int(value["round"]) if isinstance(value.get("round"), int) and not isinstance(value.get("round"), bool) else None),
            correlation_id=str(value.get("correlation_id") or ""),
            payload=dict(payload),
        )


class CanonicalTrace:
    def __init__(self, run_id: str, events: Sequence[CanonicalTraceEvent] = ()) -> None:
        self.run_id = str(run_id or "")
        if not self.run_id:
            raise ValueError("canonical trace run_id is required")
        self.events = list(events)
        self._validate()

    def _validate(self) -> None:
        ids: set[str] = set()
        for expected, event in enumerate(self.events):
            if event.run_id != self.run_id:
                raise ValueError("canonical event run_id does not match trace")
            if event.sequence != expected:
                raise ValueError("canonical trace sequence must be contiguous and ordered")
            if not event.event_id or event.event_id in ids:
                raise ValueError("canonical event_id must be present and unique")
            ids.add(event.event_id)

    def to_list(self) -> list[dict[str, Any]]:
        return [event.to_dict() for event in self.events]

    def write_jsonl(self, path: str | Path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            for event in self.events:
                handle.write(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
        temporary.replace(destination)
        return destination

    @classmethod
    def read_jsonl(cls, path: str | Path) -> "CanonicalTrace":
        events: list[CanonicalTraceEvent] = []
        with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
            for line_no, raw in enumerate(handle, start=1):
                if not raw.strip():
                    continue
                try:
                    events.append(CanonicalTraceEvent.from_dict(json.loads(raw)))
                except (ValueError, json.JSONDecodeError) as exc:
                    raise ValueError(f"invalid canonical trace at line {line_no}: {exc}") from exc
        if not events:
            raise ValueError("canonical trace is empty")
        return cls(events[0].run_id, events)

    def summary(self) -> dict[str, Any]:
        counts = Counter(event.kind.value for event in self.events)
        gap_codes = [
            str(event.payload.get("code") or "unknown")
            for event in self.events
            if event.kind == TraceKind.OBSERVATION_GAP
        ]
        tool_call_id_list = [
            event.correlation_id
            for event in self.events
            if event.kind == TraceKind.TOOL_CALL and event.correlation_id
        ]
        tool_call_ids = set(tool_call_id_list)
        tool_result_ids = [
            event.correlation_id
            for event in self.events
            if event.kind == TraceKind.TOOL_RESULT
        ]
        unmatched_result_ids = sorted({
            correlation_id
            for correlation_id in tool_result_ids
            if not correlation_id or correlation_id not in tool_call_ids
        })
        duplicate_result_ids = sorted(
            correlation_id
            for correlation_id, count in Counter(tool_result_ids).items()
            if correlation_id and count > 1
        )
        duplicate_call_ids = sorted(
            correlation_id
            for correlation_id, count in Counter(tool_call_id_list).items()
            if correlation_id and count > 1
        )
        missing_result_ids = sorted(tool_call_ids - set(tool_result_ids))
        linkage_gap_codes = {
            "tool_result_call_unmatched",
            "tool_result_unavailable",
        }
        tool_linkage_valid = not (
            unmatched_result_ids
            or duplicate_result_ids
            or duplicate_call_ids
            or missing_result_ids
            or linkage_gap_codes.intersection(gap_codes)
        )
        return {
            "schema_version": TRACE_SCHEMA_VERSION,
            "run_id": self.run_id,
            "events": len(self.events),
            "event_kinds": dict(sorted(counts.items())),
            "observation_gaps": gap_codes,
            "tool_linkage_valid": tool_linkage_valid,
            "unmatched_tool_result_ids": unmatched_result_ids,
            "duplicate_tool_result_ids": duplicate_result_ids,
            "duplicate_tool_call_ids": duplicate_call_ids,
            "missing_tool_result_ids": missing_result_ids,
            "complete": any(event.kind == TraceKind.RUN_END for event in self.events),
        }


class _TraceBuilder:
    def __init__(self, run_id: str, source: str) -> None:
        self.run_id = str(run_id or "")
        self.source = source
        self.events: list[CanonicalTraceEvent] = []

    def add(
        self,
        kind: TraceKind,
        payload: Mapping[str, Any] | None = None,
        *,
        invocation_id: str = "root",
        timestamp: str = "",
        timestamp_s: float | None = None,
        round: int | None = None,
        correlation_id: str = "",
    ) -> CanonicalTraceEvent:
        sequence = len(self.events)
        identity = f"{self.run_id}:{sequence}:{kind.value}:{invocation_id}:{correlation_id}"
        event = CanonicalTraceEvent(
            event_id="trace-" + hashlib.sha256(identity.encode()).hexdigest()[:20],
            run_id=self.run_id,
            sequence=sequence,
            kind=kind,
            source=self.source,
            invocation_id=invocation_id,
            timestamp=timestamp,
            timestamp_s=timestamp_s,
            round=round,
            correlation_id=correlation_id,
            payload=dict(payload or {}),
        )
        self.events.append(event)
        return event

    def gap(self, code: str, detail: str = "", *, invocation_id: str = "root") -> None:
        payload = {"code": code}
        if detail:
            payload["detail"] = detail
        self.add(TraceKind.OBSERVATION_GAP, payload, invocation_id=invocation_id)

    def build(self) -> CanonicalTrace:
        return CanonicalTrace(self.run_id, self.events)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _command_from_arguments(arguments: Any) -> str:
    if isinstance(arguments, str):
        return arguments
    if not isinstance(arguments, Mapping):
        return json.dumps(_jsonable(arguments), sort_keys=True)
    for key in ("command", "cmd", "shell"):
        if isinstance(arguments.get(key), str):
            return str(arguments[key])
    if isinstance(arguments.get("path"), str):
        if "content" in arguments:
            return f"{arguments['path']}\n{arguments.get('content') or ''}"
        return str(arguments["path"])
    return json.dumps(_jsonable(arguments), sort_keys=True)


def _exit_code(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, Mapping):
        for key in ("exit_code", "returncode", "code"):
            item = value.get(key)
            if isinstance(item, int) and not isinstance(item, bool):
                return item
    return None


def _native_sse_payload(raw: str) -> dict[str, Any] | str | None:
    value = str(raw or "").strip()
    if not value.startswith("data:"):
        return None
    value = value[5:].strip()
    if value == "[DONE]":
        return "[DONE]"
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def decode_native_trace(
    records: Iterable[Mapping[str, Any]],
    *,
    run_id: str,
    runtime_revision: str = "",
) -> CanonicalTrace:
    """Decode native SSE capture rows into canonical trace events."""

    builder = _TraceBuilder(run_id, "odysseus_native")
    run_payload = {"adapter": "native_sse"}
    if runtime_revision:
        run_payload["runtime_revision"] = str(runtime_revision)
    builder.add(TraceKind.RUN_START, run_payload)
    pending: list[dict[str, Any]] = []
    done = False
    saw_end = False
    emitted_evidence: set[str] = set()
    emitted_completion: set[str] = set()

    for record in records:
        elapsed = record.get("elapsed_s")
        timestamp = float(elapsed) if isinstance(elapsed, (int, float)) else None
        if record.get("type") == "media_ingress" and isinstance(record.get("data"), Mapping):
            builder.add(TraceKind.MEDIA_INGRESS, _jsonable(record["data"]), timestamp_s=timestamp)
            continue
        if record.get("type") == "decode_gap":
            builder.gap("source_record_unparseable", str((record.get("data") or {}).get("line") or "unknown"))
            continue
        event = _native_sse_payload(str(record.get("sse") or ""))
        if event == "[DONE]":
            done = True
            continue
        if not isinstance(event, Mapping):
            continue
        kind = str(event.get("type") or "")
        round_no = event.get("round") if isinstance(event.get("round"), int) else None
        if kind == "agent_step":
            builder.add(TraceKind.MODEL_TURN, _jsonable(event), timestamp_s=timestamp, round=round_no)
        elif kind == "model_response_ref":
            response_id = str(event.get("response_id") or "")
            builder.add(
                TraceKind.MODEL_CALL,
                {
                    "response_id": response_id,
                    "model": str(event.get("model") or ""),
                },
                timestamp_s=timestamp,
                round=round_no,
                correlation_id=response_id,
            )
        elif kind == "tool_start":
            call_id = str(event.get("tool_call_id") or event.get("call_id") or "")
            if not call_id:
                call_id = f"native-call-{len(pending) + 1}-{len(builder.events)}"
            payload = {
                "tool_name": str(event.get("tool") or ""),
                "arguments": event.get("command"),
                "command": str(event.get("command") or ""),
            }
            builder.add(
                TraceKind.TOOL_CALL,
                payload,
                timestamp_s=timestamp,
                round=round_no,
                correlation_id=call_id,
            )
            pending.append({"call_id": call_id, "tool": payload["tool_name"], "command": payload["command"], "round": round_no})
        elif kind == "tool_output":
            tool = str(event.get("tool") or "")
            command = str(event.get("command") or "")
            explicit_call_id = str(event.get("tool_call_id") or event.get("call_id") or "")
            matches = [
                item for item in pending
                if explicit_call_id and item["call_id"] == explicit_call_id
            ]
            if not matches:
                matches = [item for item in pending if item["tool"] == tool and item["command"] == command]
            if not matches:
                matches = [item for item in pending if item["tool"] == tool]
            if matches:
                selected = matches[0]
                pending.remove(selected)
                call_id = selected["call_id"]
                if round_no is None:
                    round_no = selected["round"]
            elif event.get("execution_attempted") is False:
                # Preview guards return a protocol-level tool result for a
                # model-proposed call that was rejected before dispatch (for
                # example, an exact duplicate).  It is still a real attempted
                # model action and must have a correlated call in the trace;
                # treating it as an orphan falsely invalidates otherwise
                # complete runs.  The explicit marker keeps genuinely
                # unpaired legacy outputs fail-closed below.
                call_id = explicit_call_id or f"native-rejected-{len(builder.events)}"
                builder.add(
                    TraceKind.TOOL_CALL,
                    {
                        "tool_name": tool,
                        "arguments": command,
                        "command": command,
                        "execution_attempted": False,
                        "rejected_before_execution": True,
                    },
                    timestamp_s=timestamp,
                    round=round_no,
                    correlation_id=call_id,
                )
            else:
                call_id = explicit_call_id or f"native-orphan-{len(builder.events)}"
                builder.gap("tool_result_call_unmatched", f"{tool}:{call_id}")
            exit_code = _exit_code(event.get("exit_code"))
            builder.add(
                TraceKind.TOOL_RESULT,
                {
                    "tool_name": tool,
                    "arguments": command,
                    "command": command,
                    "output": event.get("output"),
                    "error": event.get("error"),
                    "exit_code": exit_code,
                    "status": "completed" if exit_code in (None, 0) and not event.get("error") else "failed",
                },
                timestamp_s=timestamp,
                round=round_no,
                correlation_id=call_id,
            )
        elif isinstance(event.get("delta"), str):
            builder.add(
                TraceKind.MODEL_DELTA,
                {"text": event["delta"], "thinking": bool(event.get("thinking"))},
                timestamp_s=timestamp,
                round=round_no,
            )
        elif kind == "usage" and isinstance(event.get("data"), Mapping):
            builder.add(TraceKind.USAGE, _jsonable(event["data"]), timestamp_s=timestamp, round=round_no)
        elif kind == "completion_decision" and isinstance(event.get("data"), Mapping):
            key = json.dumps(event["data"], sort_keys=True, default=str)
            if key not in emitted_completion:
                emitted_completion.add(key)
                builder.add(TraceKind.COMPLETION, _jsonable(event["data"]), timestamp_s=timestamp)
        elif kind == "metrics" and isinstance(event.get("data"), Mapping):
            metrics = dict(event["data"])
            for evidence in metrics.pop("evidence_events", []) or []:
                if not isinstance(evidence, Mapping):
                    continue
                evidence_id = str(evidence.get("event_id") or "")
                if evidence_id and evidence_id in emitted_evidence:
                    continue
                if evidence_id:
                    emitted_evidence.add(evidence_id)
                builder.add(TraceKind.EVIDENCE, _jsonable(evidence), timestamp_s=timestamp)
            completion = metrics.pop("completion_decision", None)
            if isinstance(completion, Mapping):
                key = json.dumps(completion, sort_keys=True, default=str)
                if key not in emitted_completion:
                    emitted_completion.add(key)
                    builder.add(TraceKind.COMPLETION, _jsonable(completion), timestamp_s=timestamp)
            metrics.pop("tool_events", None)
            builder.add(TraceKind.RUNTIME, {"type": "metrics", "data": _jsonable(metrics)}, timestamp_s=timestamp)
        elif kind == "run_cancelled":
            payload = _jsonable(event)
            builder.add(TraceKind.RUNTIME, {"type": "run_cancelled", "data": payload}, timestamp_s=timestamp, round=round_no)
            builder.add(
                TraceKind.RUN_END,
                {
                    "status": "cancelled",
                    "reason": str(event.get("reason") or "cancelled"),
                },
                timestamp_s=timestamp,
                round=round_no,
            )
            saw_end = True
        elif kind:
            builder.add(TraceKind.RUNTIME, {"type": kind, "data": _jsonable(event)}, timestamp_s=timestamp, round=round_no)

    for item in pending:
        builder.gap("tool_result_unavailable", f"{item['tool']}:{item['call_id']}")
    if done:
        builder.add(TraceKind.RUN_END, {"status": "completed"})
        saw_end = True
    if not saw_end:
        builder.gap("trace_end_unavailable")
    return builder.build()


def decode_native_trace_path(
    path: str | Path,
    *,
    run_id: str,
    runtime_revision: str = "",
) -> CanonicalTrace:
    records: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
        for line_no, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                value = {"type": "decode_gap", "data": {"line": line_no, "error": str(exc)}}
            if isinstance(value, dict):
                records.append(value)
    return decode_native_trace(records, run_id=run_id, runtime_revision=runtime_revision)


def decode_typed_trace(records: Iterable[Mapping[str, Any]], *, run_id: str | None = None) -> CanonicalTrace:
    """Decode typed JSONL agent events into the canonical trace schema."""

    rows = [dict(row) for row in records if isinstance(row, Mapping)]
    inferred = next((str(row.get("trace_id")) for row in rows if row.get("trace_id")), "")
    builder = _TraceBuilder(run_id or inferred or "typed-trace", "typed_jsonl")
    saw_start = False
    saw_end = False
    for row in rows:
        event_type = str(row.get("type") or "")
        timestamp = None
        if event_type == "trace_start":
            saw_start = True
            builder.add(
                TraceKind.RUN_START,
                {
                    key: _jsonable(row.get(key))
                    for key in ("task_id", "model", "persona", "runtime_revision")
                    if row.get(key) is not None
                },
                timestamp=str(row.get("timestamp") or ""),
            )
        elif event_type == "message":
            builder.add(
                TraceKind.MESSAGE,
                {"message": _jsonable(row.get("message")), "usage": _jsonable(row.get("usage") or {})},
                timestamp=str(row.get("timestamp") or ""),
            )
        elif event_type == "tool_dispatch":
            call_id = str(row.get("tool_use_id") or f"typed-call-{len(builder.events)}")
            arguments = row.get("request_body") or {}
            command = _command_from_arguments(arguments)
            tool = str(row.get("tool_name") or "")
            builder.add(
                TraceKind.TOOL_CALL,
                {"tool_name": tool, "arguments": _jsonable(arguments), "command": command, "endpoint_url": row.get("endpoint_url")},
                timestamp=str(row.get("timestamp") or ""),
                correlation_id=call_id,
            )
            response = row.get("response_body")
            exit_code = _exit_code(response)
            status_code = row.get("response_status")
            transport_ok = isinstance(status_code, int) and 200 <= status_code < 300
            builder.add(
                TraceKind.TOOL_RESULT,
                {
                    "tool_name": tool,
                    "arguments": _jsonable(arguments),
                    "command": command,
                    "output": _jsonable(response),
                    "exit_code": exit_code,
                    "status": "completed" if transport_ok and exit_code in (None, 0) else "failed",
                    "duration_ms": row.get("latency_ms"),
                    "transport_status": status_code,
                },
                timestamp=str(row.get("timestamp") or ""),
                correlation_id=call_id,
            )
        elif event_type == "media_load":
            builder.add(TraceKind.MEDIA_INGRESS, _jsonable(row), timestamp=str(row.get("timestamp") or ""))
        elif event_type == "compact":
            builder.add(TraceKind.COMPACTION, _jsonable(row), timestamp=str(row.get("timestamp") or ""))
        elif event_type == "grading_result":
            builder.add(TraceKind.GRADING, _jsonable(row), timestamp=str(row.get("timestamp") or ""))
        elif event_type == "trace_end":
            saw_end = True
            builder.add(TraceKind.RUN_END, _jsonable(row), timestamp=str(row.get("timestamp") or ""))
        elif event_type == "audit_snapshot":
            builder.add(TraceKind.RUNTIME, {"type": "audit_snapshot", "data": _jsonable(row)})
        else:
            builder.add(TraceKind.RUNTIME, {"type": event_type or "unknown", "data": _jsonable(row)})
            builder.gap("source_event_unknown", event_type or "missing_type")
    if not saw_start:
        builder.events.insert(0, CanonicalTraceEvent(
            event_id="trace-" + hashlib.sha256(f"{builder.run_id}:synthetic-start".encode()).hexdigest()[:20],
            run_id=builder.run_id,
            sequence=0,
            kind=TraceKind.RUN_START,
            source=builder.source,
            payload={"synthetic": True},
        ))
        builder.events = [
            CanonicalTraceEvent(**{**event.__dict__, "sequence": index})
            for index, event in enumerate(builder.events)
        ]
    if not saw_end:
        builder.gap("trace_end_unavailable")
    return builder.build()


def decode_typed_trace_path(path: str | Path, *, run_id: str | None = None) -> CanonicalTrace:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
        for line_no, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                value = {"type": "decode_error", "line": line_no, "error": str(exc)}
            if isinstance(value, dict):
                rows.append(value)
    return decode_typed_trace(rows, run_id=run_id)


def decode_nemo_trajectory(value: Mapping[str, Any]) -> CanonicalTrace:
    """Decode a NeMo Gym TrajectoryRecord-shaped object without runtime coupling."""

    run_id = str(value.get("rollout_id") or "nemo-rollout")
    task_id = str(value.get("task_id") or "")
    builder = _TraceBuilder(run_id, "nemo_gym")
    builder.add(TraceKind.RUN_START, {"task_id": task_id})
    for invocation in value.get("invocations") or []:
        if not isinstance(invocation, Mapping):
            continue
        invocation_id = str(invocation.get("invocation_id") or "root")
        builder.add(TraceKind.INVOCATION, _jsonable(invocation), invocation_id=invocation_id)
    for turn in value.get("turns") or []:
        if not isinstance(turn, Mapping):
            continue
        invocation_id = str(turn.get("invocation_id") or "root")
        turn_no = turn.get("turn_no") if isinstance(turn.get("turn_no"), int) else None
        builder.add(TraceKind.MODEL_TURN, _jsonable(turn), invocation_id=invocation_id, round=turn_no)
    for call in value.get("model_calls") or []:
        if isinstance(call, Mapping):
            builder.add(TraceKind.MODEL_CALL, _jsonable(call), correlation_id=str(call.get("model_call_id") or ""))
    for call in value.get("tool_calls") or []:
        if not isinstance(call, Mapping):
            continue
        invocation_id = str(call.get("invocation_id") or "root")
        call_id = str(call.get("tool_call_id") or f"nemo-call-{len(builder.events)}")
        tool = str(call.get("tool_name") or "")
        arguments = call.get("arguments")
        if arguments is None:
            builder.gap("tool_arguments_unavailable", call_id, invocation_id=invocation_id)
        command = _command_from_arguments(arguments)
        builder.add(
            TraceKind.TOOL_CALL,
            {"tool_name": tool, "arguments": _jsonable(arguments), "command": command},
            invocation_id=invocation_id,
            correlation_id=call_id,
        )
        builder.add(
            TraceKind.TOOL_RESULT,
            {
                "tool_name": tool,
                "arguments": _jsonable(arguments),
                "command": command,
                "output": _jsonable(call.get("output")),
                "exit_code": _exit_code(call.get("output")),
                "status": str(call.get("status") or "unknown"),
                "duration_ms": call.get("duration_ms"),
            },
            invocation_id=invocation_id,
            correlation_id=call_id,
        )
    for gap in value.get("gaps") or []:
        if isinstance(gap, Mapping):
            builder.add(
                TraceKind.OBSERVATION_GAP,
                _jsonable(gap),
                invocation_id=str(gap.get("invocation_id") or "root"),
            )
    builder.add(TraceKind.RUN_END, {"status": "completed"})
    return builder.build()


def tool_events_from_trace(trace: CanonicalTrace) -> list[dict[str, Any]]:
    """Rehydrate normalized execution events for deterministic evidence replay."""

    events: list[dict[str, Any]] = []
    for event in trace.events:
        if event.kind != TraceKind.TOOL_RESULT:
            continue
        payload = event.payload
        result = {
            "round": event.round,
            "tool": str(payload.get("tool_name") or ""),
            "command": str(payload.get("command") or _command_from_arguments(payload.get("arguments"))),
            "output": payload.get("output"),
            "error": payload.get("error"),
            "exit_code": payload.get("exit_code"),
            "tool_call_id": event.correlation_id,
        }
        events.append(result)
    return events


def evidence_ledger_from_trace(
    trace: CanonicalTrace,
    requirements: CompletionRequirements | None = None,
) -> EvidenceLedger:
    return EvidenceLedger.from_tool_events(tool_events_from_trace(trace), requirements)


def completion_from_trace(
    trace: CanonicalTrace,
    requirements: CompletionRequirements | None = None,
    *,
    exhausted: bool = False,
    awaiting_user: bool = False,
) -> CompletionDecision:
    return evidence_ledger_from_trace(trace, requirements).evaluate(
        exhausted=exhausted,
        awaiting_user=awaiting_user,
    )


def audit_completion_trace(
    trace: CanonicalTrace,
    requirements: CompletionRequirements | None = None,
    *,
    exhausted: bool | None = None,
    awaiting_user: bool = False,
) -> dict[str, Any]:
    """Compare a source-persisted completion decision with deterministic replay."""

    # Native adapters emit ``rounds_exhausted`` before their completion event,
    # but the audit runs after the stream has closed and historically forgot to
    # carry that state into replay. Infer it only when the caller did not pass
    # an explicit override so test tools and alternate adapters can retain
    # control over the replay contract.
    if exhausted is None:
        exhausted = any(
            event.kind == TraceKind.RUNTIME
            and (
                event.payload.get("type") == "rounds_exhausted"
                or (
                    isinstance(event.payload.get("data"), Mapping)
                    and event.payload["data"].get("type") == "rounds_exhausted"
                )
            )
            for event in trace.events
        )

    persisted = next(
        (
            event.payload
            for event in reversed(trace.events)
            if event.kind == TraceKind.COMPLETION
        ),
        None,
    )
    recomputed = completion_from_trace(
        trace,
        requirements,
        exhausted=exhausted,
        awaiting_user=awaiting_user,
    ).to_dict()
    compared_fields = ("status", "can_complete", "missing_artifacts")
    differences: list[str] = []
    if persisted is not None:
        differences = [
            field
            for field in compared_fields
            if persisted.get(field) != recomputed.get(field)
        ]
    return {
        "persisted_available": persisted is not None,
        "agreement": None if persisted is None else not differences,
        "differences": differences,
        "persisted": _jsonable(persisted) if persisted is not None else None,
        "recomputed": recomputed,
    }
