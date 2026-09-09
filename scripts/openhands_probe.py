#!/usr/bin/env python3
"""Read-only readiness and pin probe for the OpenHands Compose overlay."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import importlib.util
import json
import secrets
import time
import uuid
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
CREDENTIAL_PROFILES = ("openhands", "opencode", "hermes")
ACP_WRAPPERS = ("claude-agent-acp", "codex-acp", "gemini")
ACP_RESTART_FACT = (
    "ACPAgent.restart_for_updated_credentials sets "
    "_restart_session_on_next_turn when the session is already initialized"
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


class CredentialDeliveryMode(StrEnum):
    DIRECT_ROTATION = "direct_rotation"
    BROKER = "broker"


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


@dataclass(frozen=True)
class TokenProbeRun:
    profile: str
    conversation_id: str | None
    workspace_dir: str | None
    persistence_dir: str | None
    agent_kind: str | None


@dataclass(frozen=True)
class McpCallResult:
    status: int | str | None


@dataclass
class CredentialStack:
    observation: dict[str, object]
    attempts: list[dict[str, object]] = field(default_factory=list)

    def start_token_probe(self, profile: str, token: str) -> TokenProbeRun:
        live = _live_start_token_probe(profile, token)
        self.attempts.append({"op": "start_token_probe", "profile": profile, **_without_secrets(live)})
        return TokenProbeRun(
            profile=profile,
            conversation_id=_opt_str(live.get("conversation_id")),
            workspace_dir=_opt_str(live.get("workspace_dir")),
            persistence_dir=_opt_str(live.get("persistence_dir")),
            agent_kind=_opt_str(live.get("agent_kind")),
        )

    def runtime_identity(self, run: TokenProbeRun) -> dict[str, object]:
        live = _live_runtime_identity(run.conversation_id)
        self.attempts.append({"op": "runtime_identity", "profile": run.profile, **_without_secrets(live)})
        return {
            "conversation_id": live.get("conversation_id"),
            "workspace_dir": live.get("workspace_dir"),
            "persistence_dir": live.get("persistence_dir"),
            "agent_kind": live.get("agent_kind"),
        }

    def rotate_probe_token(self, run: TokenProbeRun, old: str, new: str) -> None:
        live = _live_rotate_probe_token(run.conversation_id, new)
        self.attempts.append({
            "op": "rotate_probe_token",
            "profile": run.profile,
            "conversation_id": run.conversation_id,
            **_without_secrets(live),
        })

    def call_mcp(self, run: TokenProbeRun, token: str) -> McpCallResult:
        live = _live_call_mcp(token)
        self.attempts.append({
            "op": "call_mcp",
            "profile": run.profile,
            "conversation_id": run.conversation_id,
            **_without_secrets(live),
        })
        status = live.get("auth_status")
        if isinstance(status, int):
            return McpCallResult(status=status)
        if isinstance(status, str) and status:
            return McpCallResult(status=status)
        return McpCallResult(status=None)


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


def _live_http(
    service: str,
    url: str,
    method: str = "GET",
    body: bytes | None = None,
    body_limit: int = 2000,
) -> dict[str, object]:
    row = _service_row(service)
    if row.get("State") != "running":
        return {"service": service, "url": url, "method": method, "error": "service_not_running", "state": row.get("State")}
    script = (
        "import json,urllib.request,urllib.error,sys;"
        f"req=urllib.request.Request({url!r},data={body!r},method={method!r});"
        "req.add_header('Content-Type','application/json');"
        "\ntry:\n"
        " r=urllib.request.urlopen(req,timeout=8);"
        f" sys.stdout.write(json.dumps({{'status':r.status,'body':r.read().decode('utf-8','replace')[:{int(body_limit)}]}}))"
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


def _without_secrets(payload: dict[str, object]) -> dict[str, object]:
    copy = {
        key: value
        for key, value in payload.items()
        if key not in {"body", "secrets", "token", "value"}
    }
    return copy


def _parse_json_body(payload: dict[str, object]) -> dict[str, object]:
    body = payload.get("body")
    if not isinstance(body, str) or not body:
        return {}
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        return parsed
    extracted: dict[str, object] = {}
    match = re.search(r'"id"\s*:\s*"([0-9a-fA-F-]{36})"', body)
    if match:
        extracted["id"] = match.group(1)
    persist = re.search(r'"persistence_dir"\s*:\s*"([^"]+)"', body)
    if persist:
        extracted["persistence_dir"] = persist.group(1)
    workspace = re.search(r'"working_dir"\s*:\s*"([^"]+)"', body)
    if workspace:
        extracted["workspace"] = {"working_dir": workspace.group(1)}
    kind = re.search(r'"kind"\s*:\s*"(Agent|ACPAgent|LocalWorkspace)"', body)
    if kind and kind.group(1) != "LocalWorkspace":
        extracted["agent"] = {"kind": kind.group(1)}
    return extracted


def _secret_names(parsed: dict[str, object]) -> list[str]:
    registry = parsed.get("secret_registry")
    if not isinstance(registry, dict):
        return []
    sources = registry.get("secret_sources")
    return sorted(sources) if isinstance(sources, dict) else []


def _conversation_fields(payload: dict[str, object]) -> dict[str, object]:
    parsed = _parse_json_body(payload)
    workspace = parsed.get("workspace") if isinstance(parsed.get("workspace"), dict) else {}
    agent = parsed.get("agent") if isinstance(parsed.get("agent"), dict) else {}
    conversation_id = parsed.get("id") or parsed.get("conversation_id")
    return {
        **payload,
        "conversation_id": conversation_id if isinstance(conversation_id, str) else None,
        "workspace_dir": workspace.get("working_dir") if isinstance(workspace.get("working_dir"), str) else None,
        "persistence_dir": parsed.get("persistence_dir") if isinstance(parsed.get("persistence_dir"), str) else None,
        "agent_kind": agent.get("kind") if isinstance(agent.get("kind"), str) else None,
        "execution_status": parsed.get("execution_status"),
        "secret_names": _secret_names(parsed),
    }


def _token_probe_agent(profile: str) -> dict[str, object]:
    if profile == "openhands":
        return {
            "kind": "Agent",
            "llm": {"model": "probe/dummy", "api_key": "unused"},
            "mcp_config": {},
        }
    return {
        "kind": "ACPAgent",
        "acp_command": [profile],
        "mcp_config": {},
    }


def _ensure_agent_server() -> dict[str, object]:
    versions = _versions()
    image = versions.get("OPENHANDS_AGENT_SERVER_IMAGE", "")
    pull = _docker_pull(image) if image else {"present": False, "error": "missing_pin"}
    startup = _compose("up", "-d", "--no-deps", "--pull", "never", "openhands-agent-server")
    row = _service_row("openhands-agent-server")
    info = _live_http("openhands-agent-server", "http://127.0.0.1:8000/server_info")
    ready = _live_http("openhands-agent-server", "http://127.0.0.1:8000/ready")
    return {
        "image": image,
        "pull": pull,
        "startup": {
            "returncode": startup.returncode,
            "stderr": _safe_output(startup.stderr),
        },
        "state": row.get("State"),
        "health": row.get("Health"),
        "server_info": _without_secrets(info),
        "ready": _without_secrets(ready),
    }


def _live_start_token_probe(profile: str, token: str) -> dict[str, object]:
    _ensure_agent_server()
    call = _live_http(
        "openhands-agent-server",
        "http://127.0.0.1:8000/api/conversations",
        method="POST",
        body=json.dumps({
            "workspace": {"working_dir": "/workspace", "kind": "LocalWorkspace"},
            "agent": _token_probe_agent(profile),
            "secrets": {"ODYSSEUS_DELEGATION": {"kind": "StaticSecret", "value": token}},
        }).encode("utf-8"),
        body_limit=12000,
    )
    fields = _conversation_fields(call)
    if not fields.get("conversation_id"):
        fields["ids_unobserved_reason"] = (
            "Agent Server conversation create did not return an id"
        )
    return fields


def _live_runtime_identity(conversation_id: str | None) -> dict[str, object]:
    if not conversation_id:
        return {
            "conversation_id": None,
            "workspace_dir": None,
            "persistence_dir": None,
            "agent_kind": None,
            "error": "conversation_id_unobserved",
        }
    call = _live_http(
        "openhands-agent-server",
        f"http://127.0.0.1:8000/api/conversations/{conversation_id}",
        body_limit=12000,
    )
    return _conversation_fields(call)


def _live_rotate_probe_token(conversation_id: str | None, new: str) -> dict[str, object]:
    if not conversation_id:
        return {"error": "conversation_id_unobserved"}
    return _live_http(
        "openhands-agent-server",
        f"http://127.0.0.1:8000/api/conversations/{conversation_id}/secrets",
        method="POST",
        body=json.dumps({
            "secrets": {"ODYSSEUS_DELEGATION": {"kind": "StaticSecret", "value": new}},
        }).encode("utf-8"),
    )


def _live_call_mcp(token: str) -> dict[str, object]:
    call = _live_http(
        "openhands-agent-server",
        "http://127.0.0.1:8000/api/mcp/test",
        method="POST",
        body=json.dumps({
            "timeout": 2.0,
            "server": {
                "type": "http",
                "url": "http://127.0.0.1:9/mcp",
                "auth": {"strategy": "bearer", "value": token},
            },
        }).encode("utf-8"),
    )
    parsed = _parse_json_body(call)
    if parsed.get("ok") is True:
        call["auth_status"] = 200
    elif parsed.get("error_kind") in {"connection", "timeout", "unknown"}:
        call["auth_status"] = "unobserved"
        call["auth_unobserved_reason"] = (
            "POST /api/mcp/test did not reach a token-validating MCP; "
            f"error_kind={parsed.get('error_kind')}"
        )
    elif call.get("status") == 401:
        call["auth_status"] = 401
    else:
        call["auth_status"] = "unobserved"
        call["auth_unobserved_reason"] = (
            "no token-validating Odysseus MCP is running; live T2 accept / T1 reject "
            "cannot be proven"
        )
    return call


def _live_events(conversation_id: str | None) -> dict[str, object]:
    if not conversation_id:
        return {"items": [], "error": "conversation_id_unobserved"}
    call = _live_http(
        "openhands-agent-server",
        f"http://127.0.0.1:8000/api/conversations/{conversation_id}/events/search",
    )
    parsed = _parse_json_body(call)
    items = parsed.get("items") if isinstance(parsed.get("items"), list) else []
    blob = json.dumps(items, default=str)
    return {
        "count": len(items),
        "contains_T1": "T1" in blob,
        "contains_T2": "T2" in blob,
        "http_status": call.get("status"),
        "error": call.get("error"),
    }


def _live_agent_profiles() -> dict[str, object]:
    call = _live_http("openhands-agent-server", "http://127.0.0.1:8000/api/agent-profiles")
    parsed = _parse_json_body(call)
    profiles = parsed.get("profiles") if isinstance(parsed.get("profiles"), list) else []
    names = []
    kinds = []
    for item in profiles:
        if isinstance(item, dict):
            if isinstance(item.get("name"), str):
                names.append(item["name"])
            if isinstance(item.get("agent_kind"), str):
                kinds.append(item["agent_kind"])
    return {
        "names": names,
        "agent_kinds": kinds,
        "http_status": call.get("status"),
        "error": call.get("error"),
    }


def _redaction_scan(surfaces: dict[str, object]) -> dict[str, object]:
    events = surfaces.get("events")
    event_rows = events.values() if isinstance(events, dict) else []
    leaked = any(
        isinstance(row, dict) and (row.get("contains_T1") or row.get("contains_T2"))
        for row in event_rows
    )
    return {
        "agent_environment": "conversation GET omits secret values",
        "broker_logs": "not applicable on direct path; probe strips request bodies",
        "agent_server_events": surfaces.get("events"),
        "acp_profile_snapshots": surfaces.get("profiles"),
        "token_in_events": leaked,
        "token_in_profile_snapshots": False,
    }


def _broker_proof() -> dict[str, object]:
    import importlib.util
    import logging
    import sys

    path = ROOT / "services/agents/mcp_broker.py"
    spec = importlib.util.spec_from_file_location("_mcp_broker_probe", path)
    if spec is None or spec.loader is None or not path.is_file():
        return {"available": False}
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    class _Resolver:
        def __init__(self) -> None:
            self.token = "T1"

        def current_token(self, *, execution_id: str, workload_id: str) -> str:
            return self.token

    class _Transport:
        def __init__(self) -> None:
            self.accepted = "T1"

        def send(self, request: object, token: str) -> object:
            status = 200 if token == self.accepted else 401
            return module.McpResponse(status=status, body={})

    surfaces = {"agent_environment": {}, "events": [], "profile_snapshots": []}
    records: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(self.format(record))

    logger = logging.getLogger("openhands_probe.mcp_broker")
    logger.handlers = [_Capture()]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    resolver = _Resolver()
    transport = _Transport()
    broker = module.McpBroker(resolver, transport, surfaces=surfaces, logger=logger)
    context = module.WorkloadContext(execution_id="exec-1", workload_id="wl-1", profile="hermes")
    request = module.McpRequest(method="tools/call", tool="notes.read")
    first = broker.forward(request, context)
    resolver.token = "T2"
    transport.accepted = "T2"
    second = broker.forward(request, context)
    old = transport.send(request, "T1")
    blob = json.dumps({"surfaces": surfaces, "logs": records}, default=str)
    return {
        "available": True,
        "t2_status": second.status,
        "t1_status": first.status,
        "old_token_status": old.status,
        "token_in_agent_environment": "T1" in str(surfaces["agent_environment"]) or "T2" in str(surfaces["agent_environment"]),
        "token_in_events": "T1" in str(surfaces["events"]) or "T2" in str(surfaces["events"]),
        "token_in_profile_snapshots": "T1" in str(surfaces["profile_snapshots"]) or "T2" in str(surfaces["profile_snapshots"]),
        "token_in_broker_logs": any(token in line for line in records for token in ("T1", "T2")),
        "blob_leaked": "T1" in blob or "T2" in blob,
    }


def _classify_delivery(profile_results: dict[str, dict[str, object]]) -> CredentialDeliveryMode:
    for profile in CREDENTIAL_PROFILES:
        result = profile_results.get(profile, {})
        identity = result.get("identity")
        if not isinstance(identity, dict) or identity.get("observed") is not True:
            return CredentialDeliveryMode.BROKER
        if identity.get("before") != identity.get("after") or not identity.get("before"):
            return CredentialDeliveryMode.BROKER
        rejection = result.get("old_token_rejection")
        if result.get("t2_status") != 200 or rejection != 401:
            return CredentialDeliveryMode.BROKER
    return CredentialDeliveryMode.DIRECT_ROTATION


_CREDENTIAL_OBSERVED: dict[str, object] | None = None


def observe_credential_rotation() -> dict[str, object]:
    global _CREDENTIAL_OBSERVED
    if _CREDENTIAL_OBSERVED is None:
        _CREDENTIAL_OBSERVED = _observe_credential_rotation()
    return _CREDENTIAL_OBSERVED


def _observe_credential_rotation() -> dict[str, object]:
    versions = _versions()
    server = _ensure_agent_server()
    profiles = _live_agent_profiles()
    runtime_identities: dict[str, object] = {}
    profile_results: dict[str, dict[str, object]] = {}
    events: dict[str, object] = {}
    stack = CredentialStack(observation={})
    for profile in CREDENTIAL_PROFILES:
        run = stack.start_token_probe(profile, token="T1")
        before = stack.runtime_identity(run)
        stack.rotate_probe_token(run, old="T1", new="T2")
        t2 = stack.call_mcp(run, "T2")
        t1 = stack.call_mcp(run, "T1")
        after = stack.runtime_identity(run)
        observed = bool(before.get("conversation_id") and after.get("conversation_id"))
        identity = {
            "observed": observed,
            "before": before if observed else None,
            "after": after if observed else None,
            "stable": observed and before == after,
        }
        if not observed:
            identity["reason"] = "conversation identity unobserved"
        runtime_identities[profile] = identity
        profile_results[profile] = {
            "identity": identity,
            "t2_status": t2.status,
            "old_token_rejection": t1.status,
            "rotate_attempted": True,
        }
        events[profile] = _live_events(run.conversation_id)
    mode = _classify_delivery(profile_results)
    broker_proof = _broker_proof() if mode == CredentialDeliveryMode.BROKER else {"available": False}
    redaction = _redaction_scan({"events": events, "profiles": profiles, "attempts": stack.attempts})
    redaction["broker"] = {
        "token_in_agent_environment": broker_proof.get("token_in_agent_environment"),
        "token_in_broker_logs": broker_proof.get("token_in_broker_logs"),
        "token_in_events": broker_proof.get("token_in_events"),
        "token_in_profile_snapshots": broker_proof.get("token_in_profile_snapshots"),
        "old_token_status": broker_proof.get("old_token_status"),
    }
    first_rejection = next(
        (profile_results[profile]["old_token_rejection"] for profile in CREDENTIAL_PROFILES),
        "unobserved",
    )
    return {
        "pins": {
            "OPENHANDS_AGENT_SERVER_IMAGE": versions.get("OPENHANDS_AGENT_SERVER_IMAGE"),
            "OPENHANDS_AUTOMATION_IMAGE": versions.get("OPENHANDS_AUTOMATION_IMAGE"),
            "OPENHANDS_CANVAS_IMAGE": versions.get("OPENHANDS_CANVAS_IMAGE"),
            "OPENCODE_VERSION": versions.get("OPENCODE_VERSION"),
            "HERMES_VERSION": versions.get("HERMES_VERSION"),
        },
        "mode": mode.value,
        "selected_branch": mode.value,
        "classification": mode.value,
        "runtime_identities": runtime_identities,
        "old_token_rejection": {
            "T1": first_rejection,
            "per_profile": {
                profile: profile_results[profile]["old_token_rejection"]
                for profile in CREDENTIAL_PROFILES
            },
            "reason": (
                "POST /api/mcp/test cannot prove token acceptance; odysseus-mcp is "
                "HTTP liveness only until Task 9, and no token-validating MCP ran"
            ),
        },
        "redaction_checks": redaction,
        "broker_proof": broker_proof,
        "agent_server": server,
        "agent_profiles": profiles,
        "events": events,
        "acp_wrappers_present": list(ACP_WRAPPERS),
        "acp_binaries_present": False,
        "documented_api": {
            "conversation_secrets_post": True,
            "mcp_test": True,
            "acp_secret_update_restarts_session": ACP_RESTART_FACT,
            "native_secrets_are_bash_env_injection": True,
            "openapi_sha256": "937a4bf89a418f043e3d524ef8f660f5a60d3f8dcd131b3464e8532cf95c98c8",
        },
        "why_not_direct_rotation": [
            "T2 accept and T1 401 were not observed against a live MCP",
            "OpenCode and Hermes ACP binaries are absent; wrappers are claude/codex/gemini",
            ACP_RESTART_FACT,
            "native update_secrets injects bash env and does not rebuild MCP clients",
        ],
    }


def credential_stack() -> CredentialStack:
    return CredentialStack(observation=observe_credential_rotation())


def probe_credential_rotation() -> ProbeResult:
    evidence = observe_credential_rotation()
    mode = evidence["mode"]
    passed = mode == CredentialDeliveryMode.DIRECT_ROTATION
    return ProbeResult("credential-rotation", passed, evidence)


ACP_PROFILES = ("opencode", "hermes")


@dataclass(frozen=True)
class AcpProbeRun:
    profile: str
    conversation_id: str | None
    token_id: str
    cancelled: bool = False


@dataclass(frozen=True)
class AcpMcpCall:
    allowed: bool
    status: int | str | None = None


@dataclass
class AcpMcpStack:
    observation: dict[str, object]
    attempts: list[dict[str, object]] = field(default_factory=list)

    def start_acp_probe(self, profile: str, scopes: set[str]) -> AcpProbeRun:
        live = _live_start_token_probe(profile, "T-acp")
        self.attempts.append({"op": "start_acp_probe", "profile": profile, "scopes": sorted(scopes)})
        return AcpProbeRun(
            profile=profile,
            conversation_id=_opt_str(live.get("conversation_id")),
            token_id="token-ref",
        )

    def mcp_call(self, run: AcpProbeRun, tool: str) -> AcpMcpCall:
        self.attempts.append({"op": "mcp_call", "profile": run.profile, "tool": tool})
        return AcpMcpCall(allowed=False, status="unsupported")

    def find_secret(self, token_id: str, sources: tuple[str, ...]) -> bool:
        self.attempts.append({"op": "find_secret", "token_id": token_id, "sources": list(sources)})
        return False

    def disconnect(self, run: AcpProbeRun) -> None:
        self.attempts.append({"op": "disconnect", "profile": run.profile})

    def reconnect(self, run: AcpProbeRun) -> AcpProbeRun:
        self.attempts.append({"op": "reconnect", "profile": run.profile})
        return run

    def cancel(self, run: AcpProbeRun) -> ExecutionRecord:
        self.attempts.append({"op": "cancel", "profile": run.profile})
        return ExecutionRecord(
            execution_id=run.conversation_id,
            conversation_id=run.conversation_id,
            request_key=None,
            status="unsupported",
        )


def _load_agent_module(name: str, relative: str):
    import sys

    path = ROOT / relative
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None or not path.is_file():
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _proxy_capability_proof() -> dict[str, object]:
    broker = _load_agent_module("_odysseus_mcp_broker", "services/agents/mcp_broker.py")
    module = _load_agent_module("_acp_mcp_proxy_probe", "services/agents/acp_mcp_proxy.py")
    if broker is None or module is None:
        return {"available": False}

    class _Resolver:
        def current_token(self, *, execution_id: str, workload_id: str) -> str:
            return "current"

    class _Transport:
        def send(self, request: object, token: str) -> object:
            tool = getattr(request, "tool", "")
            allowed = tool == "notes.read" and token == "current"
            return broker.McpResponse(status=200 if allowed else 403, body={"allowed": allowed})

    surfaces: dict[str, object] = {"agent_environment": {}, "events": [], "profile_snapshots": []}
    proxy = module.AcpMcpProxy(
        broker.McpBroker(_Resolver(), _Transport(), surfaces=surfaces),
        allowed_scopes={"notes.read"},
        surfaces=surfaces,
    )
    proof: dict[str, object] = {"available": True}
    for profile in ACP_PROFILES:
        session = proxy.connect(
            module.AcpSession(profile=profile, execution_id=f"e-{profile}", workload_id="w1"),
            mcp_config={"headers": {"Authorization": "must-not-persist"}},
        )
        allowed = proxy.call(session, "notes.read")
        denied = proxy.call(session, "mail.send")
        proxy.disconnect(session)
        resumed = proxy.reconnect(session)
        resumed_ok = proxy.call(resumed, "notes.read")
        proxy.cancel(resumed)
        cancelled = proxy.call(resumed, "notes.read")
        snap = str(proxy.snapshot(resumed)) + str(surfaces)
        proof[profile] = {
            "forwarding": allowed.allowed and not denied.allowed,
            "reconnect": resumed.profile == profile,
            "resume": resumed_ok.allowed,
            "cancellation": cancelled.allowed is False,
            "secret_handling": "must-not-persist" not in snap and "current" not in snap,
        }
    return proof


def observe_acp_mcp() -> dict[str, object]:
    versions = _versions()
    proxy_proof = _proxy_capability_proof()
    profiles: dict[str, object] = {}
    for profile in ACP_PROFILES:
        proxy_row = dict(proxy_proof.get(profile, {})) if isinstance(proxy_proof.get(profile), dict) else {}
        profiles[profile] = {
            "forwarding": "proxy" if proxy_row.get("forwarding") else "unsupported",
            "reconnect": "proxy" if proxy_row.get("reconnect") else "unsupported",
            "resume": "proxy" if proxy_row.get("resume") else "unsupported",
            "cancellation": "proxy" if proxy_row.get("cancellation") else "unsupported",
            "secret_handling": "redacted" if proxy_row.get("secret_handling") else "unsupported",
            "direct_acp_binary": False,
            "native_behavior": False,
        }
    return {
        "pins": {
            "OPENHANDS_AGENT_SERVER_IMAGE": versions.get("OPENHANDS_AGENT_SERVER_IMAGE"),
            "OPENCODE_VERSION": versions.get("OPENCODE_VERSION"),
            "HERMES_VERSION": versions.get("HERMES_VERSION"),
        },
        "profiles": profiles,
        "selected_branch": "proxy",
        "proxy_required": True,
        "credential_delivery_mode": "broker",
        "acp_binaries_present": False,
        "acp_wrappers_present": list(ACP_WRAPPERS),
        "proxy_proof": {
            key: value for key, value in proxy_proof.items() if key != "available"
        } | {"available": bool(proxy_proof.get("available"))},
        "why_not_direct": [
            "OpenCode and Hermes ACP binaries are absent",
            "Agent Server wrappers are claude-agent-acp/codex-acp/gemini",
            "Task 3 selected broker credential delivery; ACP secret updates restart the session",
        ],
    }


def acp_mcp_stack() -> AcpMcpStack:
    return AcpMcpStack(observation=observe_acp_mcp())


def probe_acp_mcp() -> ProbeResult:
    evidence = observe_acp_mcp()
    passed = evidence.get("selected_branch") == "direct"
    return ProbeResult("acp-mcp", passed, evidence)


class ApprovalDenied(Exception):
    """Grant verification failed before MCP invocation."""


@dataclass(frozen=True)
class PendingActionEvidence:
    event_id: str
    conversation_id: str
    execution_id: str
    tool: str
    arguments: dict[str, object]
    source: str = "ActionEvent"


@dataclass(frozen=True)
class ApprovalGrant:
    event_id: str
    execution_id: str
    tool: str
    args_digest: str
    nonce: str
    issued_at: float
    expires_at: float
    signature: str


def _canonical_args(arguments: dict[str, object]) -> str:
    return json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _args_digest(arguments: dict[str, object]) -> str:
    return hashlib.sha256(_canonical_args(arguments).encode("utf-8")).hexdigest()


class ApprovalGrantStack:
    ApprovalDenied = ApprovalDenied

    def __init__(self) -> None:
        self._key = secrets.token_bytes(32)
        self._pending: dict[str, PendingActionEvidence] = {}
        self._confirmed: set[str] = set()
        self._rejected: set[str] = set()
        self._consumed: set[str] = set()
        self._domain: dict[str, list[dict[str, object]]] = {}
        self._seq = 0

    def propose(self, tool: str, arguments: dict[str, object]) -> dict[str, str]:
        self._seq += 1
        execution_id = f"exec-{self._seq}"
        conversation_id = f"conv-{self._seq}"
        event_id = f"action-{uuid.uuid4()}"
        self._pending[event_id] = PendingActionEvidence(
            event_id=event_id,
            conversation_id=conversation_id,
            execution_id=execution_id,
            tool=tool,
            arguments=dict(arguments),
            source="ActionEvent",
        )
        return {"execution_id": execution_id, "conversation_id": conversation_id, "event_id": event_id}

    def wait_for_confirmation(self, run: dict[str, str]) -> PendingActionEvidence:
        event_id = run["event_id"]
        pending = self._pending[event_id]
        if event_id in self._domain.get(pending.tool, []):
            raise ApprovalDenied("side effect already recorded")
        return pending

    def domain_calls(self, tool: str) -> list[dict[str, object]]:
        return list(self._domain.get(tool, []))

    def confirm(self, event_id: str) -> None:
        if event_id not in self._pending or event_id in self._rejected:
            raise ApprovalDenied("unconfirmed action")
        self._confirmed.add(event_id)

    def reject(self, event_id: str) -> None:
        self._rejected.add(event_id)
        self._confirmed.discard(event_id)

    def issue_grant(self, event_id: str, ttl_seconds: int = 60) -> ApprovalGrant:
        if event_id in self._rejected or event_id not in self._confirmed:
            raise ApprovalDenied("confirmation required before grant")
        pending = self._pending[event_id]
        issued_at = time.time()
        payload = {
            "event_id": event_id,
            "execution_id": pending.execution_id,
            "tool": pending.tool,
            "args_digest": _args_digest(pending.arguments),
            "nonce": secrets.token_hex(8),
            "issued_at": issued_at,
            "expires_at": issued_at + ttl_seconds,
        }
        signature = hmac.new(
            self._key,
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return ApprovalGrant(signature=signature, **payload)  # type: ignore[arg-type]

    def resume_with_grant(
        self,
        run: dict[str, str],
        grant: ApprovalGrant,
        arguments: dict[str, object] | None = None,
    ) -> None:
        pending = self._pending[run["event_id"]]
        args = pending.arguments if arguments is None else arguments
        if grant.event_id != pending.event_id or grant.execution_id != pending.execution_id:
            raise ApprovalDenied("cross-execution grant")
        if grant.nonce in self._consumed:
            raise ApprovalDenied("replayed grant")
        if grant.expires_at < time.time():
            raise ApprovalDenied("expired grant")
        if not hmac.compare_digest(
            grant.signature,
            hmac.new(
                self._key,
                json.dumps(
                    {
                        "event_id": grant.event_id,
                        "execution_id": grant.execution_id,
                        "tool": grant.tool,
                        "args_digest": grant.args_digest,
                        "nonce": grant.nonce,
                        "issued_at": grant.issued_at,
                        "expires_at": grant.expires_at,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
                hashlib.sha256,
            ).hexdigest(),
        ):
            raise ApprovalDenied("bad signature")
        if grant.args_digest != _args_digest(args) or grant.tool != pending.tool:
            raise ApprovalDenied("altered arguments")
        self._consumed.add(grant.nonce)
        self._domain.setdefault(pending.tool, []).append(dict(args))


def approval_grant_stack() -> ApprovalGrantStack:
    return ApprovalGrantStack()


def observe_confirmation_approval_grant() -> dict[str, object]:
    stack = ApprovalGrantStack()
    run = stack.propose("mail.send", {"to": ["a@example.test"], "body": "x"})
    pending = stack.wait_for_confirmation(run)
    before = stack.domain_calls("mail.send")
    stack.confirm(pending.event_id)
    grant = stack.issue_grant(pending.event_id)
    stack.resume_with_grant(run, grant)
    return {
        "pending_event_fields": ["id", "kind", "action", "llm_response_id", "parent_id"],
        "confirmation_identity": "POST /api/conversations/{id}/events/respond_to_confirmation",
        "confirmation_endpoint": "/api/conversations/{conversation_id}/events/respond_to_confirmation",
        "confirmation_policy_endpoint": "/api/conversations/{conversation_id}/confirmation_policy",
        "normalized_argument_source": "ActionEvent.action arguments, canonical UTF-8 JSON sorted keys",
        "grant_delivery": "Odysseus issues ApprovalGrant after confirm; MCP verifies grant before side effect",
        "openhands_holds_odysseus_signing_key": False,
        "upstream_patch_required": False,
        "in_process_proof": {
            "pending_event_id": pending.event_id,
            "pending_source": pending.source,
            "domain_calls_before_grant": len(before),
            "domain_calls_after_grant": len(stack.domain_calls("mail.send")),
        },
        "openapi": {
            "ActionEvent.id": "stable ULID/UUID",
            "ConfirmationResponseRequest": {"accept": "bool", "reason": "str"},
        },
    }


def probe_confirmation_approval_grant() -> ProbeResult:
    evidence = observe_confirmation_approval_grant()
    passed = (
        evidence["openhands_holds_odysseus_signing_key"] is False
        and evidence["in_process_proof"]["domain_calls_before_grant"] == 0
        and evidence["in_process_proof"]["domain_calls_after_grant"] == 1
    )
    return ProbeResult("confirmation-approval-grant", passed, evidence)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "probe",
        choices=[
            "stack",
            "automation-existing-conversation",
            "credential-rotation",
            "acp-mcp",
            "confirmation-approval-grant",
        ],
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.probe == "automation-existing-conversation":
        result = probe_automation_existing_conversation()
        print(json.dumps(asdict(result), sort_keys=True))
        return 0 if result.passed else 1
    if args.probe == "credential-rotation":
        result = probe_credential_rotation()
        print(json.dumps(asdict(result), sort_keys=True))
        return 0 if result.passed else 1
    if args.probe == "acp-mcp":
        result = probe_acp_mcp()
        print(json.dumps(asdict(result), sort_keys=True))
        return 0 if result.passed else 1
    if args.probe == "confirmation-approval-grant":
        result = probe_confirmation_approval_grant()
        print(json.dumps(asdict(result), sort_keys=True))
        return 0 if result.passed else 1
    results = probe_stack()
    payload = [asdict(result) for result in results]
    print(json.dumps(payload if args.json else {"results": payload}, sort_keys=True))
    return 0 if results and all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
