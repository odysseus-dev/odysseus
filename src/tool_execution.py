"""
tool_execution.py

Tool dispatcher and result formatter for the agent loop.
Routes tool blocks to MCP servers or native implementations.

Extracted from agent_tools.py.
"""

import asyncio
import collections
import contextvars
import json
import logging
import os
import pathlib
import re
import stat
import secrets
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, Awaitable, Callable, Dict, Iterator, Optional, Tuple



from src.tool_security import (
    BUILTIN_EMAIL_TOOLS,
    email_tool_policy_names,
    is_public_blocked_tool,
    owner_is_admin_or_single_user,
)
from src.tool_capabilities import ToolRunSecurityContext, blocked_tool_result
from src.tool_approvals import ExactToolApproval
from src.tool_policy import ToolPolicy
from src.client_tool_contract import TUI_ROUTED_BRIDGE_TOOL_NAMES
from src.constants import (
    DATA_DIR,
    AGENT_WORKSPACE_DIR,
    MAX_DIFF_LINES,
    MAX_OUTPUT_CHARS,
    MAX_READ_CHARS,
    WORKSPACE_MOUNT,
)
from src.path_confinement import canonical_root, confine, is_inside
from src.tool_utils import _truncate, get_mcp_manager
from src.tool_types import ToolBlock
from src.agent_runtime.resource_binding import (
    NATIVE_FILESYSTEM_TOOLS, active_resource_operation, bind_resource_operation,
    resolve_filesystem_operation,
)
from src.agent_runtime.resources import ExternalResource, NativeBackendResource, ResourceIdentityError
from src.agent_runtime.remote_resources import (
    active_backend_operation, bind_backend_operation, bind_backend_for_operation,
)
from src.agent_runtime.owned_resources import (
    active_owned_operation, bind_owned_operation, admit_owned_operation, needs_owned_binding,
)


class _MissingToolSecurityContext:
    pass


class _NoToolSecurityContext:
    """Explicit sentinel for non-agent callers that have no run provenance."""


_MISSING_TOOL_SECURITY_CONTEXT = _MissingToolSecurityContext()
NO_TOOL_SECURITY_CONTEXT = _NoToolSecurityContext()

# Persistent working directory for agent subprocesses.
# Resolves to <repo_root>/data/agent_workspace, inside the bind-mounted volume
# in Docker (/app/data), so files survive a rebuild as before. The subdirectory
# rather than data/ itself keeps agent scratch files and dotfiles out of the
# directory holding the session store and the auth database.
_AGENT_WORKDIR = AGENT_WORKSPACE_DIR


ExecutionBridgeHandler = Callable[
    [str, str, Optional[str], Optional[Dict[str, Any]]],
    Awaitable[Tuple[str, Dict[str, Any]]],
]


@dataclass(frozen=True)
class AgentExecutionBridge:
    """Request-scoped transport for tools owned by an external environment.

    Tool parsing, policy, approvals, evidence, and completion remain in the
    canonical loop. Only execution crosses this boundary. Context variables
    keep concurrent rollouts isolated without process-global monkeypatches.
    """

    route_tool: ExecutionBridgeHandler
    supported_tools: frozenset[str]
    name: str = "external_environment"
    endpoint_id: str = ""
    incarnation: str = field(default_factory=lambda: secrets.token_hex(16), init=False)
    configuration_id: str = ""

    def __post_init__(self) -> None:
        if not callable(self.route_tool):
            raise TypeError("execution bridge route_tool must be callable")
        if not self.supported_tools:
            raise ValueError("execution bridge supported_tools cannot be empty")

    def resource_identity(self, tool):
        from src.agent_runtime.resources import ExternalResource
        from src.agent_runtime.remote_resources import endpoint_identity
        endpoint = endpoint_identity(self.endpoint_id) if self.endpoint_id else "bridge:" + self.name
        return ExternalResource("execution_bridge", endpoint, self.name, tool, self.configuration_id or self.incarnation)


_active_execution_bridge: contextvars.ContextVar[AgentExecutionBridge | None] = (
    contextvars.ContextVar("agent_execution_bridge", default=None)
)


@contextmanager
def bind_execution_bridge(bridge: AgentExecutionBridge) -> Iterator[AgentExecutionBridge]:
    """Bind an external execution transport to the current rollout task."""

    if not isinstance(bridge, AgentExecutionBridge):
        raise TypeError("bridge must be an AgentExecutionBridge")
    token = _active_execution_bridge.set(bridge)
    try:
        yield bridge
    finally:
        _active_execution_bridge.reset(token)


def get_active_execution_bridge() -> AgentExecutionBridge | None:
    return _active_execution_bridge.get()


def _tui_host_bridge_endpoint_url(url: str, path: str) -> str:
    """Replace a validated bridge URL's path without changing its authority."""
    from src.agent_tools.subprocess_tools import host_shell_bridge_endpoint_url
    return host_shell_bridge_endpoint_url(url, path)


def _tui_host_bridge_patch_url(
    client_runtime_context: Optional[Dict[str, Any]],
) -> tuple[str, str] | None:
    if not isinstance(client_runtime_context, dict):
        return None
    if str(client_runtime_context.get("surface") or "").strip() != "odysseus-tui":
        return None
    bridge = (
        client_runtime_context.get("host_shell_bridge")
        or client_runtime_context.get("hostShellBridge")
    )
    if not isinstance(bridge, dict):
        return None
    url = str(bridge.get("url") or "").strip().rstrip("/")
    token = str(bridge.get("token") or "").strip()
    if not url or not token:
        return None
    from src.agent_tools.subprocess_tools import is_host_shell_bridge_url_allowed
    if not is_host_shell_bridge_url_allowed(url):
        return None
    return _tui_host_bridge_endpoint_url(url, "/patch"), token


async def _apply_patch_via_tui_host_bridge(
    patch_text: str,
    client_runtime_context: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    target = _tui_host_bridge_patch_url(client_runtime_context)
    if target is None:
        return {"error": "apply_patch: TUI host bridge is unavailable", "exit_code": 1}
    url, token = target
    request_id = secrets.token_urlsafe(18)
    try:
        import httpx

        timeout = httpx.Timeout(125.0, connect=5.0, write=10.0, pool=5.0)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.post(
                url,
                headers={"x-odysseus-tui-bridge-token": token},
                json={"patch": patch_text, "request_id": request_id},
            )
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if not isinstance(payload, dict) or not payload:
            return {
                "error": "apply_patch: host bridge returned an invalid payload",
                "exit_code": 1,
            }
        if response.status_code >= 400:
            payload.setdefault(
                "error",
                f"apply_patch: host bridge returned HTTP {response.status_code}",
            )
            payload["exit_code"] = 1
        elif payload.get("error"):
            payload["exit_code"] = 1
        else:
            payload.setdefault("exit_code", 0)
        exit_code = payload.get("exit_code")
        if exit_code is not None and (
            isinstance(exit_code, bool) or not isinstance(exit_code, int)
        ):
            return {
                "error": "apply_patch: host bridge returned an invalid exit_code",
                "exit_code": 1,
            }
        return payload
    except asyncio.CancelledError:
        bridge = (
            client_runtime_context.get("host_shell_bridge")
            or client_runtime_context.get("hostShellBridge")
            or {}
        )
        task = asyncio.create_task(
            _cancel_bridge_request(bridge, request_id),
            name=f"cancel-tui-patch-{request_id[:24]}",
        )
        _bridge_cancel_tasks.add(task)
        task.add_done_callback(_bridge_cancel_tasks.discard)
        raise
    except Exception as exc:
        return {"error": f"apply_patch: host bridge request failed: {exc}", "exit_code": 1}


async def _bridge_post(bridge: Dict, path: str, payload: Dict, *, timeout_s: float, err_prefix: str) -> Dict:
    url = str(bridge.get("url") or "").strip()
    token = str(bridge.get("token") or "").strip()
    from src.agent_tools.subprocess_tools import is_host_shell_bridge_url_allowed
    if not token or not is_host_shell_bridge_url_allowed(url):
        return {
            "error": f"{err_prefix}: invalid TUI host bridge",
            "exit_code": 1,
        }
    target = _tui_host_bridge_endpoint_url(url, path)
    try:
        import httpx
        timeout = httpx.Timeout(
            timeout_s + 5.0,
            connect=5.0,
            write=10.0,
            pool=5.0,
        )
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.post(
                target,
                headers={"x-odysseus-tui-bridge-token": token},
                json=payload,
            )
        try:
            result = response.json()
        except Exception:
            detail = str(response.text or "")[:500].strip()
            result = {
                "error": detail or f"{err_prefix}: bridge returned invalid JSON",
                "exit_code": 1,
            }
        if not isinstance(result, dict):
            result = {"error": f"{err_prefix}: bridge returned non-object response", "exit_code": 1}
        elif not result:
            result = {"error": f"{err_prefix}: bridge returned an empty response", "exit_code": 1}
        elif response.status_code >= 400:
            result.setdefault("error", f"{err_prefix}: bridge returned HTTP {response.status_code}")
            result["exit_code"] = 1
        elif result.get("error"):
            result["exit_code"] = 1
        else:
            result.setdefault("exit_code", 0)
        exit_code = result.get("exit_code")
        if exit_code is not None and (
            isinstance(exit_code, bool) or not isinstance(exit_code, int)
        ):
            return {
                "error": f"{err_prefix}: bridge returned an invalid exit_code",
                "exit_code": 1,
            }
        return result
    except asyncio.CancelledError:
        request_id = str(payload.get("request_id") or "").strip()
        if request_id and payload.get("detach") is not True:
            task = asyncio.create_task(
                _cancel_bridge_request(bridge, request_id),
                name=f"cancel-tui-bridge-{request_id[:24]}",
            )
            _bridge_cancel_tasks.add(task)
            task.add_done_callback(_bridge_cancel_tasks.discard)
        raise
    except Exception as exc:
        return {"error": f"{err_prefix}: bridge request failed: {exc}", "exit_code": 1}


_bridge_cancel_tasks: set[asyncio.Task[Any]] = set()


async def _cancel_bridge_request(bridge: Dict, request_id: str) -> None:
    """Best-effort cancellation for a host command whose rollout was interrupted."""
    url = str(bridge.get("url") or "").strip()
    token = str(bridge.get("token") or "").strip()
    from src.agent_tools.subprocess_tools import is_host_shell_bridge_url_allowed
    if not token or not is_host_shell_bridge_url_allowed(url):
        return
    target = _tui_host_bridge_endpoint_url(url, "/cancel")
    try:
        import httpx
        timeout = httpx.Timeout(5.0, connect=2.0, write=2.0, pool=2.0)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            await client.post(
                target,
                headers={"x-odysseus-tui-bridge-token": token},
                json={"request_id": request_id},
            )
    except Exception:
        logger.debug("Failed to cancel TUI bridge request %s", request_id, exc_info=True)


def _client_bridge(client_runtime_context: Optional[Dict]) -> Optional[Dict]:
    context = client_runtime_context if isinstance(client_runtime_context, dict) else {}
    if str(context.get("surface") or "").strip() != "odysseus-tui":
        return None
    bridge = context.get("host_shell_bridge") or context.get("hostShellBridge")
    if not isinstance(bridge, dict):
        return None
    url = str(bridge.get("url") or "").strip()
    token = str(bridge.get("token") or "").strip()
    if not url or not token:
        return None
    from src.agent_tools.subprocess_tools import is_host_shell_bridge_url_allowed
    if not is_host_shell_bridge_url_allowed(url):
        return None
    return bridge


_ROUTED_BRIDGE_TOOLS = TUI_ROUTED_BRIDGE_TOOL_NAMES
_BRIDGE_TOOL_TIMEOUT_S = 900.0
# ``write_file`` transports UTF-8 source/text only.  Keep this guard on the
# bridge route as well as the local WriteFileTool: native/Harbor runs use the
# bridge for workspace files, so otherwise a model can overwrite a verified
# PNG/PDF screenshot with a prose status message.
_TEXT_WRITE_BINARY_SUFFIXES = frozenset({
    ".bmp", ".gif", ".ico", ".jpeg", ".jpg", ".mp3", ".mp4", ".ogg",
    ".pdf", ".png", ".wav", ".webm", ".webp", ".zip",
})


def _text_write_to_binary_artifact_result(content: str) -> tuple[str, Dict] | None:
    """Reject UTF-8 text writes that target a binary artifact path."""
    raw = str(content or "")
    path, _, _body = raw.partition("\n")
    path = path.strip()
    if raw.lstrip().startswith("{"):
        try:
            args = json.loads(raw)
            if isinstance(args, dict) and isinstance(args.get("path"), str):
                path = args["path"].strip()
        except (TypeError, ValueError):
            pass
    if os.path.splitext(path)[1].casefold() not in _TEXT_WRITE_BINARY_SUFFIXES:
        return None
    return f"write_file: {path[:80]}", {
        "error": (
            f"write_file: refusing UTF-8 text for binary artifact path {path}. "
            "Use Python or a format-specific creation tool, then inspect the result."
        ),
        "exit_code": 1,
        "binary_artifact_preserved": True,
    }


async def _route_tool_via_bridge(tool: str, content: str, session_id: Optional[str], client_runtime_context: Optional[Dict]):
    import base64
    from urllib.parse import urlparse
    bridge = _client_bridge(client_runtime_context)
    if bridge is None:
        return tool, {"error": f"{tool}: TUI host bridge is not available", "exit_code": 1}

    url = str(bridge.get("url") or "").strip()
    parsed = urlparse(url)
    sanitized_endpoint = f"{parsed.scheme}://{parsed.netloc}{parsed.path}" if parsed.scheme and parsed.netloc else "tui_bridge"
    from src import containment
    spec = containment.agent_spec(agent_cwd(), {}, int(_BRIDGE_TOOL_TIMEOUT_S))
    grant = containment.declare_external_bridge(
        spec,
        owner=str(session_id or "tui_bridge"),
        endpoint=sanitized_endpoint,
    )
    boundary = grant.to_dict()
    boundary["executed"] = False

    if tool == "bash":
        from src.agent_tools.subprocess_tools import _host_shell_requires_detach, _host_shell_should_auto_poll

        is_background, command = _split_bg_marker(content)
        auto_poll = not is_background and _host_shell_should_auto_poll(command)
        display_command = command if is_background else content
        desc = f"bash: {display_command.strip().splitlines()[0][:80] if display_command.strip() else ''}"
        payload = {
            "command": command if is_background else content,
            "timeout": _BRIDGE_TOOL_TIMEOUT_S,
        }
        if is_background or _host_shell_requires_detach(command):
            payload["detach"] = True
        else:
            payload["request_id"] = secrets.token_urlsafe(18)
        result = await _bridge_post(
            bridge,
            "/run",
            payload,
            timeout_s=_BRIDGE_TOOL_TIMEOUT_S,
            err_prefix="bash",
        )
        # Keep implicit long commands from returning a false start-success.
        # Explicit #!bg is intentionally left for the agent's poll contract.
        if auto_poll and isinstance(result, dict) and result.get("job_id") and result.get("status") == "running":
            job_id = str(result["job_id"])
            deadline = time.monotonic() + 120.0
            while time.monotonic() < deadline:
                await asyncio.sleep(0.25)
                polled = await _bridge_post(
                    bridge,
                    "/run",
                    {"job_id": job_id},
                    timeout_s=30.0,
                    err_prefix="bash",
                )
                if not isinstance(polled, dict):
                    continue
                if polled.get("status") not in {"running", "unknown"}:
                    result = polled
                    break
            else:
                result = {
                    **result,
                    "detached": True,
                    "status": "running",
                    "job_id": job_id,
                    "output": "host job still running; poll the returned job_id",
                    "exit_code": 0,
                }
        if isinstance(result, dict):
            b = dict(boundary)
            b["executed"] = (result.get("exit_code") == 0 or (isinstance(result.get("exit_code"), int) and not result.get("error")))
            result["containment"] = b
        return desc, result
    if tool == "python":
        py_res = await _bridge_post(
            bridge,
            "/run",
            {
                "exec": ["python3", "-I", "-c", content],
                "timeout": _BRIDGE_TOOL_TIMEOUT_S,
                "request_id": secrets.token_urlsafe(18),
            },
            timeout_s=_BRIDGE_TOOL_TIMEOUT_S,
            err_prefix="python",
        )
        if isinstance(py_res, dict):
            b = dict(boundary)
            b["executed"] = (py_res.get("exit_code") == 0 or (isinstance(py_res.get("exit_code"), int) and not py_res.get("error")))
            py_res["containment"] = b
        return "python: (client)", py_res
    if tool == "grep":
        stripped = content.strip()
        try:
            args = json.loads(stripped) if stripped.startswith("{") else {"pattern": stripped}
        except (TypeError, ValueError):
            args = None
        if not isinstance(args, dict):
            return "grep: invalid arguments", {
                "error": "grep: expected a JSON object", "exit_code": 1,
            }
        pattern = args.get("pattern")
        path = args.get("path", ".")
        glob = args.get("glob")
        ignore_case = args.get("ignore_case", False)
        max_results = args.get("max_results", 200)
        if (
            not isinstance(pattern, str) or not pattern or "\n" in pattern or "\r" in pattern
            or not isinstance(path, str) or "\n" in path or "\r" in path
            or (glob is not None and (not isinstance(glob, str) or not glob))
            or not isinstance(ignore_case, bool)
            or isinstance(max_results, bool) or not isinstance(max_results, int)
            or max_results < 1 or max_results > 1000
        ):
            return "grep: invalid arguments", {
                "error": "grep: invalid pattern, path, glob, ignore_case, or max_results",
                "exit_code": 1,
            }
        payload = {
            "pattern": pattern,
            "path": path.strip() or ".",
            "ignore_case": ignore_case,
            "max_results": max_results,
        }
        if glob is not None:
            payload["glob"] = glob
        return f"grep: {pattern[:80]}", await _bridge_post(
            bridge, "/grep", payload, timeout_s=60.0, err_prefix="grep",
        )
    if tool in {"ls", "list_dir"}:
        stripped = content.strip()
        if stripped.startswith("{"):
            try:
                args = json.loads(stripped)
            except (TypeError, ValueError):
                return f"{tool}: invalid arguments", {
                    "error": f"{tool}: expected JSON with an optional path",
                    "exit_code": 1,
                }
            if not isinstance(args, dict):
                return f"{tool}: invalid arguments", {
                    "error": f"{tool}: expected a JSON object",
                    "exit_code": 1,
                }
            path_value = args.get("path", ".")
            offset = args.get("offset", 0)
            limit = args.get("limit", 0)
        else:
            path_value = stripped or "."
            offset = 0
            limit = 0
        if (
            not isinstance(path_value, str)
            or "\n" in path_value
            or "\r" in path_value
            or isinstance(offset, bool)
            or not isinstance(offset, int)
            or offset < 0
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit < 0
        ):
            return f"{tool}: invalid arguments", {
                "error": f"{tool}: path must be a string and ranges non-negative integers",
                "exit_code": 1,
            }
        path = path_value.strip() or "."
        return f"{tool}: {path[:80]}", await _bridge_post(
            bridge,
            "/list",
            {
                "path": path,
                "offset": offset,
                "limit": limit,
                "recursive": tool == "list_dir",
            },
            timeout_s=60.0,
            err_prefix=tool,
        )
    if tool in {"glob", "find_files"}:
        stripped = content.strip()
        if stripped.startswith("{"):
            try:
                args = json.loads(stripped)
            except (TypeError, ValueError):
                return f"{tool}: invalid arguments", {
                    "error": f"{tool}: expected JSON with pattern and optional path",
                    "exit_code": 1,
                }
            if not isinstance(args, dict):
                return f"{tool}: invalid arguments", {
                    "error": f"{tool}: expected a JSON object",
                    "exit_code": 1,
                }
            pattern_value = args.get("pattern")
            path_value = args.get("path", ".")
        else:
            pattern_value = stripped
            path_value = "."
        if (
            not isinstance(pattern_value, str)
            or not pattern_value.strip()
            or "\n" in pattern_value
            or "\r" in pattern_value
            or not isinstance(path_value, str)
            or "\n" in path_value
            or "\r" in path_value
        ):
            return f"{tool}: invalid arguments", {
                "error": f"{tool}: pattern and path must be single-line strings",
                "exit_code": 1,
            }
        pattern = pattern_value.strip()
        path = path_value.strip() or "."
        find_payload = {"path": path}
        if tool == "glob":
            find_payload["glob"] = pattern
        else:
            find_payload["pattern"] = pattern
        return f"{tool}: {pattern[:80]}", await _bridge_post(
            bridge,
            "/find",
            find_payload,
            timeout_s=60.0,
            err_prefix=tool,
        )
    if tool == "read_file":
        payload = {"path": content.split("\n", 1)[0].strip()}
        stripped = content.strip()
        if stripped.startswith("{"):
            try:
                args = json.loads(stripped)
            except (TypeError, ValueError):
                return "read_file: invalid arguments", {
                    "error": "read_file: expected JSON with a path",
                    "exit_code": 1,
                }
            if not isinstance(args, dict):
                return "read_file: invalid arguments", {
                    "error": "read_file: expected a JSON object",
                    "exit_code": 1,
                }
            offset = args.get("offset", 0)
            limit = args.get("limit", 0)
            if (
                isinstance(offset, bool)
                or not isinstance(offset, int)
                or offset < 0
                or isinstance(limit, bool)
                or not isinstance(limit, int)
                or limit < 0
            ):
                return "read_file: invalid arguments", {
                    "error": "read_file: offset and limit must be non-negative integers",
                    "exit_code": 1,
                }
            path_value = args.get("path")
            if (
                not isinstance(path_value, str)
                or not path_value.strip()
                or "\n" in path_value
                or "\r" in path_value
            ):
                return "read_file: invalid arguments", {
                    "error": "read_file: path must be a non-empty single-line string",
                    "exit_code": 1,
                }
            payload = {
                "path": path_value.strip(),
                "offset": offset,
                "limit": limit,
            }
        path = str(payload.get("path") or "")
        if not path:
            return "read_file: invalid arguments", {
                "error": "read_file: path is required",
                "exit_code": 1,
            }
        return f"read_file: {path[:80]}", await _bridge_post(
            bridge,
            "/read",
            payload,
            timeout_s=60.0,
            err_prefix="read_file",
        )
    if tool == "edit_file":
        try:
            args = json.loads(content.strip())
        except (TypeError, ValueError):
            return "edit_file: invalid arguments", {
                "error": "edit_file: expected JSON with path, old_string, and new_string",
                "exit_code": 1,
            }
        if not isinstance(args, dict):
            return "edit_file: invalid arguments", {
                "error": "edit_file: expected a JSON object",
                "exit_code": 1,
            }
        path_value = args.get("path")
        path = path_value.strip() if isinstance(path_value, str) else ""
        if "\n" in path or "\r" in path:
            path = ""
        old_string = args.get("old_string")
        new_string = args.get("new_string")
        replace_all = args.get("replace_all", False)
        if not path:
            return "edit_file: invalid arguments", {
                "error": "edit_file: path is required",
                "exit_code": 1,
            }
        if not isinstance(old_string, str) or not old_string:
            return "edit_file: invalid arguments", {
                "error": "edit_file: old_string is required",
                "exit_code": 1,
            }
        if not isinstance(new_string, str):
            return "edit_file: invalid arguments", {
                "error": "edit_file: new_string is required",
                "exit_code": 1,
            }
        if old_string == new_string:
            return "edit_file: invalid arguments", {
                "error": "edit_file: old_string and new_string are identical",
                "exit_code": 1,
            }
        if not isinstance(replace_all, bool):
            return "edit_file: invalid arguments", {
                "error": "edit_file: replace_all must be a boolean",
                "exit_code": 1,
            }
        return f"edit_file: {str(args.get('path') or '')[:80]}", await _bridge_post(
            bridge,
            "/edit",
            {
                "path": path,
                "old_string": old_string,
                "new_string": new_string,
                "replace_all": replace_all,
                "request_id": secrets.token_urlsafe(18),
            },
            timeout_s=60.0,
            err_prefix="edit_file",
        )
    stripped = content.strip()
    if stripped.startswith("{"):
        try:
            args = json.loads(stripped)
        except (TypeError, ValueError):
            return "write_file: invalid arguments", {
                "error": "write_file: expected JSON with path and content",
                "exit_code": 1,
            }
        if not isinstance(args, dict):
            return "write_file: invalid arguments", {
                "error": "write_file: expected a JSON object",
                "exit_code": 1,
            }
        path_value = args.get("path")
        path = path_value.strip() if isinstance(path_value, str) else ""
        if "\n" in path or "\r" in path:
            path = ""
        body_value = args.get("content")
        if not isinstance(body_value, str):
            return "write_file: invalid arguments", {
                "error": "write_file: content must be a string",
                "exit_code": 1,
            }
        body = body_value
    else:
        path, _, body = content.partition("\n")
        path = path.strip()
    if not path or path.endswith(("/", "\\")):
        return "write_file: invalid arguments", {
            "error": "write_file: path is required",
            "exit_code": 1,
        }
    rejected = _text_write_to_binary_artifact_result(content)
    if rejected is not None:
        return rejected
    from src.agent_tools.filesystem_tools import _parse_write_intent, _unwrap_fenced_source_body
    try:
        _, original_body, section, explicit_clear = _parse_write_intent(content)
    except ValueError as error:
        return "write_file: invalid arguments", {"error": str(error), "exit_code": 1}
    body = _unwrap_fenced_source_body(body, path)
    if not section or (not body.strip() and not explicit_clear):
        return "write_file: ambiguous content", {
            "error": "write_file: bridge cannot safely enforce implicit empty content; use explicit JSON string content",
            "exit_code": 1,
        }
    return f"write_file: {path[:80]}", await _bridge_post(
        bridge,
        "/write",
        {
            "path": path,
            "content_b64": base64.b64encode(body.encode("utf-8")).decode("ascii"),
            "request_id": secrets.token_urlsafe(18),
        },
        timeout_s=60.0,
        err_prefix="write_file",
    )



# ---------------------------------------------------------------------------
# Path confinement for read_file / write_file
# ---------------------------------------------------------------------------
# read_file + write_file are admin-only tools, but the path the agent
# supplies is model-controlled. Prompt-injection in an admin's chat can
# weaponise "read /etc/shadow" or "write ~/.ssh/authorized_keys" without
# the admin noticing.
#
# Policy:
#   1. Sensitive-subpath deny list — checked FIRST. Blocks .ssh,
#      .gnupg, shell rc files, token/env files even if the root above
#      them is on the allowlist.
#   2. Application-state deny (_is_app_state_path) - DATA_DIR holds the
#      session store, auth database, app key and settings, so only
#      _agent_readable_data_subdirs() is readable inside it.
#   3. Allowlist - only the directories the agent legitimately needs
#      (its data/ workspace, user content, system tmp). $HOME is NOT on
#      the default list.
#   4. Opt-in extra roots - admin can add broader roots via the
#      "tool_path_extra_roots" setting. These cannot re-open DATA_DIR;
#      rule 2 is independent of which root a path arrived through.
# ---------------------------------------------------------------------------

# Compatibility exports: the same deny predicate protects tool access and
# artifact observations, so hashing cannot become a sensitive-file side channel.
from src.agent_runtime.path_policy import (
    _SENSITIVE_BASENAMES, _SENSITIVE_FILE_PATTERNS,
    _SENSITIVE_BASENAMES_CF, _SENSITIVE_FILE_PATTERNS_CF, _is_sensitive_path,
)


def _path_within(resolved: str, root: str) -> bool:
    """True when *resolved* is *root* itself or sits underneath it.

    Use the platform's path-case rules.  This helper participates in allow
    decisions, so unconditional case-folding would let a distinct ``/DATA``
    tree masquerade as a descendant of ``/data`` on case-sensitive systems.
    """
    resolved, root = os.path.normcase(resolved), os.path.normcase(root)
    if resolved == root:
        return True
    try:
        if os.path.commonpath([resolved, root]) == root:
            return True
    except ValueError:
        return False
    # normcase is intentionally conservative about assumptions (notably on
    # POSIX), so consult the filesystem when paths exist.  This recognizes a
    # case alias on a case-insensitive volume without treating distinct
    # case-sensitive paths as the same allow root.
    if os.path.exists(root):
        candidate = resolved
        while True:
            try:
                if os.path.exists(candidate) and os.path.samefile(candidate, root):
                    return True
            except OSError:
                pass
            parent = os.path.dirname(candidate)
            if parent == candidate:
                break
            candidate = parent
    return False


def _path_within_conservative(resolved: str, root: str) -> bool:
    """Containment for deny decisions, folding case to fail closed."""
    resolved, root = resolved.casefold(), root.casefold()
    if resolved == root:
        return True
    try:
        return os.path.commonpath([resolved, root]) == root
    except ValueError:
        return False


def _agent_readable_data_subdirs() -> tuple[str, ...]:
    """The only parts of DATA_DIR the agent's file tools may reach.

    The agent's own scratch folder, plus the directories of user content whose
    paths the application itself gives to the model, which it would then be
    unable to open.  These normally live under DATA_DIR; the documented mail
    attachment override may instead name a disjoint external directory:

      UPLOAD_DIR            the chat upload manifest renders "path=<p>" and
                            says to read it with read_file (agent_loop.py)
      MAIL_ATTACHMENTS_DIR  download_attachment returns the path and its own
                            description tells the model to read it
      PERSONAL_DIR          GET /api/personal returns a path per file and is
                            reachable through the app_api tool; RUNBOOK_DIR
                            nests under it
      PERSONAL_UPLOADS_DIR  indexed as a personal-docs directory, which
                            manage_rag lists as an absolute path

    Order matters: the first entry is roots[0], which _resolve_search_root uses
    when grep/glob/ls are called with no path.
    """
    from src.constants import (
        DATA_DIR,
        MAIL_ATTACHMENTS_DIR,
        PERSONAL_DIR,
        PERSONAL_UPLOADS_DIR,
        UPLOAD_DIR,
    )
    configured = (
        (AGENT_WORKSPACE_DIR, "agent_workspace", False),
        (UPLOAD_DIR, "uploads", False),
        # This has a documented environment override and may legitimately
        # live outside DATA_DIR, but it must never equal/contain DATA_DIR.
        (MAIL_ATTACHMENTS_DIR, "mail-attachments", True),
        (PERSONAL_DIR, "personal_docs", False),
        (PERSONAL_UPLOADS_DIR, "personal_uploads", False),
    )
    configured_data_dir = os.path.abspath(os.path.expanduser(str(DATA_DIR)))
    data_dir = os.path.realpath(configured_data_dir)
    safe: list[str] = []
    for raw, internal_name, external_ok in configured:
        value = str(raw or "").strip()
        # These paths are security-policy roots, not ordinary allowlist
        # entries. Internal roles may inherit a relative DATA_DIR, but must
        # still resolve to their exact canonical child below. External mail
        # overrides require an absolute, disjoint directory.
        if not value:
            continue
        expanded = os.path.abspath(os.path.expanduser(value))
        # A policy root must not acquire an exemption by redirecting its final
        # path component to protected state or to an unrelated external tree.
        if os.path.islink(expanded):
            continue
        resolved = os.path.realpath(expanded)
        if os.path.exists(resolved) and not os.path.isdir(resolved):
            continue
        expected_internal = os.path.join(data_dir, internal_name)
        expected_configured = os.path.join(configured_data_dir, internal_name)
        inside_data = (
            os.path.normcase(expanded)
            in {
                os.path.normcase(expected_configured),
                os.path.normcase(expected_internal),
            }
            and resolved == expected_internal
        )
        external_safe = (
            external_ok
            and os.path.isabs(os.path.expanduser(value))
            and resolved != data_dir
            and os.path.dirname(resolved) != resolved
            and not _path_within(data_dir, resolved)
            and not _path_within(resolved, data_dir)
        )
        if not (inside_data or external_safe) or _is_sensitive_path(resolved):
            continue
        safe.append(resolved)
    return tuple(safe)


def _is_app_state_path(resolved: str) -> bool:
    """True for anything under DATA_DIR that is not agent-readable.

    DATA_DIR holds the session store, the auth database, the app encryption key
    and the settings file. A model-supplied path must not reach those through
    any root, so this is checked in both resolvers rather than expressed as an
    absence from the allowlist: a workspace bound at or above the data
    directory, or an opt-in tool_path_extra_roots entry covering it, would
    otherwise put them back in reach.

    A containment rule rather than a filename deny list, so state files added
    later are covered without anyone remembering to list them. The composed
    sensitive-name policy also denies settings.json and app.db in workspaces.
    """
    from src.constants import DATA_DIR
    if not _path_within_conservative(resolved, os.path.realpath(DATA_DIR)):
        return False
    return not any(
        _path_within(resolved, d)
        for d in _agent_readable_data_subdirs()
    )


def _is_hardlinked_regular_file(resolved: str) -> bool:
    """Reject inode aliases that can smuggle DATA_DIR state into an allow root."""
    try:
        target = os.stat(resolved, follow_symlinks=False)
    except OSError:
        return False
    return stat.S_ISREG(target.st_mode) and getattr(target, "st_nlink", 1) > 1


def _is_denied_tool_path(resolved: str, *, snapshot=None) -> bool:
    """Apply every path deny to a canonical traversal result.

    Directory scans pass one ``_control_plane_snapshot()`` for the whole walk;
    rebuilding it per entry made grep/ls/glob linear in protected-state stats.
    """
    from src.agent_runtime.resources import _control_plane_path
    return (
        _is_sensitive_path(resolved)
        or _is_app_state_path(resolved)
        or _is_hardlinked_regular_file(resolved)
        or _control_plane_path(resolved, snapshot=snapshot)
    )


def _can_traverse_tool_path(resolved: str, *, snapshot=None) -> bool:
    """Allow walking a denied state parent only to reach safe carve-outs."""
    if _is_sensitive_path(resolved):
        return False
    if not _is_app_state_path(resolved):
        from src.agent_runtime.resources import _control_plane_path
        return not _control_plane_path(resolved, snapshot=snapshot)
    return any(
        _path_within(readable, resolved)
        for readable in _agent_readable_data_subdirs()
    )


def _tool_path_roots() -> list[str]:
    """Return the list of directory roots that read_file / write_file
    may touch. Default: project data/ + system temp dirs. Extra roots
    are loaded from the ``tool_path_extra_roots`` setting.
    """
    roots: list[str] = []

    # The agent's workspace plus the user-content directories inside data/.
    # The rest of DATA_DIR is denied by _is_app_state_path.
    roots.extend(_agent_readable_data_subdirs())

    # /tmp (and its macOS realpath /private/tmp).
    roots.append("/tmp")
    try:
        private_tmp = os.path.realpath("/tmp")
        if private_tmp != "/tmp":
            roots.append(private_tmp)
    except OSError:
        pass

    # $TMPDIR — per-user temp root on macOS (e.g. /var/folders/.../T/).
    tmpdir = os.environ.get("TMPDIR")
    if tmpdir:
        roots.append(tmpdir)

    # Opt-in extra roots from settings.
    try:
        from src.settings import get_setting
        extra = get_setting("tool_path_extra_roots")
        if isinstance(extra, list):
            roots.extend(str(r) for r in extra if r)
    except Exception:
        pass

    # Deduplicate; resolve symlinks so containment is unambiguous.
    seen: set[str] = set()
    out: list[str] = []
    for r in roots:
        try:
            real = os.path.realpath(r)
        except OSError:
            continue
        if real in seen:
            continue
        seen.add(real)
        out.append(real)
    return out


def _resolve_tool_path(raw_path: str) -> str:
    """Resolve and confine a model-supplied path.

    Order of checks:
      1. Non-empty path.
      2. Sensitive-subpath deny list (blocks .ssh, .gnupg, etc.
         even when the root is on the allowlist).
      3. Allowlist containment (must land under one of the roots).

    Returns the realpath on success. Raises ValueError on rejection.
    Symlinks are resolved before comparison.

    When a workspace is active for this turn, paths are confined to it instead
    of the default allowlist (see _resolve_tool_path_in_workspace).
    """
    resource_operation = active_resource_operation()
    if resource_operation is not None:
        resolved = resource_operation.resolve_path(raw_path)
        if _is_denied_tool_path(resolved):
            raise ValueError("bound path is sensitive, application state or a hard-linked file")
        return resolved
    ws = get_active_workspace()
    if ws:
        return _resolve_tool_path_in_workspace(ws, raw_path)
    if raw_path is None or not str(raw_path).strip():
        raise ValueError("path is required")
    expanded = os.path.expanduser(str(raw_path).strip())
    resolved = os.path.realpath(expanded)

    if _is_app_state_path(resolved):
        raise ValueError(
            f"path '{raw_path}' is inside the application state directory"
        )
    if _is_sensitive_path(resolved):
        raise ValueError(
            f"path '{raw_path}' is inside a sensitive directory "
            f"(e.g. .ssh, .gnupg) or matches a sensitive filename"
        )
    if _is_hardlinked_regular_file(resolved):
        raise ValueError(f"path '{raw_path}' is a hard-linked file")

    if _is_denied_tool_path(resolved):
        raise ValueError("path addresses sensitive execution control-plane state")

    for root in _tool_path_roots():
        if is_inside(root, resolved):
            return resolved
    raise ValueError(
        f"path '{raw_path}' is outside the allowed roots"
    )


def _resolve_tool_path_in_workspace(workspace: str, raw_path: str) -> str:
    """Confine a model-supplied path to the active workspace.

    Layered on top of upstream's path policy: the workspace is the allowed
    root (relative paths resolve under it; paths that escape it are rejected),
    and the sensitive-file deny list (.ssh, .gnupg, id_rsa, …) still applies
    inside it. When no workspace is set, callers use _resolve_tool_path (the
    default data/tmp allowlist) instead.
    """
    if raw_path is None or not str(raw_path).strip():
        raise ValueError("path is required")
    base = canonical_root(workspace)
    expanded = os.path.expanduser(str(raw_path).strip())
    # `/workspace` is the stable user-facing agent root in tasks and docs.
    # Native/manual installs may bind the request to another physical folder;
    # resolve the alias inside that active workspace rather than rejecting it.
    if expanded == WORKSPACE_MOUNT:
        expanded = base
    elif expanded.startswith(WORKSPACE_MOUNT + "/"):
        expanded = os.path.join(base, expanded.removeprefix(WORKSPACE_MOUNT + "/"))
    try:
        resolved = confine(base, expanded)
    except (ValueError, OSError):
        raise ValueError(f"path '{raw_path}' is outside the workspace ({workspace})")
    # Confinement says "inside the root"; the deny list says "allowed". They
    # are separate questions and this one stays here, with the policy that
    # owns it.
    if _is_app_state_path(resolved):
        raise ValueError(
            f"path '{raw_path}' is inside the application state directory"
        )
    if _is_sensitive_path(resolved):
        raise ValueError(
            f"path '{raw_path}' is inside a sensitive directory "
            f"(e.g. .ssh, .gnupg) or matches a sensitive filename"
        )
    if _is_hardlinked_regular_file(resolved):
        raise ValueError(f"path '{raw_path}' is a hard-linked file")
    if _is_denied_tool_path(resolved):
        raise ValueError("path addresses sensitive execution control-plane state")
    return resolved



# ---------------------------------------------------------------------------
# Active workspace (per-turn, context-local)
# ---------------------------------------------------------------------------
# Set ONCE in execute_tool_block from the request's `workspace`. The path
# resolvers (_resolve_tool_path / _resolve_search_root) and the subprocess cwd
# helper (agent_cwd) read it from here, so confinement is enforced in a single
# place: any tool that resolves paths through these helpers is confined
# automatically and cannot accidentally bypass the workspace. contextvars are
# task-local, so concurrent turns don't leak into each other.
_active_workspace: contextvars.ContextVar = contextvars.ContextVar(
    "agent_active_workspace", default=None
)


def get_active_workspace() -> Optional[str]:
    """The folder the agent is confined to this turn, or None."""
    return _active_workspace.get()


def _display_tool_path(path: str) -> str:
    """Render a resolved workspace path through the stable `/workspace` alias."""

    value = str(path or "")
    workspace = get_active_workspace()
    if not workspace:
        return value
    base = os.path.realpath(workspace)
    resolved = os.path.realpath(value)
    try:
        relative = os.path.relpath(resolved, base)
    except ValueError:
        return value
    if relative == ".":
        return "/workspace"
    if relative == ".." or relative.startswith(".." + os.sep):
        return value
    return "/workspace/" + relative.replace(os.sep, "/")


def vet_workspace(raw: str) -> Optional[str]:
    """Validate a requested workspace path at bind time.

    Returns the canonical path, or None when it is unusable: not a real
    directory, or itself a sensitive path (.ssh, .gnupg, ...). The in-workspace
    resolver deny-lists sensitive paths *inside* the workspace, but the
    empty-path search root is the workspace itself, so the root has to be
    vetted before it is ever bound.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    resolved = os.path.realpath(os.path.expanduser(raw))
    if not os.path.isdir(resolved) or _is_sensitive_path(resolved):
        return None
    # Refuse the bind rather than binding a workspace where every subsequent
    # tool call would fail on the same deny list.
    if _is_denied_tool_path(resolved):
        return None
    # Reject filesystem roots: binding / (or a Windows drive/UNC root) as the
    # workspace would make every absolute path "inside" it, collapsing the
    # confinement into host-wide file access. A root is its own dirname, which
    # also covers C:\ and \\server\share without platform-specific lists.
    if os.path.dirname(resolved) == resolved:
        return None
    return resolved


def agent_cwd() -> str:
    """Working directory for agent subprocesses (bash/python/background jobs):
    a validated sealed launch root, active workspace, or agent workspace."""
    from src.agent_runtime.process_resources import active_process_operation
    bound = active_process_operation()
    if bound is not None and bound.launch is not None:
        bound.launch.scope.validate()
        root = bound.launch.scope.root.path
        if vet_workspace(root) != root:
            raise RuntimeError("sealed process root is not a safe real directory")
        return root
    workspace = get_active_workspace()
    if workspace:
        vetted = vet_workspace(workspace)
        if vetted is None:
            raise RuntimeError("active workspace is not a safe real directory")
        return vetted
    resolved = os.path.realpath(_AGENT_WORKDIR)
    if resolved not in _agent_readable_data_subdirs() or vet_workspace(resolved) != resolved:
        raise RuntimeError("agent workspace is not a safe real directory")
    return resolved


def get_mcp_manager():
    from src import agent_tools
    return agent_tools.get_mcp_manager()




def _resolve_search_root(raw_path: str) -> str:
    """Resolve + confine a code-nav path (grep/glob/ls).

    With a workspace active, the workspace folder is the root and a supplied
    path is confined inside it. Otherwise an empty path defaults to the agent's
    primary root (its workspace under the project data dir) and a supplied path
    is confined by the global allowlist + sensitive-file policy.
    """
    resource_operation = active_resource_operation()
    if resource_operation is not None:
        resolved = resource_operation.resolve_path(raw_path, search=True)
        if _is_denied_tool_path(resolved):
            raise ValueError("bound search root is sensitive, application state or a hard-linked file")
        return resolved
    raw = (raw_path or "").strip()
    ws = get_active_workspace()
    if ws:
        # Resolve the empty case as the workspace path rather than returning
        # it directly: returned unchecked it skipped both deny lists, so a
        # bare ls listed whatever the workspace was bound to.
        return _resolve_tool_path_in_workspace(ws, raw or ws)
    if not raw:
        roots = _tool_path_roots()
        default_root = os.path.realpath(AGENT_WORKSPACE_DIR)
        if default_root in roots and not _is_denied_tool_path(default_root):
            return default_root
        raise ValueError("default agent workspace is not a safe readable data subdirectory")
    return _resolve_tool_path(raw)

logger = logging.getLogger(__name__)


_ADMIN_TOOLS = {
    "app_api",
    "manage_endpoints",
    "manage_mcp",
    "manage_webhooks",
    "manage_tokens",
    "manage_settings",
    "download_model",
    "serve_model",
    "serve_preset",
    "stop_served_model",
    "cancel_download",
}


def _owner_is_admin(owner: Optional[str]) -> bool:
    """Mirror route-level admin behavior for agent tool execution."""
    return owner_is_admin_or_single_user(owner)

# ---------------------------------------------------------------------------
# MCP-backed tool helpers
# ---------------------------------------------------------------------------

# Map legacy tool names -> (MCP server_id, MCP tool_name)
_MCP_TOOL_MAP = {
    "bash":           ("bash",       "bash"),
    "python":         ("python",     "python"),
    "read_file":      ("filesystem", "read_file"),
    "write_file":     ("filesystem", "write_file"),
    "web_search":     ("web_search", "web_search"),
    "web_fetch":      ("web_fetch",  "web_fetch"),
    "generate_image": ("image_gen",  "generate_image"),
}
_EMAIL_MCP_OWNER_ARG = "_odysseus_owner"
_EMAIL_MCP_SESSION_ARG = "_odysseus_session_id"


def _parse_qualified_mcp_args(tool: str, content: str) -> tuple[Dict, Optional[str]]:
    raw = (content or "").strip()
    if not raw:
        return {}, None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        if tool.startswith("mcp__email__"):
            return {}, "Email MCP tool arguments must be a JSON object."
        return {}, None
    if not isinstance(parsed, dict):
        if tool.startswith("mcp__email__"):
            return {}, "Email MCP tool arguments must be a JSON object."
        return {}, None
    return parsed, None


def _parse_generate_image(content: str) -> Dict:
    lines = content.strip().split("\n")
    args = {"prompt": lines[0].strip() if lines else ""}
    for i, key in enumerate(["model", "size", "quality"], 1):
        if len(lines) > i and lines[i].strip():
            args[key] = lines[i].strip()
    return args


def _parse_manage_memory(content: str) -> Dict:
    lines = content.strip().split("\n")
    action = lines[0].strip().lower() if lines else ""
    args = {"action": action}
    if action == "add":
        args["text"] = lines[1].strip() if len(lines) > 1 else ""
        if len(lines) > 2 and lines[2].strip():
            args["category"] = lines[2].strip().lower()
    elif action == "edit":
        args["memory_id"] = lines[1].strip() if len(lines) > 1 else ""
        args["text"] = lines[2].strip() if len(lines) > 2 else ""
    elif action == "delete":
        args["memory_id"] = lines[1].strip() if len(lines) > 1 else ""
    elif action == "search":
        args["text"] = lines[1].strip() if len(lines) > 1 else ""
    elif action == "list":
        if len(lines) > 1 and lines[1].strip():
            args["category"] = lines[1].strip().lower()
    return args


def _parse_write_file(content: str) -> Dict:
    lines = content.split("\n", 1)
    return {"path": lines[0].strip(), "content": lines[1] if len(lines) > 1 else ""}


_MCP_ARG_PARSERS: Dict[str, Callable[[str], Dict[str, str]]] = {
    "bash":           lambda c: {"command": c},
    "python":         lambda c: {"code": c},
    "web_search":     lambda c: {"query": c.split("\n")[0].strip()},
    "web_fetch":      lambda c: {"url": c.split("\n")[0].strip()},
    "read_file":      lambda c: {"path": c.split("\n")[0].strip()},
    "write_file":     _parse_write_file,
    "generate_image": _parse_generate_image,
    "manage_memory":  _parse_manage_memory,
}


# Primary argument key(s) for the legacy line-parsed tools. When a fenced
# block's content is a JSON object carrying one of these keys, it's structured
# inline args (the relaxed parser's ```web_search {"query": "..."}``` shape) —
# use the object directly instead of letting the line-based parsers wrap the
# whole JSON string as the query/url/path/prompt. Keyed off membership only
# (the primary key never changes), so this can't drift; an unrecognized object
# safely falls through to the line-based parser, i.e. the previous behavior.
#
# IMPORTANT — this only covers the MCP path. _build_mcp_args is reached via
# _call_mcp_tool only for _MCP_TOOL_MAP tools (so an entry outside that map is
# dead, as manage_memory was). And of these, only generate_image has a live MCP
# server today; web_search/web_fetch/read_file/write_file have none, so they run
# via _direct_fallback -> TOOL_HANDLERS, whose handlers decode JSON themselves
# (see ReadFileTool/WriteFileTool/WebSearchTool/WebFetchTool). The entries here
# are kept as defense-in-depth for if/when those servers are added. The live
# fix for each server-less tool lives in its handler. test_write_file_inline_
# json_args and test_mcp_json_primary_keys_are_all_live pin both halves.
_MCP_JSON_PRIMARY_KEYS: Dict[str, tuple] = {
    "web_search":     ("query", "queries"),
    "web_fetch":      ("url",),
    "read_file":      ("path",),
    "write_file":     ("path",),
    "generate_image": ("prompt",),
}


def _build_mcp_args(tool: str, content: str) -> Dict:
    """Convert fenced-block text content to structured MCP arguments."""
    primaries = _MCP_JSON_PRIMARY_KEYS.get(tool)
    if primaries and content.strip().startswith("{"):
        try:
            decoded = json.loads(content.strip())
        except (json.JSONDecodeError, TypeError):
            decoded = None
        if isinstance(decoded, dict) and any(k in decoded for k in primaries):
            return decoded
    parser = _MCP_ARG_PARSERS.get(tool)
    return parser(content) if parser else {}


def _normalize_mcp_text_error(result: Dict) -> Dict:
    """Lift an explicit Error: TextContent result into the host error shape."""
    if isinstance(result, dict) and result.get("exit_code") in (None, 0):
        stdout = str(result.get("stdout") or "").strip()
        if re.match(r"^Error:\s*", stdout, re.IGNORECASE):
            result["error"] = re.sub(r"^Error:\s*", "", stdout, flags=re.IGNORECASE)
            result["exit_code"] = 1
    return result


async def _call_mcp_tool(
    tool: str,
    content: str,
    progress_cb: Optional[Callable[[Dict], Awaitable[None]]] = None,
) -> Dict:
    """Route a legacy tool call through the MCP manager, with direct fallbacks."""
    bound = active_backend_operation()
    if bound is not None and isinstance(bound.resource, NativeBackendResource):
        return await _direct_fallback(tool, content, progress_cb=progress_cb) or {"error": f"Native tool '{tool}' unavailable", "exit_code": 1}
    mcp = get_mcp_manager()
    if not mcp:
        if bound is not None:
            raise ResourceIdentityError("Pinned MCP backend is unavailable")
        return await _direct_fallback(tool, content, progress_cb=progress_cb) or {"error": f"MCP manager not available for tool '{tool}'", "exit_code": 1}

    server_id, tool_name = _MCP_TOOL_MAP[tool]
    qualified = f"mcp__{server_id}__{tool_name}"
    args = _build_mcp_args(tool, content)
    result = await mcp.call_tool(qualified, args)

    # Stdio MCP servers can only return TextContent, so an explicit
    # ``Error: ...`` may arrive as stdout with exit_code=0. Normalize that
    # transport shape before success checks, audit capture, and loop breaking.
    result = _normalize_mcp_text_error(result)

    # If MCP server not connected, try direct fallback
    if bound is None and isinstance(result, dict) and result.get("exit_code") == 1 and "not connected" in result.get("error", ""):
        fallback = await _direct_fallback(tool, content, progress_cb=progress_cb)
        if fallback:
            return fallback

    # generate_image runs as a text-only MCP tool, so the saved image URL never
    # reaches the agent loop's structured forwarding (which renders the image via
    # buildImageBubble on result["image_url"]). Lift it out of the tool's stdout so
    # the image renders deterministically — no dependence on the model echoing the
    # URL into its prose (which it mangles/hallucinates).
    if tool == "generate_image":
        _promote_image_fields(result)

    return result


def _promote_image_fields(result: Dict) -> None:
    """Lift the image URL (+ prompt/model/size) from a successful generate_image MCP
    text result into structured fields the agent loop already forwards to
    buildImageBubble. Only acts on a dict result with exit_code 0; matches the
    generated-image URL by pattern (absolute or relative) so it's robust to the
    result's wording."""
    if not isinstance(result, dict) or result.get("exit_code") != 0:
        return
    out = result.get("stdout") or ""
    m = re.search(r'(?:https?://[^\s)\]]+)?/api/generated-image/[A-Za-z0-9._-]+', out)
    if not m:
        return
    result["image_url"] = m.group(0).strip()
    for field, pat in (
        ("image_prompt", r'^Generated image for:\s*(.+)$'),
        ("image_model", r'^model:\s*(.+)$'),
        ("image_size", r'^size:\s*(.+)$'),
    ):
        fm = re.search(pat, out, re.M)
        if fm:
            result[field] = fm.group(1).strip()


_BG_MARKERS = {"#!bg", "#bg", "# bg", "#background", "# background", "@background", "# @background"}


def _split_bg_marker(content: str):
    """If the bash content's first non-empty line is a background marker
    (e.g. `#!bg`), return (True, command_without_marker); else (False, content)."""
    lines = content.split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].strip().lower() in _BG_MARKERS:
        del lines[i]
        return True, "\n".join(lines).strip()
    return False, content


# Variables a legitimate agent bash/python subprocess needs from the host.
# Anything not listed here is never inherited.
_SAFE_SUBPROCESS_VARS = frozenset({
    # POSIX execution
    "PATH", "LANG", "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "TZ",
    "USER", "LOGNAME", "SHELL", "TMPDIR", "TEMP", "TMP",
    # Python isolation / virtualenvs
    "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV",
    "ODYSSEUS_PYTHON_TOOL_SITE_PACKAGES",
    # Windows system essentials
    "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT",
    "ALLUSERSPROFILE", "PROGRAMDATA", "COMMONPROGRAMFILES",
    "PROGRAMFILES", "PROGRAMFILES(X86)",
    # XDG / runtime
    "XDG_RUNTIME_DIR", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
    # Bubblewrap / container paths
    "LD_LIBRARY_PATH",
})

def _agent_subprocess_env() -> dict:
    base = {
        key: os.environ[key]
        for key in _SAFE_SUBPROCESS_VARS
        if key in os.environ
    }
    base.setdefault("PATH", os.environ.get("PATH") or os.defpath or "/usr/local/bin:/usr/bin:/bin")
    base.setdefault("LANG", "C.UTF-8")
    base.update({"TERM": "xterm-256color", "COLUMNS": "120", "LINES": "40", "HOME": _AGENT_WORKDIR})
    return base


async def _direct_fallback(
    tool: str,
    content: str,
    progress_cb: Optional[Callable[[Dict], Awaitable[None]]] = None,
    session_id: Optional[str] = None,
    owner: Optional[str] = None,
    client_runtime_context: Optional[Dict[str, Any]] = None,
    disabled_tools: Optional[set] = None,
    tool_policy: Optional[ToolPolicy] = None,
) -> Optional[Dict]:
    _subproc_env = _agent_subprocess_env()

    try:
        owned = active_owned_operation()
        if owned is not None:
            owned.validate()
        ctx = {
            "progress_cb": progress_cb,
            "subproc_env": _subproc_env,
            "session_id": session_id,
            "owner": owner,
            "client_runtime_context": client_runtime_context,
            "disabled_tools": frozenset(disabled_tools or ()),
            "tool_policy": tool_policy,
            "request_authority": active_request_authority(),
            "resource_operation": active_resource_operation(),
            "owned_operation": active_owned_operation(),
        }

        from src.agent_tools import TOOL_HANDLERS
        if tool in TOOL_HANDLERS:
            return await TOOL_HANDLERS[tool](content, ctx)

    except Exception as e:
        return {"error": f"{tool}: {e}", "exit_code": 1}

    return None


async def _document_tool_dispatch(
    tool: str,
    content: str,
    session_id: Optional[str] = None,
    owner: Optional[str] = None,
    document_id: Optional[str] = None,
    document_version: Optional[int] = None,
    document_digest: Optional[str] = None,
) -> Optional[Dict]:
    """Route a document tool through TOOL_HANDLERS with the right ctx shape."""
    from src.agent_tools import TOOL_HANDLERS
    owned = active_owned_operation()
    if owned is not None:
        owned.validate()
    ctx = {
        "session_id": session_id,
        "owner": owner,
        "doc_id": document_id,
        "expected_document_version": document_version,
        "expected_document_digest": document_digest,
    }
    if tool in TOOL_HANDLERS:
        return await TOOL_HANDLERS[tool](content, ctx)
    return None


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

from src.agent_runtime.journal import dispatched, mark_authorized, mark_dispatch, record_action
from src.agent_runtime.authority import (
    MISSING_AUTHORITY, ExactOperation, RequestAuthority, active_request_authority,
    bind_request_authority,
)
from src.agent_runtime.process_resources import (
    active_process_operation, bind_process_operation, needs_process_binding, resolve_process_operation,
)
from src.browser_identity import (
    native_browser, parse_operation as parse_browser_operation, SESSION_ACTIONS, PAGE_FAILURE,
    page_unavailable, resolve_browser_operation, bind_browser_operation, revalidate_browser_operation,
)


@record_action
async def execute_tool_block(
    block: Any,
    session_id: Optional[str] = None,
    disabled_tools: Optional[set] = None,
    owner: Optional[str] = None,
    progress_cb: Optional[Callable[[Dict], Awaitable[None]]] = None,
    workspace: Optional[str] = None,
    tool_policy: Optional[Any] = None,
    security_context: (
        ToolRunSecurityContext
        | _NoToolSecurityContext
        | _MissingToolSecurityContext
    ) = _MISSING_TOOL_SECURITY_CONTEXT,
    exact_approval: Optional[ExactToolApproval] = None,
    active_document_id: Optional[str] = None,
    client_runtime_context: Optional[Dict[str, Any]] = None,
    request_authority=MISSING_AUTHORITY,
) -> Tuple[str, Dict]:
    """Execute a single tool block. Returns (description, result_dict).

    Thin wrapper: bind the per-turn workspace (so the path resolvers + subprocess
    cwd confine to it) for the duration of this call, then delegate. Reset on the
    way out so the binding never leaks to the next tool call.
    """
    if security_context is _MISSING_TOOL_SECURITY_CONTEXT:
        raise TypeError(
            "execute_tool_block requires security_context; pass a "
            "ToolRunSecurityContext or NO_TOOL_SECURITY_CONTEXT explicitly"
        )
    if (
        not isinstance(security_context, ToolRunSecurityContext)
        and security_context is not NO_TOOL_SECURITY_CONTEXT
    ):
        raise TypeError(
            "security_context must be a ToolRunSecurityContext or "
            "NO_TOOL_SECURITY_CONTEXT"
        )

    # Credential denial precedes authority, exact approval claim and bridges.
    if (isinstance(security_context, ToolRunSecurityContext)
            and security_context.delegated_credential):
        from src.tool_capabilities import delegated_tool_is_blocked
        if delegated_tool_is_blocked(getattr(block, "tool_type", None), getattr(block, "content", None)):
            decision = security_context.decision_for(block.tool_type, block.content)
            return blocked_tool_result(block.tool_type, decision.reason)

    authority = active_request_authority() if request_authority is MISSING_AUTHORITY else request_authority
    parent = active_request_authority()
    if isinstance(authority, RequestAuthority) and parent is not None and authority is not parent:
        authority = parent.intersect(authority)
    try:
        operation = ExactOperation.normalize(getattr(block, "tool_type", None), getattr(block, "content", None))
        valid = isinstance(authority, RequestAuthority) and authority.bound_to(
            owner=owner, session_id=session_id, workspace=workspace)
        if valid:
            authority = authority.restrict(tool_policy, disabled_tools)
        exact_admission = bool(
            valid and not authority.inherited and not authority.restricted(operation)
            and exact_approval is not None and exact_approval.matches(
                owner=owner, session_id=session_id, workspace=workspace,
                tool_name=getattr(block, "tool_type", None), content=getattr(block, "content", None)))
        admitted = valid and (authority.permits(operation) or exact_admission)
    except (ValueError, TypeError) as error:
        return f"{getattr(block, 'tool_type', '')}: invalid arguments", {
            "error": (f"Tool arguments are not valid JSON: {error}"
                      if isinstance(error, json.JSONDecodeError) else str(error)),
            "exit_code": 1, "blocked": True,
            "failure_kind": "request_authority_denied",
        }
    if not admitted:
        reason = "The exact operation is outside server request authority."
        if tool_policy and any(tool_policy.blocks(name) for name in email_tool_policy_names(getattr(block, "tool_type", ""))):
            reason = f"Execution of tool '{getattr(block, 'tool_type', '')}' is forbade by the active tool policy."
        elif isinstance(authority, RequestAuthority) and authority.restricted(operation):
            reason = "The exact operation is disabled by user or server request authority policy."
        return f"{getattr(block, 'tool_type', '')}: BLOCKED", {
            "error": reason,
            "exit_code": 1, "blocked": True, "failure_kind": "request_authority_denied",
        }

    from src.turn_contract import active_turn_contract
    contract = active_turn_contract()
    if contract is not None and not contract.permits(getattr(block, "tool_type", "")):
        return f"{getattr(block, 'tool_type', '')}: BLOCKED", {
            "error": "Tool is outside the requested turn capabilities.",
            "exit_code": 1, "failure_kind": "turn_contract_denied",
        }

    transport = operation.transport_tool
    if operation.tool == "private_browser":
        try:
            _, browser_args = parse_browser_operation(operation.input)
        except (ValueError, TypeError):
            return f"{transport}: UNSUPPORTED", {**page_unavailable(), "error": "Browser raw commands, flags and batches are unsupported."}
        if browser_args["action"] not in SESSION_ACTIONS:
            return f"{transport}: UNSUPPORTED", page_unavailable()
    # Raw global Playwright MCP has no authoritative session/page observation.
    # Its transport process and remote backend identity cannot substitute for it.
    if transport.startswith("mcp__") and transport.rsplit("__", 1)[-1] in {
        "browser_click", "browser_fill_form", "browser_type", "browser_press_key", "browser_evaluate",
        "browser_navigate", "browser_navigate_back", "browser_snapshot", "browser_take_screenshot",
        "browser_wait_for", "browser_tabs", "browser_close", "browser_run_code", "browser_network_requests",
        "browser_console_messages", "browser_drag", "browser_hover", "browser_select_option",
        "browser_file_upload", "browser_handle_dialog", "browser_resize", "browser_install"}:
        return f"{transport}: UNSUPPORTED", page_unavailable()
    try:
        pending = exact_approval.pending if exact_approval is not None else None
        if pending is not None and pending.backend_operation is None:
            raise ResourceIdentityError("Approved action has no sealed backend identity")
        backend_operation = bind_backend_for_operation(
            authority, operation, context=client_runtime_context,
            approved=pending.backend_operation if pending is not None else None,
            exact_admission=exact_admission)
        external_resource_call = isinstance(backend_operation.resource, ExternalResource)
        if operation.tool == "private_browser" and external_resource_call:
            raise ResourceIdentityError("External backend cannot supply native browser session authority")
        owned_operation = None
        process_operation = None
        browser_operation = None
        if native_browser(operation, backend_operation.resource):
            _, browser_args = parse_browser_operation(operation.input)
            if browser_args["action"] not in SESSION_ACTIONS:
                return f"{transport}: UNSUPPORTED", page_unavailable()
            if pending is not None and pending.browser_operation is None:
                raise ResourceIdentityError("Approved action has no sealed browser identity")
            browser_operation = resolve_browser_operation(authority, operation,
                approved=pending.browser_operation if pending is not None else None, exact_admission=exact_admission)
            await revalidate_browser_operation(browser_operation)
        if needs_process_binding(operation, backend_operation.resource):
            if pending is not None and pending.process_operation is None:
                raise ResourceIdentityError("Approved action has no sealed process/job identity")
            process_operation = resolve_process_operation(authority, operation, backend_operation.resource,
                approved=pending.process_operation if pending is not None else None, exact_admission=exact_admission)
        if needs_owned_binding(operation) and not external_resource_call:
            if pending is not None and pending.owned_operation is None:
                raise ResourceIdentityError("Approved action has no sealed owned resource identity")
            owned_operation = admit_owned_operation(
                authority, operation, document_id=active_document_id,
                approved=pending.owned_operation if pending is not None else None,
                exact_admission=exact_admission)
    except (ValueError, TypeError, OSError) as error:
        return f"{transport}: BLOCKED", {
            "error": str(error), "exit_code": 1, "blocked": True,
            "failure_kind": "resource_identity_denied",
            **({"policy": "exact_tool_approval"} if exact_approval is not None else {}),
        }
    resource_operation = None
    if operation.tool in NATIVE_FILESYSTEM_TOOLS and not external_resource_call:
        try:
            roots = authority.resource_roots
            approved_resource = exact_approval.pending.resource_operation if exact_approval is not None else None
            if exact_approval is not None and approved_resource is None:
                raise ValueError("Approved filesystem action has no sealed resource identity")
            if exact_admission and not roots and approved_resource is not None:
                # This single exact action can use only the roots sealed with
                # its proposal. The request/child authority is never widened.
                roots = tuple(dict.fromkeys(b.resource.root for b in approved_resource.bindings))
            if any(r.owner and r.owner != authority.owner for r in roots):
                raise ValueError("Filesystem resource owner differs from request authority")
            resource_operation = resolve_filesystem_operation(
                operation, roots=roots, workspace=authority.workspace, request_id=authority.request_id)
            if approved_resource is not None:
                if approved_resource.request_id and approved_resource.request_id != authority.request_id:
                    raise ValueError("Approved resource belongs to another request")
                if replace(resource_operation, request_id=approved_resource.request_id) != approved_resource:
                    raise ValueError("Approved filesystem resource identity changed")
        except (ValueError, TypeError, OSError, RuntimeError) as error:
            return f"{transport}: BLOCKED", {
                "error": str(error), "exit_code": 1, "blocked": True,
                "failure_kind": "resource_identity_denied",
            }

    approval_claimed = False
    if exact_approval is not None:
        if (
            not isinstance(security_context, ToolRunSecurityContext)
            or not security_context.external_untrusted_context_seen
            or not exact_approval.pending.external_untrusted_context_seen
        ):
            return (
                f"{getattr(block, 'tool_type', None)}: BLOCKED",
                {
                    "error": "Exact-action approval requires an armed run security context.",
                    "exit_code": 1,
                    "blocked": True,
                    "policy": "exact_tool_approval",
                },
            )
        if (
            exact_approval.pending.tool_name
            in {"edit_document", "suggest_document", "update_document"}
            and (
                not exact_approval.pending.document_id
                or exact_approval.pending.document_version is None
                or not exact_approval.pending.document_digest
            )
        ):
            return (
                f"{getattr(block, 'tool_type', None)}: BLOCKED",
                {
                    "error": (
                        "The approved document action has no sealed target and "
                        "cannot be executed."
                    ),
                    "exit_code": 1,
                    "blocked": True,
                    "policy": "exact_tool_approval",
                },
            )
        sealed_workspace = exact_approval.pending.workspace
        if sealed_workspace and vet_workspace(sealed_workspace) != sealed_workspace:
            return (
                f"{getattr(block, 'tool_type', None)}: BLOCKED",
                {
                    "error": (
                        "The approved workspace is no longer a valid safe "
                        "directory. Review the action again."
                    ),
                    "exit_code": 1,
                    "blocked": True,
                    "policy": "exact_tool_approval",
                },
            )
        approval_claimed = exact_approval.claim(
            owner=owner,
            session_id=session_id,
            tool_name=getattr(block, "tool_type", None),
            content=getattr(block, "content", None),
            workspace=workspace,
        )
        if not approval_claimed:
            return (
                f"{getattr(block, 'tool_type', None)}: BLOCKED",
                {
                    "error": "The exact-action approval did not match this tool request.",
                    "exit_code": 1,
                    "blocked": True,
                    "policy": "exact_tool_approval",
                },
            )

    if isinstance(security_context, ToolRunSecurityContext) and not approval_claimed:
        decision = security_context.decision_for(
            getattr(block, "tool_type", None),
            getattr(block, "content", None),
        )
        if not decision.allowed:
            logger.warning(
                "External-context policy blocked tool=%r",
                getattr(block, "tool_type", None),
            )
            return blocked_tool_result(
                getattr(block, "tool_type", None),
                decision.reason or "Tool blocked by external-context policy.",
            )

    token = _active_workspace.set(workspace or None)
    try:
        backend_operation.validate(client_runtime_context)
        if process_operation is not None and approval_claimed:
            process_operation = replace(process_operation, exact_approval=exact_approval)
        if browser_operation is not None and approval_claimed:
            browser_operation = replace(browser_operation, exact_approval=exact_approval)
        normalized = resource_operation or owned_operation
        sealed_document = owned_operation or (exact_approval.pending if approval_claimed else None)
        with (bind_request_authority(authority), bind_resource_operation(resource_operation),
              bind_backend_operation(backend_operation), bind_owned_operation(owned_operation),
              bind_process_operation(process_operation), bind_browser_operation(browser_operation)):
            output = await _execute_tool_block_impl(
                ToolBlock(transport, normalized.execution_input) if normalized is not None else block,
                session_id=session_id,
                disabled_tools=disabled_tools,
                owner=owner,
                progress_cb=progress_cb,
                tool_policy=tool_policy,
                approved_document_id=sealed_document.document_id if sealed_document is not None else None,
                approved_document_version=sealed_document.document_version if sealed_document is not None else None,
                approved_document_digest=sealed_document.document_digest if sealed_document is not None else None,
                active_document_id=active_document_id,
                client_runtime_context=client_runtime_context,
            )
        if isinstance(security_context, ToolRunSecurityContext):
            security_context.observe_tool_result(
                getattr(block, "tool_type", None),
                output[1],
                getattr(block, "content", None),
            )
        return output
    except ResourceIdentityError as error:
        if native_browser(operation, backend_operation.resource) and str(error) == PAGE_FAILURE:
            return f"{transport}: UNSUPPORTED", page_unavailable()
        return f"{transport}: BLOCKED", {
            "error": str(error), "exit_code": 1, "blocked": True,
            "failure_kind": "resource_identity_denied",
        }
    finally:
        _active_workspace.reset(token)


async def _execute_tool_block_impl(
    block: Any,
    session_id: Optional[str] = None,
    disabled_tools: Optional[set] = None,
    owner: Optional[str] = None,
    progress_cb: Optional[Callable[[Dict], Awaitable[None]]] = None,
    tool_policy: Optional[Any] = None,
    approved_document_id: Optional[str] = None,
    approved_document_version: Optional[int] = None,
    approved_document_digest: Optional[str] = None,
    active_document_id: Optional[str] = None,
    client_runtime_context: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Dict]:
    """Execute a single tool block. Returns (description, result_dict).

    `progress_cb` is forwarded to long-running subprocess tools
    (bash, python) so the agent loop can emit `tool_progress` SSE
    events while the command is in flight. Ignored by other tools.
    """
    from src.tool_implementations import (
        do_search_chats, do_manage_tasks,
        do_manage_skills, do_api_call, do_manage_notes,
        do_manage_calendar,
        do_download_model, do_serve_model, do_list_served_models, do_stop_served_model,
        do_tail_serve_output,
        do_list_downloads, do_cancel_download, do_search_hf_models, do_list_cached_models,
        do_list_serve_presets, do_serve_preset, do_adopt_served_model,
        do_list_cookbook_servers,
        do_edit_image, do_trigger_research, do_manage_research, do_resolve_contact,
        do_manage_contact,
        do_vault_search, do_vault_get, do_vault_unlock,
        do_app_api,
    )

    # HACK:
    # This is a temporary workaround for a circular dependency between
    # tool_execution.py and agent_tools.__init__.py.
    #
    # See issue #4277:
    # refactor(tools): Move the registry from __init__.py into a
    # dedicated registry.py module.
    #
    # Do not copy this pattern elsewhere. This import should be removed
    # once the registry refactor is completed.
    try:
        agent_tools_mod = __import__("src.agent_tools", fromlist=["TOOL_HANDLERS"])
        dynamic_handlers = getattr(agent_tools_mod, "TOOL_HANDLERS", {})
    except ImportError:
        dynamic_handlers = {}

    tool = block.tool_type
    content = block.content

    # The block/disable gates below must match every policy-equivalent
    # spelling of the tool name (bare email names alias their mcp__email__
    # form — see email_tool_policy_names), not just the spelling the model
    # happened to emit.
    policy_names = email_tool_policy_names(tool)

    # Misformatted tool call detection: model put JSON inside ```python``` (or
    # similar) without naming the tool. Common with MiniMax-style outputs.
    # Return a helpful error so the model retries with the correct format.
    if tool in ("python", "json", "xml") and content.strip().startswith("{") and content.strip().endswith("}"):
        try:
            parsed = json.loads(content.strip())
            if isinstance(parsed, dict):
                desc = f"{tool}: misformatted tool call"
                result = {
                    "error": (
                        f"You wrote a JSON object inside a ```{tool}``` block, but that's not a tool call.\n"
                        "To call a tool, use the tool name as the fence tag, e.g.\n"
                        "```resolve_contact\n"
                        "{\"name\": \"...\"}\n"
                        "```\n"
                        "or\n"
                        "```send_email\n"
                        "{\"to\": \"...\", \"subject\": \"...\", \"body\": \"...\"}\n"
                        "```"
                    ),
                    "exit_code": 1,
                }
                return desc, result
        except (ValueError, TypeError):
            pass

    # Reject tools that the user has disabled for this request
    from src.turn_contract import active_turn_contract
    contract = active_turn_contract()
    if contract is not None and not contract.permits(tool):
        return f"{tool}: BLOCKED", {
            "error": f"Tool '{tool}' is outside the requested turn capabilities.",
            "exit_code": 1,
            "failure_kind": "turn_contract_denied",
        }
    # A turn contract narrows the offered tool inventory; it is not an
    # authorization grant overriding explicit execution-time restrictions.
    if (
        disabled_tools
        and not policy_names.isdisjoint(disabled_tools)
    ):
        desc = f"{tool}: BLOCKED"
        result = {"error": f"Tool '{tool}' is disabled by user.", "exit_code": 1}
        logger.info(f"Tool blocked by user: {tool}")
        return desc, result

    if (
        tool_policy
        and any(tool_policy.blocks(name) for name in policy_names)
    ):
        desc = f"{tool}: BLOCKED"
        result = {
            "error": f"Execution of tool '{tool}' is forbade by the active guide-only policy.",
            "exit_code": 1,
        }
        logger.warning("Tool policy blocked tool=%s", tool)
        return desc, result

    if tool in _ADMIN_TOOLS and not _owner_is_admin(owner):
        desc = f"{tool}: BLOCKED"
        result = {"error": f"Tool '{tool}' requires an admin user.", "exit_code": 1}
        logger.warning("Admin tool blocked for non-admin owner=%r tool=%s", owner, tool)
        return desc, result

    execution_bridge = get_active_execution_bridge()
    backend = active_backend_operation()
    if backend is not None:
        backend.validate(client_runtime_context)
    owned = active_owned_operation()
    if owned is not None:
        owned.validate()
    bridge_owns_tool = (
        execution_bridge is not None
        and tool in execution_bridge.supported_tools
        and active_resource_operation() is None
        and (backend is None or backend.resource.namespace == "execution_bridge")
    )
    if backend is not None and backend.resource.namespace == "execution_bridge" and not bridge_owns_tool:
        raise ResourceIdentityError("Pinned external execution bridge is unavailable")

    # Public-owner restrictions protect tools executed by this deployment.
    # A request-scoped execution bridge is a separate, explicit authority for
    # its own allowlisted environment (for example, a disposable task
    # container). User-disabled, guide-only, and admin-tool gates above still
    # win; only the deployment-local public restriction is inapplicable.
    if (
        is_public_blocked_tool(tool)
        and not _owner_is_admin(owner)
        and not bridge_owns_tool
    ):
        desc = f"{tool}: BLOCKED"
        result = {
            "error": (
                f"Tool '{tool}' is restricted to admin users on this deployment. "
                "Ask an admin to perform this action or grant the needed permission."
            ),
            "exit_code": 1,
        }
        logger.warning("Public tool policy blocked owner=%r tool=%s", owner, tool)
        return desc, result

    # A request-scoped bridge owns where a task workspace lives, not the
    # semantic contract of write_file.  Keep text writes from corrupting a
    # rendered image/PDF even when the bridge handles the physical write.
    if tool == "write_file":
        rejected = _text_write_to_binary_artifact_result(content)
        if rejected is not None:
            return rejected

    mark_authorized()
    if bridge_owns_tool:
        if tool == "write_file":
            from src.agent_tools.filesystem_tools import _parse_write_intent, _unwrap_fenced_source_body
            try:
                path, body, section, explicit_clear = _parse_write_intent(content)
                if not path or path.endswith(("/", "\\")) or "\n" in path or "\r" in path:
                    raise ValueError("write_file: valid file path required")
                body = _unwrap_fenced_source_body(body, path)
                if not section or (not body.strip() and not explicit_clear):
                    raise ValueError("write_file: bridge cannot safely enforce implicit empty content; use explicit JSON string content")
            except ValueError as error:
                return "write_file: invalid arguments", {"error": str(error), "exit_code": 1}
        try:
            return await dispatched(execution_bridge.route_tool(
                tool,
                content,
                session_id,
                client_runtime_context,
            ))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Scoped execution bridge %s failed for tool=%s: %s: %s",
                execution_bridge.name,
                tool,
                type(exc).__name__,
                exc,
            )
            return (
                f"{tool}: external execution failed",
                {
                    "error": f"{tool}: external execution failed: {type(exc).__name__}: {exc}",
                    "exit_code": 1,
                    "execution_bridge": execution_bridge.name,
                },
            )

    if (active_resource_operation() is None and (backend is None or backend.resource.namespace == "client_bridge") and tool in _ROUTED_BRIDGE_TOOLS
            and _client_bridge(client_runtime_context) is not None):
        return await dispatched(_route_tool_via_bridge(tool, content, session_id, client_runtime_context))

    # Background execution: a `bash` block whose first line is the `#!bg`
    # marker runs DETACHED — returns a job id immediately so the chat stream
    # isn't held open for a multi-minute install/ffmpeg/download. The always-on
    # monitor re-invokes the agent with the full output when the job finishes.
    if tool == "bash" and session_id and (backend is None or isinstance(backend.resource, NativeBackendResource)):
        _is_bg, _bg_cmd = _split_bg_marker(content)
        if _is_bg and _bg_cmd:
            from src import bg_jobs
            mark_dispatch()
            from src import containment
            try:
                rec = bg_jobs.launch(_bg_cmd, session_id=session_id, cwd=agent_cwd(), env=_agent_subprocess_env())
            except containment.ContainmentUnavailable as exc:
                return "bash (background): containment unavailable", containment.unavailable_tool_result(exc, tool="bash")
            # Only this server launch may seal detached-job authority; a
            # handler/bridge output carrying a job id is not a grant source.
            short = _bg_cmd.strip().split(chr(10))[0][:80]
            desc = f"bash (background): {short}"
            result = {
                "output": (
                    f"Started background job `{rec['id']}`. It is running detached; "
                    f"do NOT wait for it or poll it. You will be automatically re-invoked "
                    f"with its captured output and any capture limit when it finishes. Continue with other work, or "
                    f"end your turn now and resume when the result arrives. If the user "
                    f"later asks to check progress or stop it, call the manage_bg_jobs "
                    f"tool yourself (output or kill); do not tell them to run a tool "
                    f"command, and do not surface raw tool syntax in your reply."
                ),
                "exit_code": 0,
                "bg_job_id": rec["id"],
                "containment": rec.get("containment"),
            }
            logger.info(f"Tool executed: {desc} -> bg job {rec['id']}")
            return desc, result

    # Route MCP-extracted tools through the MCP manager. Forward
    # the progress callback so long-running subprocess tools
    # (bash, python) can stream `tool_progress` events to the UI.
    if tool == "generate_image":
        from src.ai_interaction import do_generate_image
        desc = "generate_image"
        result = await dispatched(do_generate_image(content, session_id=session_id, owner=owner))
    elif (tool in NATIVE_FILESYSTEM_TOOLS
          and (active_resource_operation() is not None or tool != "apply_patch"
               or not _tui_host_bridge_patch_url(client_runtime_context))):
        if active_resource_operation() is None:
            return f"{tool}: BLOCKED", {
                "error": "Native filesystem dispatch has no bound resource operation",
                "exit_code": 1, "blocked": True, "failure_kind": "resource_identity_denied",
            }
        # Backend selection is pinned. MCP connection availability cannot
        # redirect an admitted native resource to a different filesystem.
        original = active_resource_operation().operation.input
        desc = f"{tool}: {original.split(chr(10))[0][:80]}"
        result = await dispatched(_direct_fallback(tool, content, owner=owner, session_id=session_id)) \
            or {"error": f"{tool}: execution failed", "exit_code": 1}
        if tool == "edit_file":
            desc = result.get("output") or result.get("error") or "edit_file"
    elif tool in {"bash", "python"} and backend is not None and isinstance(backend.resource, NativeBackendResource):
        # Native reservations are pinned to the native producer. Pass the
        # application binding explicitly rather than the MCP fallback's empty
        # owner/session context.
        first_line = content.split(chr(10))[0][:80]
        desc = f"{tool}: {first_line}"
        result = await dispatched(_direct_fallback(tool, content, progress_cb=progress_cb,
            owner=owner, session_id=session_id, client_runtime_context=client_runtime_context)) \
            or {"error": f"{tool}: execution failed", "exit_code": 1}
    elif tool in _MCP_TOOL_MAP:
        first_line = content.split(chr(10))[0][:80]
        desc = f"{tool}: {first_line}"
        result = await dispatched(_call_mcp_tool(tool, content, progress_cb=progress_cb))
    elif tool in ("grep", "glob", "ls", "get_workspace", "host_shell"):
        # Code-navigation tools — no MCP server; run the direct implementation.
        first_line = content.split(chr(10))[0][:80]
        desc = f"{tool}: {first_line}"
        result = await dispatched(_direct_fallback(
            tool,
            content,
            progress_cb=progress_cb,
            owner=owner,
            client_runtime_context=client_runtime_context,
        )) \
            or {"error": f"{tool}: execution failed", "exit_code": 1}
    elif tool == "apply_patch" and _tui_host_bridge_patch_url(client_runtime_context):
        first_line = content.split(chr(10))[0][:80]
        desc = f"{tool}: {first_line}" if first_line else tool
        result = await dispatched(_apply_patch_via_tui_host_bridge(content, client_runtime_context))
    elif tool in ("apply_patch", "todowrite"):
        first_line = content.split(chr(10))[0][:80]
        desc = f"{tool}: {first_line}" if first_line else tool
        result = await dispatched(_direct_fallback(tool, content, session_id=session_id, owner=owner)) \
            or {"error": f"{tool}: execution failed", "exit_code": 1}
    elif tool == "manage_bg_jobs":
        # Inspect/kill detached `bash` jobs; needs session_id to scope to chat.
        desc = f"manage_bg_jobs: {content.split(chr(10))[0][:80]}"
        result = await dispatched(_direct_fallback(tool, content, session_id=session_id, owner=owner)) \
            or {"error": "manage_bg_jobs: execution failed", "exit_code": 1}
    elif tool in ("create_document", "update_document", "edit_document",
                  "suggest_document", "manage_documents"):
        desc = f"{tool}: {content.split(chr(10))[0][:80]}"
        result = await dispatched(_document_tool_dispatch(
            tool,
            content,
            session_id,
            owner,
            document_id=approved_document_id or active_document_id,
            document_version=approved_document_version,
            document_digest=approved_document_digest,
        )) \
            or {"error": f"{tool}: execution failed", "exit_code": 1}
        if tool in ("edit_document", "suggest_document") and "title" in (result or {}):
            desc = f"{tool}: {result.get('title', '')}"
    elif tool == "search_chats":
        query = content.split("\n")[0].strip()
        desc = f"search_chats: {query[:80]}"
        result = await dispatched(do_search_chats(query, owner=owner))
    elif tool in ("chat_with_model", "ask_teacher", "list_models"):
        # Migrated to the agent_tools registry (#3629): dispatched through
        # TOOL_HANDLERS with the owner/session ctx these tools need, instead
        # of the legacy dispatch_ai_tool elif. The impls live in
        # src/agent_tools/model_interaction_tools.py.
        first_line = content.split(chr(10))[0].strip()[:60]
        desc = f"{tool}: {first_line}" if first_line else tool
        result = await dispatched(_document_tool_dispatch(tool, content, session_id, owner)) \
            or {"error": f"{tool}: execution failed", "exit_code": 1}
    elif tool in ("create_session", "list_sessions", "send_to_session", "manage_session"):
        # Migrated to the agent_tools registry (#3629): dispatched through
        # TOOL_HANDLERS with the owner/session ctx these tools need. The impls
        # live in src/agent_tools/session_tools.py.
        first_line = content.split(chr(10))[0].strip()[:60]
        desc = f"{tool}: {first_line}" if first_line else tool
        result = await dispatched(_document_tool_dispatch(tool, content, session_id, owner)) \
            or {"error": f"{tool}: execution failed", "exit_code": 1}
    elif tool in ("pipeline", "manage_memory", "ui_control"):
        from src.ai_interaction import dispatch_ai_tool
        desc, result = await dispatched(dispatch_ai_tool(tool, content, session_id, owner=owner))
    elif tool == "manage_tasks":
        desc = "manage_tasks"
        result = await dispatched(do_manage_tasks(content, owner=owner))
    elif tool == "manage_skills":
        desc = "manage_skills"
        result = await dispatched(do_manage_skills(content, owner=owner))
    elif tool == "api_call":
        first_line = content.split("\n")[0].strip()[:60]
        desc = f"api_call: {first_line}"
        result = await dispatched(do_api_call(content))
    elif tool in ("manage_endpoints", "manage_mcp", "manage_webhooks", "manage_tokens", "manage_settings"):
        # Registry-dispatched (agent_tools.admin_tools); owner threaded for ownership/admin checks.
        desc = tool
        result = await dispatched(_direct_fallback(tool, content, owner=owner)) \
            or {"error": f"{tool}: execution failed", "exit_code": 1}
    elif tool == "manage_notes":
        desc = "manage_notes"
        result = await dispatched(do_manage_notes(content, owner=owner))
    elif tool == "manage_calendar":
        desc = "manage_calendar"
        result = await dispatched(do_manage_calendar(content, owner=owner))
    elif tool == "download_model":
        desc = "download_model"
        result = await dispatched(do_download_model(content, owner=owner))
    elif tool == "serve_model":
        desc = "serve_model"
        result = await dispatched(do_serve_model(content, owner=owner))
    elif tool == "list_served_models":
        desc = "list_served_models"
        result = await dispatched(do_list_served_models(content, owner=owner))
    elif tool == "stop_served_model":
        desc = "stop_served_model"
        result = await dispatched(do_stop_served_model(content, owner=owner))
    elif tool == "tail_serve_output":
        desc = "tail_serve_output"
        result = await dispatched(do_tail_serve_output(content, owner=owner))
    elif tool == "list_downloads":
        desc = "list_downloads"
        result = await dispatched(do_list_downloads(content, owner=owner))
    elif tool == "cancel_download":
        desc = "cancel_download"
        result = await dispatched(do_cancel_download(content, owner=owner))
    elif tool == "search_hf_models":
        desc = "search_hf_models"
        result = await dispatched(do_search_hf_models(content, owner=owner))
    elif tool == "list_cached_models":
        desc = "list_cached_models"
        result = await dispatched(do_list_cached_models(content, owner=owner))
    elif tool == "app_api":
        desc = "app_api"
        result = await dispatched(do_app_api(content, owner=owner))
    elif tool == "list_serve_presets":
        desc = "list_serve_presets"
        result = await dispatched(do_list_serve_presets(content, owner=owner))
    elif tool == "serve_preset":
        desc = "serve_preset"
        result = await dispatched(do_serve_preset(content, owner=owner))
    elif tool == "adopt_served_model":
        desc = "adopt_served_model"
        result = await dispatched(do_adopt_served_model(content, owner=owner))
    elif tool == "list_cookbook_servers":
        desc = "list_cookbook_servers"
        result = await dispatched(do_list_cookbook_servers(content, owner=owner))
    elif tool == "edit_image":
        desc = "edit_image"
        result = await dispatched(do_edit_image(content, owner=owner))
    elif tool == "edit_file":
        result = await dispatched(_direct_fallback(tool, content)) or {"error": "edit failed", "exit_code": 1}
        desc = result.get("output") or result.get("error") or "edit_file"
    elif tool == "trigger_research":
        desc = "trigger_research"
        result = await dispatched(do_trigger_research(content, owner=owner, chat_session_id=session_id))
    elif tool == "manage_research":
        desc = "manage_research"
        result = await dispatched(do_manage_research(content, owner=owner))
    elif tool == "resolve_contact":
        desc = "resolve_contact"
        result = await dispatched(do_resolve_contact(content, owner=owner))
    elif tool == "manage_contact":
        desc = "manage_contact"
        result = await dispatched(do_manage_contact(content, owner=owner))
    elif tool == "vault_search":
        desc = "vault_search"
        result = await dispatched(do_vault_search(content, owner=owner))
    elif tool == "vault_get":
        desc = "vault_get"
        result = await dispatched(do_vault_get(content, owner=owner))
    elif tool == "vault_unlock":
        desc = "vault_unlock"
        result = await dispatched(do_vault_unlock(content, owner=owner))
    elif tool in BUILTIN_EMAIL_TOOLS:
        # Bare email tool name from fenced-block models (e.g. Ollama) — route to MCP email server.
        # Non-admin owners never reach here: BUILTIN_EMAIL_TOOLS ⊆ NON_ADMIN_BLOCKED_TOOLS,
        # so is_public_blocked_tool() above already rejected them.
        mcp = get_mcp_manager()
        qualified = f"mcp__email__{tool}"
        desc = f"email: {tool}"
        if mcp:
            _raw = content.strip()
            args = {}
            _args_error = None
            if _raw:
                # A non-empty body is always meant to be the call's arguments,
                # and every email tool takes a JSON object. Anything that
                # isn't one is a correctable error — NOT a silent empty-args
                # call, which would read the DEFAULT mailbox/folder instead of
                # the one the model meant (#3966 class). Only an EMPTY body
                # keeps the no-arg path (e.g. ```list_email_accounts```).
                try:
                    parsed = json.loads(_raw)
                except (json.JSONDecodeError, TypeError) as _je:
                    # Covers both `{account: "work"}` (looks like JSON, bad)
                    # and `account: work` (not JSON at all).
                    _args_error = (
                        f"'{tool}' arguments are not valid JSON ({_je}). "
                        'Send a JSON object, e.g. {"account": "work"} — '
                        "keys and string values need double quotes."
                    )
                else:
                    if isinstance(parsed, dict):
                        args = parsed
                    else:
                        _args_error = (
                            f"'{tool}' arguments must be a JSON object, "
                            'e.g. {"uid": "..."} — got a JSON array/value instead.'
                        )
            if _args_error is not None:
                result = {"error": _args_error, "exit_code": 1}
            else:
                if owner:
                    args = dict(args)
                    args[_EMAIL_MCP_OWNER_ARG] = owner
                if session_id:
                    args = dict(args)
                    args[_EMAIL_MCP_SESSION_ARG] = session_id
                result = await dispatched(mcp.call_tool(qualified, args))
        else:
            result = {"error": "MCP manager not available", "exit_code": 1}
    elif tool.startswith("mcp__"):
        # MCP tool dispatch
        mcp = get_mcp_manager()
        if mcp:
            desc = f"mcp: {tool}"
            args, parse_error = _parse_qualified_mcp_args(tool, content)
            if parse_error:
                result = {"error": parse_error, "exit_code": 1}
            else:
                if tool.startswith("mcp__email__"):
                    if owner:
                        args = dict(args)
                        args[_EMAIL_MCP_OWNER_ARG] = owner
                    if session_id:
                        args = dict(args)
                        args[_EMAIL_MCP_SESSION_ARG] = session_id
                result = _normalize_mcp_text_error(await dispatched(mcp.call_tool(tool, args)))
        else:
            desc = f"mcp: {tool}"
            result = {"error": "MCP manager not available", "exit_code": 1}


    elif tool in dynamic_handlers:
        first_line = content.split(chr(10))[0][:80]
        desc = f"registry: {tool} {first_line}".strip()
        res = await dispatched(_direct_fallback(
            tool,
            content,
            progress_cb=progress_cb,
            session_id=session_id,
            owner=owner,
            client_runtime_context=client_runtime_context,
            disabled_tools=disabled_tools,
            tool_policy=tool_policy,
        ))

        if isinstance(res, tuple):
            desc, result = res
        else:
            result = res or {"error": f"{tool}: execution failed", "exit_code": 1}

    else:
        desc = f"unknown: {tool}"
        result = {
            "error": f"Unknown tool: {tool}",
            "exit_code": 1
        }

    logger.info(f"Tool executed: {desc} -> exit_code={result.get('exit_code', 'n/a')}")
    return desc, result


# ---------------------------------------------------------------------------
# Result formatting
# ---------------------------------------------------------------------------

# Keys handled by the dedicated branches below — never echo them as raw JSON.
_FORMATTER_HANDLED_KEYS = {
    "stdout", "stderr", "exit_code", "content", "size",
    "response", "results", "session_id", "name", "model", "session_name",
    "success", "path", "action", "title", "doc_id", "version", "applied",
    "error", "output", "images",
}


def _compact_binary_like_output(value: object) -> str:
    """Replace decoded binary dumps with bounded extraction guidance."""

    text = str(value or "")
    if len(text) < 128:
        return text
    suspicious = text.count("\ufffd") + sum(
        1
        for char in text
        if ord(char) < 32 and char not in "\n\r\t"
    )
    if suspicious / len(text) < 0.08:
        return text
    return (
        f"[Binary-like output omitted: {len(text)} decoded characters, "
        f"{suspicious} replacement/control characters. Use `file`, `strings`, "
        "or a format-specific extractor instead of printing the binary file.]"
    )


def format_tool_result(description: str, result: Dict) -> str:
    """Format a tool result into text for feeding back to the LLM."""
    parts = [f"### {description}"]

    if "stdout" in result:
        if result["stdout"]:
            parts.append(
                f"**stdout:**\n```\n{_compact_binary_like_output(result['stdout'])}\n```"
            )
        if result["stderr"]:
            parts.append(
                f"**stderr:**\n```\n{_compact_binary_like_output(result['stderr'])}\n```"
            )
        parts.append(f"**exit_code:** {result.get('exit_code', 'unknown')}")
    elif "output" in result:
        # bash / python canonical result shape: {"output": ..., "exit_code": ...}
        parts.append(f"```\n{_compact_binary_like_output(result['output'])}\n```")
        if result.get("exit_code") not in (0, None):
            parts.append(f"**exit_code:** {result['exit_code']}")
    elif "content" in result:
        parts.append(
            f"**content ({result.get('size', '?')} chars):**\n```\n"
            f"{_compact_binary_like_output(result['content'])}\n```"
        )
    elif "response" in result:
        model = result.get("model", result.get("session_name", ""))
        if model:
            parts.append(f"**{model} responded:**\n{result['response']}")
        else:
            parts.append(result["response"])
    elif "results" in result:
        parts.append(result["results"])
    elif "session_id" in result and "name" in result:
        parts.append(f"Session created: **{result['name']}** (id: `{result['session_id']}`, model: {result.get('model', 'unknown')})")
    elif "success" in result:
        if result["success"]:
            parts.append(f"File written: {result['path']} ({result['size']} bytes)")
        else:
            parts.append(f"Error: {result.get('error', 'unknown')}")
    elif "action" in result:
        action = result["action"]
        if action == "create":
            parts.append(f"Document created: \"{result.get('title', '')}\" (id: {result['doc_id']}, v{result['version']})")
        elif action == "update":
            parts.append(f"Document updated: \"{result.get('title', '')}\" (v{result['version']})")
        elif action == "edit":
            parts.append(f'Document edited: "{result.get("title", "")}" (v{result.get("version", "?")}, {result.get("applied", 0)} edit(s) applied)')
    elif "error" in result:
        parts.append(f"**Error:** {result['error']}")

    if result.get("detached") or (
        result.get("status") == "running" and result.get("job_id")
    ):
        parts.append(
            "**POLL REQUIRED:** this host job is only started, not finished. "
            "The next tool call must be `host_shell` with JSON "
            f"`{{\"job_id\":\"{result.get('job_id', '')}\"}}`; "
            "do not run a substitute command or report completion until the "
            "job result says `status=completed`."
        )

    # Surface any additional structured payload (events, tasks, notes, calendars,
    # documents, attachments, etc.) that the dedicated branches above don't show.
    # Without this, tools that return {"response": "...", "events": [...]} would
    # silently drop the events list and the model would only see the summary line.
    extra = {k: v for k, v in result.items() if k not in _FORMATTER_HANDLED_KEYS}
    if extra:
        try:
            extra_json = json.dumps(extra, indent=2, default=str, ensure_ascii=False)
            # Cap to avoid blowing the context window on huge payloads.
            if len(extra_json) > 8000:
                extra_json = extra_json[:8000] + f"\n... (truncated, {len(extra_json)} chars total)"
            parts.append(f"**data:**\n```json\n{extra_json}\n```")
        except (TypeError, ValueError):
            pass

    # External execution bridges are allowed to return canonical result shapes
    # without using the built-in tools' output cap. Bound the final model-facing
    # text here so one verbose command cannot consume the next request window.
    return _truncate("\n".join(parts))
