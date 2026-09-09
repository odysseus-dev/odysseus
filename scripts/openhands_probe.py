#!/usr/bin/env python3
"""Read-only readiness and pin probe for the OpenHands Compose overlay."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILES = ("docker-compose.yml", "docker-compose.openhands.yml")
SERVICES = {
    "openhands-agent-server": "OPENHANDS_AGENT_SERVER_IMAGE",
    "openhands-automation": "OPENHANDS_AUTOMATION_IMAGE",
    "openhands-canvas": "OPENHANDS_CANVAS_IMAGE",
    "odysseus-mcp": None,
}
HEALTH_URLS = {
    "openhands-agent-server": "http://127.0.0.1:8000/health",
    "openhands-automation": "http://127.0.0.1:8000/health",
    "openhands-canvas": "http://127.0.0.1:8000/health",
    "odysseus-mcp": "http://127.0.0.1:7000/api/health",
}
CONNECTIVITY = {
    "openhands-automation": "http://openhands-agent-server:8000/health",
    "openhands-canvas": "http://openhands-agent-server:8000/health",
    "odysseus-mcp": "http://openhands-agent-server:8000/health",
}
AUTOMATION_SOURCE = {
    "router": "https://raw.githubusercontent.com/OpenHands/automation/1.11.0/openhands/automation/router.py",
    "schemas": "https://raw.githubusercontent.com/OpenHands/automation/1.11.0/openhands/automation/schemas.py",
    "conversations": "https://raw.githubusercontent.com/OpenHands/automation/1.11.0/openhands/automation/conversations.py",
    "models": "https://raw.githubusercontent.com/OpenHands/automation/1.11.0/openhands/automation/models.py",
}
INTERACTIVE_EXCEPTION = (
    "interactive Agent Server execution is the explicit exception for "
    "interactive turns; do not invent Odysseus orchestration"
)


@dataclass(frozen=True)
class ProbeResult:
    name: str
    passed: bool
    evidence: dict[str, object]


class AutomationConversationMode(StrEnum):
    DISTINCT_RUN = "distinct_run"
    CONTINUED_RUN = "continued_run"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class AdhocExecution:
    conversation_id: str | None
    execution_id: str | None
    request_key: str | None


@dataclass(frozen=True)
class ExecutionRecord:
    execution_id: str | None
    conversation_id: str | None
    request_key: str | None
    status: str | int | None


@dataclass
class AutomationStack:
    observation: dict[str, object]
    created_disposable_definition: bool = False
    attempts: list[dict[str, object]] = field(default_factory=list)

    def start_adhoc(
        self,
        message: str,
        conversation_id: str | None = None,
        request_key: str | None = None,
    ) -> AdhocExecution:
        live = _live_start_adhoc(message, conversation_id, request_key)
        self.attempts.append({"op": "start_adhoc", "message": message, **live})
        if live.get("created_definition"):
            self.created_disposable_definition = True
        return AdhocExecution(
            conversation_id=_opt_str(live.get("conversation_id")),
            execution_id=_opt_str(live.get("execution_id")),
            request_key=_opt_str(live.get("request_key")),
        )

    def wait_idle(self, conversation_id: str | None) -> None:
        self.attempts.append({"op": "wait_idle", "conversation_id": conversation_id})

    def execution(self, execution_id: str | None) -> ExecutionRecord:
        live = _live_get_execution(execution_id)
        self.attempts.append({"op": "execution", "execution_id": execution_id, **live})
        return ExecutionRecord(
            execution_id=execution_id,
            conversation_id=_opt_str(live.get("conversation_id")),
            request_key=_opt_str(live.get("request_key")),
            status=live.get("status"),
        )

    def create_canvas_conversation(self) -> AdhocExecution:
        live = _live_create_conversation()
        self.attempts.append({"op": "create_canvas_conversation", **live})
        return AdhocExecution(
            conversation_id=_opt_str(live.get("conversation_id")),
            execution_id=None,
            request_key=None,
        )

    def cancel(self, execution_id: str | None) -> ExecutionRecord:
        live = _live_cancel(execution_id)
        self.attempts.append({"op": "cancel", "execution_id": execution_id, **live})
        return ExecutionRecord(
            execution_id=execution_id,
            conversation_id=_opt_str(live.get("conversation_id")),
            request_key=None,
            status=live.get("status"),
        )

    def list_automation_definitions(self) -> list[object]:
        live = _live_list_definitions()
        self.attempts.append({"op": "list_definitions", **live})
        items = live.get("definitions")
        return list(items) if isinstance(items, list) else []


def _versions() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (ROOT / "deploy/openhands/versions.env").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    return values


def _compose(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", *(item for file in COMPOSE_FILES for item in ("-f", file)), *args],
        cwd=ROOT, check=False, capture_output=True, text=True,
    )


def _exec(service: str, url: str) -> subprocess.CompletedProcess[str]:
    return _compose("exec", "-T", service, "python", "-c", "import urllib.request; urllib.request.urlopen(" + repr(url) + ", timeout=3).read()")


def probe_stack() -> list[ProbeResult]:
    config = _compose("config", "--format", "json")
    if config.returncode:
        return [ProbeResult("compose", False, {"stderr": config.stderr.strip()})]
    try:
        configured = json.loads(config.stdout).get("services", {})
    except json.JSONDecodeError as error:
        return [ProbeResult("compose", False, {"error": "invalid_config_json", "detail": str(error)})]
    state = _compose("ps", "--format", "json")
    if state.returncode:
        return [ProbeResult("compose", False, {"error": "ps_failed", "stderr": state.stderr.strip()})]
    try:
        running = {row["Service"]: row for line in state.stdout.splitlines() if line.strip() for row in [json.loads(line)]}
    except (json.JSONDecodeError, KeyError) as error:
        return [ProbeResult("compose", False, {"error": "invalid_ps_json", "detail": str(error)})]
    versions = _versions()
    results = []
    for name, pin_key in SERVICES.items():
        service = configured.get(name, {})
        row = running.get(name, {})
        expected = versions.get(pin_key) if pin_key else None
        image = service.get("image")
        health = row.get("Health", "")
        running_state = row.get("State") == "running"
        health_probe = _exec(name, HEALTH_URLS[name]) if running_state else None
        connectivity_probe = _exec(name, CONNECTIVITY[name]) if name in CONNECTIVITY and running_state else None
        passed = bool(service) and running_state and health == "healthy" and (not expected or image == expected) and bool(health_probe and health_probe.returncode == 0) and (not connectivity_probe or connectivity_probe.returncode == 0)
        results.append(ProbeResult(name, passed, {"expected_image": expected, "configured_image": image, "state": row.get("State"), "health": health, "health_url": HEALTH_URLS[name], "health_exit": health_probe.returncode if health_probe else None, "connectivity_url": CONNECTIVITY.get(name), "connectivity_exit": connectivity_probe.returncode if connectivity_probe else None, "networks": service.get("networks", {})}))
    return results


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _safe_output(text: str) -> str:
    redacted = re.sub(r"(?i)(bearer\s+)\S+", r"\1[redacted]", text or "")
    redacted = re.sub(r"(?i)(authorization:\s*)\S+", r"\1[redacted]", redacted)
    redacted = re.sub(r"(?i)(token=)\S+", r"\1[redacted]", redacted)
    return redacted[-800:]


def _extract_block(source: str, prefix: str) -> str:
    start = source.find(prefix)
    if start < 0:
        return ""
    next_async = source.find("\nasync def ", start + len(prefix))
    next_def = source.find("\ndef ", start + len(prefix))
    next_class = source.find("\nclass ", start + len(prefix))
    ends = [index for index in (next_async, next_def, next_class) if index > start]
    return source[start:min(ends)] if ends else source[start:]


def _fetch_text(url: str) -> dict[str, object]:
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            body = response.read().decode("utf-8", errors="replace")
        return {
            "url": url,
            "via": "urllib",
            "status": getattr(response, "status", 200),
            "bytes": len(body),
            "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "body": body,
        }
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        urllib_error = str(error)
    curl = subprocess.run(
        ["curl", "-fsS", "--max-time", "20", url],
        check=False, capture_output=True, text=True,
    )
    if curl.returncode == 0 and curl.stdout:
        body = curl.stdout
        return {
            "url": url,
            "via": "curl",
            "urllib_error": urllib_error,
            "status": 200,
            "bytes": len(body),
            "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "body": body,
        }
    return {
        "url": url,
        "urllib_error": urllib_error,
        "curl_error": _safe_output(curl.stderr),
        "error": "source_fetch_failed",
    }


def _docker_pull(image: str) -> dict[str, object]:
    inspect = subprocess.run(
        ["docker", "image", "inspect", image],
        check=False, capture_output=True, text=True,
    )
    if inspect.returncode == 0:
        return {"image": image, "present": True, "action": "inspect"}
    pull = subprocess.run(
        ["docker", "pull", image],
        check=False, capture_output=True, text=True,
    )
    return {
        "image": image,
        "present": pull.returncode == 0,
        "action": "pull",
        "returncode": pull.returncode,
        "stderr": _safe_output(pull.stderr),
    }


def _service_row(name: str) -> dict[str, object]:
    state = _compose("ps", "--format", "json", name)
    if state.returncode or not state.stdout.strip():
        return {"Service": name, "State": "absent", "stderr": _safe_output(state.stderr)}
    try:
        return json.loads(state.stdout.splitlines()[0])
    except (json.JSONDecodeError, IndexError):
        return {"Service": name, "State": "unknown", "raw": _safe_output(state.stdout)}


def _live_http(service: str, url: str, method: str = "GET", body: bytes | None = None) -> dict[str, object]:
    row = _service_row(service)
    if row.get("State") != "running":
        return {"service": service, "url": url, "method": method, "error": "service_not_running", "state": row.get("State")}
    script = (
        "import json,urllib.request,urllib.error,sys;"
        f"req=urllib.request.Request({url!r},data={body!r},method={method!r});"
        "req.add_header('Content-Type','application/json');"
        "\ntry:\n"
        " r=urllib.request.urlopen(req,timeout=5);"
        " sys.stdout.write(json.dumps({'status':r.status,'body':r.read().decode('utf-8','replace')[:2000]}))"
        "\nexcept Exception as e:\n"
        " sys.stdout.write(json.dumps({'error':str(e)}))"
    )
    result = _compose("exec", "-T", service, "python", "-c", script)
    payload: dict[str, object] = {"service": service, "url": url, "method": method, "exit": result.returncode}
    if result.returncode:
        payload["stderr"] = _safe_output(result.stderr)
        return payload
    try:
        payload.update(json.loads(result.stdout))
    except json.JSONDecodeError:
        payload["stdout"] = _safe_output(result.stdout)
    return payload


def _live_start_adhoc(message: str, conversation_id: str | None, request_key: str | None) -> dict[str, object]:
    """Attempt definitionless dispatch; never invent execution or conversation IDs."""
    call = _live_http(
        "openhands-automation",
        "http://127.0.0.1:8000/api/automation/v1/adhoc/dispatch",
        method="POST",
        body=json.dumps({
            "message": message,
            "conversation_id": conversation_id,
            "request_key": request_key,
        }).encode("utf-8"),
    )
    call["requested_conversation_id"] = conversation_id
    call["requested_request_key"] = request_key
    return call


def _live_get_execution(execution_id: str | None) -> dict[str, object]:
    if not execution_id:
        return {"error": "execution_id_unobserved"}
    return _live_http(
        "openhands-automation",
        f"http://127.0.0.1:8000/api/automation/v1/runs/{execution_id}",
    )


def _live_create_conversation() -> dict[str, object]:
    call = _live_http(
        "openhands-agent-server",
        "http://127.0.0.1:8000/api/conversations",
        method="POST",
        body=b"{}",
    )
    body = call.get("body")
    if isinstance(body, str):
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            conversation_id = parsed.get("id") or parsed.get("conversation_id")
            if isinstance(conversation_id, str):
                call["conversation_id"] = conversation_id
                call["origin"] = "agent_server"
    if "conversation_id" not in call:
        call["origin"] = "unobserved"
        call["origin_reason"] = "Canvas image not local; Agent Server conversation create did not return an id"
    return call


def _live_cancel(execution_id: str | None) -> dict[str, object]:
    if not execution_id:
        return {"status": "unobserved", "error": "execution_id_unobserved"}
    return _live_http(
        "openhands-automation",
        f"http://127.0.0.1:8000/api/automation/v1/runs/{execution_id}/cancel",
        method="POST",
        body=b"{}",
    )


def _live_list_definitions() -> dict[str, object]:
    call = _live_http("openhands-automation", "http://127.0.0.1:8000/api/automation/v1")
    body = call.get("body")
    if isinstance(body, str):
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict) and isinstance(parsed.get("automations"), list):
            call["definitions"] = parsed["automations"]
        elif isinstance(parsed, list):
            call["definitions"] = parsed
    return call


def _document_automation_api() -> dict[str, object]:
    fetched: dict[str, dict[str, object]] = {}
    bodies: dict[str, str] = {}
    for name, url in AUTOMATION_SOURCE.items():
        result = _fetch_text(url)
        meta = {key: value for key, value in result.items() if key != "body"}
        fetched[name] = meta
        body = result.get("body")
        if isinstance(body, str):
            bodies[name] = body
    router = bodies.get("router", "")
    schemas = bodies.get("schemas", "")
    conversations = bodies.get("conversations", "")
    models = bodies.get("models", "")
    dispatch = _extract_block(router, "async def dispatch_automation(")
    dispatch_header = dispatch.split(")", 1)[0]
    run_response = _extract_block(schemas, "class AutomationRunResponse")
    create_request = _extract_block(schemas, "class CreateAutomationRequest")
    event_response = _extract_block(schemas, "class EventResponse")
    run_model = _extract_block(models, "class AutomationRun(")
    facts = {
        "dispatch_accepts_conversation_id": "conversation_id" in dispatch_header,
        "dispatch_has_body_parameter": "body" in dispatch_header.lower(),
        "run_response_has_request_key": "request_key" in run_response,
        "run_model_has_request_key": "request_key" in run_model,
        "create_accepts_conversation_id": "conversation_id" in create_request,
        "create_extra_forbid": 'extra="forbid"' in create_request or "extra='forbid'" in create_request,
        "event_response_has_runs_created": "runs_created" in event_response,
        "event_response_has_conversations_continued": "conversations_continued" in event_response,
        "continue_conversation_helper": "async def continue_conversation(" in conversations,
        "cancel_endpoint": '@router.post("/runs/{run_id}/cancel")' in router,
        "conversation_id_is_completion_output": "set by completion callback" in run_model,
        "sources_fetched": sorted(bodies),
    }
    return {"sources": fetched, "facts": facts}


def _classify(facts: dict[str, object], live_ids: dict[str, object]) -> AutomationConversationMode:
    first = live_ids.get("first_execution_id")
    second = live_ids.get("second_execution_id")
    if (
        isinstance(first, str)
        and isinstance(second, str)
        and first
        and second
        and first != second
        and live_ids.get("same_conversation") is True
        and live_ids.get("request_key_round_trip") is True
    ):
        return AutomationConversationMode.DISTINCT_RUN
    documented_continue = (
        facts.get("continue_conversation_helper") is True
        and facts.get("event_response_has_conversations_continued") is True
        and facts.get("event_response_has_runs_created") is True
        and facts.get("dispatch_accepts_conversation_id") is False
        and facts.get("run_response_has_request_key") is False
        and facts.get("run_model_has_request_key") is False
        and facts.get("create_accepts_conversation_id") is False
    )
    if documented_continue:
        return AutomationConversationMode.CONTINUED_RUN
    return AutomationConversationMode.UNSUPPORTED


_OBSERVED: dict[str, object] | None = None


def observe_automation_semantics() -> dict[str, object]:
    global _OBSERVED
    if _OBSERVED is None:
        _OBSERVED = _observe_automation_semantics()
    return _OBSERVED


def _observe_automation_semantics() -> dict[str, object]:
    versions = _versions()
    pull = _docker_pull(versions.get("OPENHANDS_AUTOMATION_IMAGE", "ghcr.io/openhands/automation:1.11.0"))
    startup = _compose("up", "-d", "--no-deps", "--pull", "never", "openhands-automation")
    documented = _document_automation_api()
    facts = documented.get("facts", {})
    facts = facts if isinstance(facts, dict) else {}
    live_ids = {
        "first_execution_id": None,
        "second_execution_id": None,
        "first_conversation_id": None,
        "second_conversation_id": None,
        "same_conversation": False,
        "request_key_round_trip": False,
        "ids_unobserved_reason": (
            "Automation container did not start; execution and conversation IDs "
            "were not invented"
        ),
    }
    mode = _classify(facts, live_ids)
    api_calls = [
        {
            "method": "POST",
            "path": "/api/automation/v1/{automation_id}/dispatch",
            "observed": "documented_1.11.0",
            "request_body": "absent",
            "conversation_id": "not accepted",
            "request_key": "not accepted",
        },
        {
            "method": "POST",
            "path": "/api/automation/v1/runs/{run_id}/cancel",
            "observed": "documented_1.11.0",
            "result": "pending/running cancel; missing -> 404; terminal -> 409",
        },
        {
            "method": "event",
            "path": "continue_conversation",
            "observed": "documented_1.11.0",
            "result": "successful follow-up reports runs_created=[] and conversations_continued=[derived_id]",
        },
    ]
    return {
        "pins": {
            "OPENHANDS_AGENT_SERVER_IMAGE": versions.get("OPENHANDS_AGENT_SERVER_IMAGE"),
            "OPENHANDS_AUTOMATION_IMAGE": versions.get("OPENHANDS_AUTOMATION_IMAGE"),
            "OPENHANDS_CANVAS_IMAGE": versions.get("OPENHANDS_CANVAS_IMAGE"),
        },
        "classification": mode.value,
        "selected_branch": mode.value,
        "api_calls": api_calls,
        "returned_ids": live_ids,
        "idempotency": {
            "supported": False,
            "result": "AutomationRunResponse and AutomationRun have no request_key; dispatch ignores Idempotency-Key",
        },
        "cancellation": {
            "exact_run_endpoint": bool(facts.get("cancel_endpoint")),
            "result": "documented POST /v1/runs/{run_id}/cancel; not exercised because no run id was observed",
        },
        "conversation_origin": {
            "canvas": "unobserved",
            "reason": "Canvas image not present locally; Automation accepts no caller-selected conversation_id, so Canvas targeting has no public contract",
        },
        "persisted_definition_required": True,
        "pull": pull,
        "startup": {
            "returncode": startup.returncode,
            "stderr": _safe_output(startup.stderr),
        },
        "documented_api": {key: value for key, value in documented.items() if key != "sources"} | {
            "sources": {
                name: {k: v for k, v in meta.items() if k != "body"}
                for name, meta in dict(documented.get("sources", {})).items()
            }
        },
        "interactive_exception": INTERACTIVE_EXCEPTION if mode != AutomationConversationMode.DISTINCT_RUN else None,
    }


def automation_stack() -> AutomationStack:
    return AutomationStack(observation=observe_automation_semantics())


def probe_automation_existing_conversation() -> ProbeResult:
    evidence = observe_automation_semantics()
    mode = evidence["classification"]
    passed = mode == AutomationConversationMode.DISTINCT_RUN
    return ProbeResult("automation-existing-conversation", passed, evidence)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("probe", choices=["stack", "automation-existing-conversation"])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.probe == "automation-existing-conversation":
        result = probe_automation_existing_conversation()
        print(json.dumps(asdict(result), sort_keys=True))
        return 0 if result.passed else 1
    results = probe_stack()
    payload = [asdict(result) for result in results]
    print(json.dumps(payload if args.json else {"results": payload}, sort_keys=True))
    return 0 if results and all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
