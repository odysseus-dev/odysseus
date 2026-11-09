from __future__ import annotations

import ast
import asyncio
import base64
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from unittest.mock import AsyncMock

from routes.chat_routes import _agent_turn_cwd
import routes.chat_routes as chat_routes
from src import tool_execution as _te
from src.agent_loop import _is_explicit_local_network_request
from tests.runtime_evidence_helpers import server_authorized_executor

# Hold module-object references (not just from-imported names): other test
# modules re-import src.tool_execution via sys.modules pops, so string-target
# patches can land on a DIFFERENT fresh module object than the one these
# tests execute from. patch.object on the held reference always matches.
execute_tool_block = _te.execute_tool_block
_bridge_post = _te._bridge_post
_client_bridge = _te._client_bridge
_ROUTED_BRIDGE_TOOLS = _te._ROUTED_BRIDGE_TOOLS


async def _execute_tool_block_for_unit_tests(*args, **kwargs):
    """Use the explicit non-security-context test mode for dispatch tests."""
    kwargs.setdefault("security_context", _te.NO_TOOL_SECURITY_CONTEXT)
    return await server_authorized_executor(_te.execute_tool_block)(*args, **kwargs)


execute_tool_block = _execute_tool_block_for_unit_tests

ROOT = Path(__file__).resolve().parent.parent

BRIDGE_CTX = {
    "surface": "odysseus-tui",
    "session_cwd": "/home/pewds/project",
    "host_shell_bridge": {"url": "http://127.0.0.1:47475/run", "token": "secret"},
}


def test_client_bridge_extracts_valid_bridge_only():
    assert _client_bridge(BRIDGE_CTX)["token"] == "secret"
    assert _client_bridge({"host_shell_bridge": BRIDGE_CTX["host_shell_bridge"]}) is None
    assert _client_bridge({
        "surface": "odysseus-tui",
        "host_shell_bridge": {"url": "http://8.8.8.8:47475/run", "token": "x"},
    }) is None
    assert _client_bridge({"host_shell_bridge": {"url": "", "token": "x"}}) is None
    assert _client_bridge({"host_shell_bridge": {"url": "http://x/run"}}) is None
    assert _client_bridge(None) is None
    assert _client_bridge({"host_shell_bridge": "nope"}) is None


def test_local_network_requests_are_distinct_from_web_search():
    assert _is_explicit_local_network_request(
        "Show the local IPv4 interfaces and default route"
    )
    assert _is_explicit_local_network_request(
        "Find the LAN IP for ajax so I can SSH to it"
    )
    assert not _is_explicit_local_network_request(
        "Search the web for the latest local network troubleshooting guide"
    )


def _run(coro):
    return asyncio.run(coro)


class _admin_gate:
    """Bypass the admin/single-user gates in tool dispatch for tests.

    Production TUI turns carry the owner (admin on this deployment), so the
    public-tool gate passes there; unit tests have no auth manager.
    """

    def __enter__(self):
        self._patch = patch.object(_te, "_owner_is_admin", lambda owner: True)
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()
        return False


def test_routed_bash_sends_command_to_run_endpoint():
    calls = []

    async def fake_bridge_post(bridge, path, payload, **kwargs):
        calls.append((path, payload, kwargs))
        return {"stdout": "host-side", "stderr": "", "exit_code": 0, "cwd": "/home/pewds/project"}

    with _admin_gate(), patch.object(_te, "_bridge_post", fake_bridge_post):
        desc, result = _run(
            execute_tool_block(
                SimpleNamespace(tool_type="bash", content="pwd && ls"),
                client_runtime_context=dict(BRIDGE_CTX),
            )
        )

    path, payload, kwargs = calls[0]
    assert path == "/run"
    assert payload["command"] == "pwd && ls"
    assert isinstance(payload["request_id"], str) and payload["request_id"]
    assert kwargs["err_prefix"] == "bash"
    assert result["stdout"] == "host-side"
    assert desc.startswith("bash:")


def test_routed_python_uses_exec_argv_not_shell():
    calls = []

    async def fake_bridge_post(bridge, path, payload, **kwargs):
        calls.append((path, payload))
        return {"stdout": "", "stderr": "", "exit_code": 0}

    with _admin_gate(), patch.object(_te, "_bridge_post", fake_bridge_post):
        _run(
            execute_tool_block(
                SimpleNamespace(tool_type="python", content='print("hi")'),
                client_runtime_context=dict(BRIDGE_CTX),
            )
        )

    path, payload = calls[0]
    assert path == "/run"
    assert payload["exec"] == ["python3", "-I", "-c", 'print("hi")']
    assert isinstance(payload["request_id"], str) and payload["request_id"]
    assert "command" not in payload


def test_routed_read_file_uses_read_endpoint():
    calls = []

    async def fake_bridge_post(bridge, path, payload, **kwargs):
        calls.append((path, payload))
        return {"output": "file body", "exit_code": 0}

    with _admin_gate(), patch.object(_te, "_bridge_post", fake_bridge_post):
        desc, result = _run(
            execute_tool_block(
                SimpleNamespace(tool_type="read_file", content="/home/pewds/project/main.py"),
                client_runtime_context=dict(BRIDGE_CTX),
            )
        )

    path, payload = calls[0]
    assert path == "/read"
    assert payload == {"path": "/home/pewds/project/main.py"}
    assert result["output"] == "file body"
    assert "read_file" in desc


def test_routed_read_file_preserves_json_line_range():
    calls = []

    async def fake_bridge_post(bridge, path, payload, **kwargs):
        calls.append((path, payload))
        return {"output": "selected lines", "exit_code": 0}

    with _admin_gate(), patch.object(_te, "_bridge_post", fake_bridge_post):
        desc, result = _run(
            execute_tool_block(
                SimpleNamespace(
                    tool_type="read_file",
                    content='{"path":"odysseus_tui/client.py","offset":55,"limit":40}',
                ),
                client_runtime_context=dict(BRIDGE_CTX),
            )
        )

    assert calls == [
        (
            "/read",
            {"path": "odysseus_tui/client.py", "offset": 55, "limit": 40},
        )
    ]
    assert desc == "read_file: odysseus_tui/client.py"
    assert result["output"] == "selected lines"


def test_routed_read_file_rejects_malformed_json_without_host_call():
    calls = []

    async def fake_bridge_post(*args, **kwargs):
        calls.append((args, kwargs))
        return {"output": "unexpected", "exit_code": 0}

    with _admin_gate(), patch.object(_te, "_bridge_post", fake_bridge_post):
        desc, result = _run(
            execute_tool_block(
                SimpleNamespace(tool_type="read_file", content='{"path":"broken"'),
                client_runtime_context=dict(BRIDGE_CTX),
            )
        )

    assert desc == "read_file: invalid arguments"
    assert result["exit_code"] == 1
    assert calls == []


def test_routed_read_file_rejects_empty_path_without_host_call():
    calls = []

    async def fake_bridge_post(*args, **kwargs):
        calls.append((args, kwargs))
        return {"output": "unexpected", "exit_code": 0}

    with _admin_gate(), patch.object(_te, "_bridge_post", fake_bridge_post):
        desc, result = _run(
            execute_tool_block(
                SimpleNamespace(tool_type="read_file", content=""),
                client_runtime_context=dict(BRIDGE_CTX),
            )
        )

    assert desc == "read_file: invalid arguments"
    assert result["exit_code"] == 1
    assert calls == []


def test_routed_read_file_rejects_invalid_line_range_without_host_call():
    calls = []

    async def fake_bridge_post(*args, **kwargs):
        calls.append((args, kwargs))
        return {"output": "unexpected", "exit_code": 0}

    with _admin_gate(), patch.object(_te, "_bridge_post", fake_bridge_post):
        for offset, limit in ((-1, 1), (0, -1), ("1", 2), (True, 2)):
            desc, result = _run(
                execute_tool_block(
                    SimpleNamespace(
                        tool_type="read_file",
                        content=json.dumps({"path": "main.py", "offset": offset, "limit": limit}),
                    ),
                    client_runtime_context=dict(BRIDGE_CTX),
                )
            )
            assert desc == "read_file: invalid arguments"
            assert result["exit_code"] == 1

    assert calls == []


def test_routed_read_file_rejects_non_string_or_multiline_path():
    calls = []

    async def fake_bridge_post(*args, **kwargs):
        calls.append((args, kwargs))
        return {"output": "unexpected", "exit_code": 0}

    with _admin_gate(), patch.object(_te, "_bridge_post", fake_bridge_post):
        for path in (123, ["main.py"], "main.py\nother.py"):
            desc, result = _run(execute_tool_block(
                SimpleNamespace(
                    tool_type="read_file",
                    content=json.dumps({"path": path}),
                ),
                client_runtime_context=dict(BRIDGE_CTX),
            ))
            assert desc == "read_file: invalid arguments"
            assert result["exit_code"] == 1

    assert calls == []


def test_routed_write_file_sends_base64_body(tmp_path):
    calls = []

    async def fake_bridge_post(bridge, path, payload, **kwargs):
        calls.append((path, payload))
        return {"output": "wrote", "exit_code": 0}

    target = tmp_path / "out.txt"
    body = "line one\nline two with 'quotes' and $vars\n"
    with _admin_gate(), patch.object(_te, "_bridge_post", fake_bridge_post):
        _run(
            execute_tool_block(
                SimpleNamespace(tool_type="write_file", content=f"{target}\n{body}"),
                client_runtime_context=dict(BRIDGE_CTX),
            )
        )

    path, payload = calls[0]
    assert path == "/write"
    assert payload["path"] == str(target)
    assert base64.b64decode(payload["content_b64"]).decode("utf-8") == body
    assert re.fullmatch(r"[A-Za-z0-9_-]+", payload["request_id"])


def test_routed_write_file_decodes_native_json_arguments():
    calls = []

    async def fake_bridge_post(bridge, path, payload, **kwargs):
        calls.append((path, payload))
        return {"output": "wrote", "exit_code": 0}

    body = "print('héllo')\n"
    with _admin_gate(), patch.object(_te, "_bridge_post", fake_bridge_post):
        desc, result = _run(
            execute_tool_block(
                SimpleNamespace(
                    tool_type="write_file",
                    content=json.dumps({"path": "src/demo.py", "content": body}),
                ),
                client_runtime_context=dict(BRIDGE_CTX),
            )
        )

    assert desc == "write_file: src/demo.py"
    assert result["exit_code"] == 0
    assert calls[0][0] == "/write"
    assert calls[0][1]["path"] == "src/demo.py"
    assert base64.b64decode(calls[0][1]["content_b64"]).decode("utf-8") == body


def test_routed_write_file_rejects_malformed_native_json_without_host_call():
    calls = []

    async def fake_bridge_post(*args, **kwargs):
        calls.append((args, kwargs))
        return {"output": "unexpected", "exit_code": 0}

    with _admin_gate(), patch.object(_te, "_bridge_post", fake_bridge_post):
        desc, result = _run(
            execute_tool_block(
                SimpleNamespace(tool_type="write_file", content='{"path":"broken"'),
                client_runtime_context=dict(BRIDGE_CTX),
            )
        )

    assert desc == "write_file: invalid arguments"
    assert result["exit_code"] == 1
    assert calls == []


def test_routed_write_file_rejects_non_text_content_without_host_call():
    calls = []

    async def fake_bridge_post(*args, **kwargs):
        calls.append((args, kwargs))
        return {"output": "unexpected", "exit_code": 0}

    with _admin_gate(), patch.object(_te, "_bridge_post", fake_bridge_post):
        desc, result = _run(
            execute_tool_block(
                SimpleNamespace(
                    tool_type="write_file",
                    content=json.dumps({"path": "data.json", "content": {"value": 1}}),
                ),
                client_runtime_context=dict(BRIDGE_CTX),
            )
        )

    assert desc == "write_file: invalid arguments"
    assert result == {"error": "write_file: content must be a string", "exit_code": 1}
    assert calls == []


def test_routed_file_mutations_reject_non_string_or_multiline_path():
    calls = []

    async def fake_bridge_post(*args, **kwargs):
        calls.append((args, kwargs))
        return {"output": "unexpected", "exit_code": 0}

    cases = (
        ("write_file", {"path": 123, "content": "body"}),
        ("write_file", {"path": "a.txt\nb.txt", "content": "body"}),
        ("edit_file", {"path": ["a.txt"], "old_string": "a", "new_string": "b"}),
        ("edit_file", {"path": "a.txt\nb.txt", "old_string": "a", "new_string": "b"}),
    )
    with _admin_gate(), patch.object(_te, "_bridge_post", fake_bridge_post):
        for tool, args in cases:
            desc, result = _run(execute_tool_block(
                SimpleNamespace(tool_type=tool, content=json.dumps(args)),
                client_runtime_context=dict(BRIDGE_CTX),
            ))
            assert desc == f"{tool}: invalid arguments"
            assert result["exit_code"] == 1

    assert calls == []


def test_routed_bash_background_marker_detaches_on_client():
    calls = []

    async def fake_bridge_post(bridge, path, payload, **kwargs):
        calls.append((path, payload))
        return {"output": "detached", "exit_code": 0, "detached": True}

    with _admin_gate(), patch.object(_te, "_bridge_post", fake_bridge_post):
        desc, result = _run(
            execute_tool_block(
                SimpleNamespace(tool_type="bash", content="#!bg\nsleep 300"),
                session_id="sid",
                client_runtime_context=dict(BRIDGE_CTX),
            )
        )

    path, payload = calls[0]
    assert path == "/run"
    assert payload["detach"] is True
    assert payload["command"] == "sleep 300"
    assert result["output"] == "detached"


def test_routed_implicit_long_bash_is_detached_and_polled():
    calls = []

    async def fake_bridge_post(bridge, path, payload, **kwargs):
        calls.append(payload)
        if len(calls) == 1:
            return {"status": "running", "job_id": "job-implicit", "output": "started"}
        return {"status": "done", "job_id": "job-implicit", "output": "finished", "exit_code": 0}

    with _admin_gate(), patch.object(_te, "_bridge_post", fake_bridge_post):
        _, result = _run(
            execute_tool_block(
                SimpleNamespace(tool_type="bash", content="sleep 22; printf done"),
                client_runtime_context=dict(BRIDGE_CTX),
            )
        )

    assert calls[0]["detach"] is True
    assert calls[0]["command"] == "sleep 22; printf done"
    assert calls[1] == {"job_id": "job-implicit"}
    assert result["output"] == "finished"


def test_no_bridge_falls_back_to_backend_execution():
    async def fake_mcp(tool, content, progress_cb=None, **kwargs):
        return {"output": f"backend-side {tool}", "exit_code": 0}

    with patch.object(_te, "_owner_is_admin", lambda owner: True), \
            patch.object(_te, "_direct_fallback", fake_mcp):
        desc, result = _run(
            execute_tool_block(
                SimpleNamespace(tool_type="bash", content="pwd"),
                client_runtime_context={"surface": "webui"},
            )
        )

    assert result["output"] == "backend-side bash"


def test_disabled_tool_still_blocked_with_bridge_present():
    with _admin_gate():
        desc, result = _run(
            execute_tool_block(
                SimpleNamespace(tool_type="bash", content="pwd"),
                disabled_tools={"bash"},
                client_runtime_context=dict(BRIDGE_CTX),
            )
        )

    assert "BLOCKED" in desc
    assert result["exit_code"] == 1


def test_routed_tools_set_matches_computer_tools():
    assert _ROUTED_BRIDGE_TOOLS == {
        "bash", "python", "grep", "ls", "glob", "list_dir", "find_files",
        "read_file", "write_file", "edit_file",
    }


def test_routed_list_dir_preserves_range_and_uses_list_endpoint():
    calls = []

    async def fake_bridge_post(bridge, path, payload, **kwargs):
        calls.append((path, payload, kwargs))
        return {"output": "src\ntests", "exit_code": 0}

    with _admin_gate(), patch.object(_te, "_bridge_post", fake_bridge_post):
        desc, result = _run(execute_tool_block(
            SimpleNamespace(
                tool_type="list_dir",
                content='{"path":"src","offset":2,"limit":10}',
            ),
            client_runtime_context=dict(BRIDGE_CTX),
        ))

    assert desc == "list_dir: src"
    assert calls[0][0] == "/list"
    assert calls[0][1] == {
        "path": "src", "offset": 2, "limit": 10, "recursive": True,
    }
    assert result["exit_code"] == 0


def test_routed_find_files_uses_find_endpoint():
    calls = []

    async def fake_bridge_post(bridge, path, payload, **kwargs):
        calls.append((path, payload, kwargs))
        return {"output": "src/main.py", "exit_code": 0}

    with _admin_gate(), patch.object(_te, "_bridge_post", fake_bridge_post):
        desc, result = _run(execute_tool_block(
            SimpleNamespace(
                tool_type="find_files",
                content='{"pattern":"\\\\.py$","path":"src"}',
            ),
            client_runtime_context=dict(BRIDGE_CTX),
        ))

    assert desc == "find_files: \\.py$"
    assert calls[0][0] == "/find"
    assert calls[0][1] == {"pattern": "\\.py$", "path": "src"}
    assert result["exit_code"] == 0


def test_canonical_ls_and_glob_route_to_tui_discovery_endpoints():
    calls = []

    async def fake_bridge_post(bridge, path, payload, **kwargs):
        calls.append((path, payload))
        return {"output": "ok", "exit_code": 0}

    with _admin_gate(), patch.object(_te, "_bridge_post", fake_bridge_post):
        ls_desc, _ = _run(execute_tool_block(
            SimpleNamespace(tool_type="ls", content='{"path":"src"}'),
            client_runtime_context=dict(BRIDGE_CTX),
        ))
        glob_desc, _ = _run(execute_tool_block(
            SimpleNamespace(tool_type="glob", content='{"pattern":"**/*.py","path":"src"}'),
            client_runtime_context=dict(BRIDGE_CTX),
        ))

    assert ls_desc == "ls: src"
    assert calls[0] == (
        "/list",
        {"path": "src", "offset": 0, "limit": 0, "recursive": False},
    )
    assert glob_desc == "glob: **/*.py"
    assert calls[1][0] == "/find"
    assert calls[1][1] == {"path": "src", "glob": "**/*.py"}


def test_canonical_grep_routes_structured_search_to_tui_host():
    calls = []

    async def fake_bridge_post(bridge, path, payload, **kwargs):
        calls.append((path, payload))
        return {"output": "src/main.py:1:needle", "exit_code": 0}

    with _admin_gate(), patch.object(_te, "_bridge_post", fake_bridge_post):
        desc, result = _run(execute_tool_block(
            SimpleNamespace(
                tool_type="grep",
                content=(
                    '{"pattern":"needle","path":"src","glob":"**/*.py",'
                    '"ignore_case":true,"max_results":25}'
                ),
            ),
            client_runtime_context=dict(BRIDGE_CTX),
        ))

    assert desc == "grep: needle"
    assert calls == [("/grep", {
        "pattern": "needle", "path": "src", "glob": "**/*.py",
        "ignore_case": True, "max_results": 25,
    })]
    assert result["exit_code"] == 0


def test_routed_discovery_tools_reject_malformed_arguments_without_host_call():
    calls = []

    async def fake_bridge_post(*args, **kwargs):
        calls.append((args, kwargs))
        return {"output": "unexpected", "exit_code": 0}

    cases = (
        ("list_dir", {"path": 123}),
        ("list_dir", {"path": ".", "offset": "2"}),
        ("find_files", {"pattern": ""}),
        ("find_files", {"pattern": ["py"], "path": "."}),
        ("grep", {"pattern": "needle", "ignore_case": "yes"}),
        ("grep", {"pattern": "needle", "max_results": 0}),
        ("grep", {"pattern": ["needle"]}),
    )
    with _admin_gate(), patch.object(_te, "_bridge_post", fake_bridge_post):
        for tool, args in cases:
            desc, result = _run(execute_tool_block(
                SimpleNamespace(tool_type=tool, content=json.dumps(args)),
                client_runtime_context=dict(BRIDGE_CTX),
            ))
            assert desc == f"{tool}: invalid arguments"
            assert result["exit_code"] == 1

    assert calls == []


def test_agent_turn_cwd_prefers_raw_host_cwd_when_bridge_advertised():
    sess = SimpleNamespace(cwd="/workspace/project")  # translated container path
    assert _agent_turn_cwd(sess, dict(BRIDGE_CTX)) == "/home/pewds/project"


def test_agent_turn_cwd_keeps_session_cwd_without_bridge():
    sess = SimpleNamespace(cwd="/workspace/project")
    ctx = {"surface": "odysseus-tui", "session_cwd": "/home/pewds/project"}
    assert _agent_turn_cwd(sess, ctx) == "/workspace/project"
    assert _agent_turn_cwd(sess, {"surface": "webui"}) == "/workspace/project"


def test_bridge_post_targets_endpoint_paths():
    class FakeResp:
        status_code = 200

        def json(self):
            return {"stdout": "ok", "exit_code": 0}

        text = ""

    posted = {}

    class FakeClient:
        def __init__(self, timeout=None, *, trust_env=True):
            assert trust_env is False
            posted["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, headers=None, json=None):
            posted["url"] = url
            posted["headers"] = headers
            posted["json"] = json
            return FakeResp()

    import httpx

    with patch.object(httpx, "AsyncClient", FakeClient):
        result = _run(
            _bridge_post(
                {"url": "http://127.0.0.1:47475/run", "token": "secret"},
                "/write",
                {"path": "/tmp/x", "content_b64": "aGk="},
                timeout_s=30.0,
                err_prefix="write_file",
            )
        )

    assert posted["url"] == "http://127.0.0.1:47475/write"
    assert posted["headers"] == {"x-odysseus-tui-bridge-token": "secret"}
    assert result["exit_code"] == 0
    assert posted["timeout"].connect == 5.0
    assert posted["timeout"].write == 10.0
    assert posted["timeout"].read == 35.0


def test_bridge_post_rejects_untrusted_url_without_network_call():
    class UnexpectedClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("invalid bridge URL must not reach the network")

    import httpx

    with patch.object(httpx, "AsyncClient", UnexpectedClient):
        result = _run(
            _bridge_post(
                {"url": "http://8.8.8.8:47475/run", "token": "secret"},
                "/run",
                {"command": "true"},
                timeout_s=30.0,
                err_prefix="bash",
            )
        )

    assert result == {"error": "bash: invalid TUI host bridge", "exit_code": 1}


def test_bridge_post_treats_empty_or_error_payload_as_failure():
    class FakeResp:
        status_code = 200
        text = ""

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    payloads = iter(({}, {"error": "token rejected"}))

    class FakeClient:
        def __init__(self, timeout=None, *, trust_env=True):
            assert trust_env is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *args, **kwargs):
            return FakeResp(next(payloads))

    import httpx

    with patch.object(httpx, "AsyncClient", FakeClient):
        empty = _run(_bridge_post(BRIDGE_CTX["host_shell_bridge"], "/run", {}, timeout_s=1, err_prefix="bash"))
        errored = _run(_bridge_post(BRIDGE_CTX["host_shell_bridge"], "/run", {}, timeout_s=1, err_prefix="bash"))

    assert empty == {"error": "bash: bridge returned an empty response", "exit_code": 1}
    assert errored == {"error": "token rejected", "exit_code": 1}


def test_bridge_post_http_or_payload_error_overrides_false_success_code():
    class FakeResp:
        text = ""

        def __init__(self, status_code, payload):
            self.status_code = status_code
            self.payload = payload

        def json(self):
            return self.payload

    responses = iter((
        FakeResp(401, {"error": "token rejected", "exit_code": 0}),
        FakeResp(200, {"error": "host failed", "exit_code": 0}),
    ))

    class FakeClient:
        def __init__(self, timeout=None, *, trust_env=True):
            assert trust_env is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *args, **kwargs):
            return next(responses)

    import httpx

    with patch.object(httpx, "AsyncClient", FakeClient):
        unauthorized = _run(_bridge_post(
            BRIDGE_CTX["host_shell_bridge"], "/run", {}, timeout_s=1, err_prefix="bash"
        ))
        host_error = _run(_bridge_post(
            BRIDGE_CTX["host_shell_bridge"], "/run", {}, timeout_s=1, err_prefix="bash"
        ))

    assert unauthorized["exit_code"] == 1
    assert host_error["exit_code"] == 1


def test_bridge_post_rejects_malformed_exit_code():
    class FakeResp:
        status_code = 200
        text = ""

        def json(self):
            return {"output": "looks successful", "exit_code": "zero"}

    class FakeClient:
        def __init__(self, timeout=None, *, trust_env=True):
            assert trust_env is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *args, **kwargs):
            return FakeResp()

    import httpx

    with patch.object(httpx, "AsyncClient", FakeClient):
        result = _run(_bridge_post(
            BRIDGE_CTX["host_shell_bridge"], "/run", {}, timeout_s=1,
            err_prefix="bash",
        ))

    assert result == {
        "error": "bash: bridge returned an invalid exit_code",
        "exit_code": 1,
    }


def test_bridge_post_reports_empty_invalid_json_response():
    class FakeResp:
        status_code = 200
        text = ""

        def json(self):
            raise ValueError("not JSON")

    class FakeClient:
        def __init__(self, timeout=None, *, trust_env=True):
            assert trust_env is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *args, **kwargs):
            return FakeResp()

    import httpx

    with patch.object(httpx, "AsyncClient", FakeClient):
        result = _run(_bridge_post(
            BRIDGE_CTX["host_shell_bridge"], "/run", {}, timeout_s=1,
            err_prefix="bash",
        ))

    assert result == {
        "error": "bash: bridge returned invalid JSON",
        "exit_code": 1,
    }


def test_bridge_post_cancellation_schedules_host_operation_cancel():

    class FakeClient:
        def __init__(self, timeout=None, *, trust_env=True):
            assert trust_env is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *args, **kwargs):
            self.post_entered.set()
            await asyncio.Event().wait()

    async def exercise(path):
        import httpx

        entered = asyncio.Event()
        FakeClient.post_entered = entered
        cancel_request = AsyncMock()
        with patch.object(httpx, "AsyncClient", FakeClient), patch.object(
            _te, "_cancel_bridge_request", cancel_request
        ):
            task = asyncio.create_task(_bridge_post(
                BRIDGE_CTX["host_shell_bridge"],
                path,
                {"command": "sleep 900", "request_id": "request-123"},
                timeout_s=900,
                err_prefix="bash",
            ))
            await entered.wait()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await asyncio.sleep(0)
            cancel_request.assert_awaited_once_with(
                BRIDGE_CTX["host_shell_bridge"], "request-123"
            )

    for path in ("/run", "/write", "/edit"):
        _run(exercise(path))


# --------------------------------------------------------------------- #
# chat_routes wiring — source-inspection style (matches dev-tree tests)
# --------------------------------------------------------------------- #


def test_chat_stream_parses_and_injects_tui_runtime_context():
    source = (ROOT / "routes" / "chat_routes.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    chat_stream = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "chat_stream"
    )
    chat_stream_source = ast.get_source_segment(source, chat_stream)

    assert "_parse_client_runtime_context(" in chat_stream_source
    assert 'form_data.get("client_runtime_context")' in chat_stream_source
    assert "_client_runtime_context_system_message(" in chat_stream_source
    assert "disabled_tools=disabled_tools" in chat_stream_source
    assert "ctx.messages.insert(0, runtime_msg)" in chat_stream_source
    assert "cwd=_agent_turn_cwd(sess, client_runtime_context)" in chat_stream_source
    assert "client_runtime_context=client_runtime_context" in chat_stream_source
    assert "_stream_agent_with_execution_bridge(" in chat_stream_source
    assert "_external_execution_bridge(client_runtime_context)" in chat_stream_source


def test_native_runtime_context_accepts_only_loopback_execution_bridge():
    base = {
        "surface": "odysseus-native",
        "terminal_agent": True,
        "interaction_mode": "cook",
        "unattended_mode": True,
    }
    valid = chat_routes._parse_client_runtime_context({
        **base,
        "external_execution_bridge": {
            "url": "http://127.0.0.1:47123/execute",
            "token": "a-secure-request-token",
            "supported_tools": ["send_email", "send_email", "bad tool"],
        },
    })
    assert valid["external_execution_bridge"] == {
        "url": "http://127.0.0.1:47123/execute",
        "token": "a-secure-request-token",
        "supported_tools": ["send_email"],
    }

    for url in (
        "https://127.0.0.1:47123/execute",
        "http://example.com:47123/execute",
        "http://127.0.0.1:47123/",
    ):
        parsed = chat_routes._parse_client_runtime_context({
            **base,
            "external_execution_bridge": {
                "url": url,
                "token": "a-secure-request-token",
                "supported_tools": ["send_email"],
            },
        })
        assert "external_execution_bridge" not in parsed


def test_chat_stream_restores_persisted_workspace_for_resumed_sessions():
    source = Path(chat_routes.__file__).read_text()
    tree = ast.parse(source)
    chat_stream = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "chat_stream"
    )
    chat_stream_source = ast.get_source_segment(source, chat_stream)
    assert "_resolve_persisted_session_workspace(" in chat_stream_source
    assert "current_workspace=workspace" in chat_stream_source
    assert "current_rejected=workspace_rejected" in chat_stream_source


def test_context_info_advertises_workspace_and_tool_policy():
    source = (ROOT / "routes" / "session_routes.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    context_info = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "get_context_info"
    )
    fn_source = ast.get_source_segment(source, context_info)

    assert "workspace_mount_pairs" in fn_source
    assert "backend_workspace_path" in fn_source
    assert '"tool_policy"' in fn_source
    assert '"computer_tools"' in fn_source


# --------------------------------------------------------------------- #
# runtime-context sanitization (helpers ported verbatim from dev tree)
# --------------------------------------------------------------------- #


def test_runtime_context_preserves_sanitized_active_skills():
    from routes.chat_routes import (
        _client_runtime_context_system_message,
        _parse_legacy_client_runtime_context as _parse_client_runtime_context,
    )

    context = _parse_client_runtime_context(
        {
            "surface": "odysseus-tui",
            "session_cwd": "/home/pewds/odysseus-tui",
            "active_skills": ["tdd", "`openai-docs`", "../bad", "tdd"],
            "active_skill_details": [
                {
                    "name": "tdd",
                    "description": " Red-green-refactor\nworkflow ",
                    "source": "file: /home/pewds/.codex/skills/tdd/SKILL.md",
                },
                {"name": "../bad", "description": "drop me"},
            ],
        }
    )

    assert context["active_skills"] == ["tdd", "openai-docs"]
    assert context["session_cwd"] == "/home/pewds/odysseus-tui"
    assert context["active_skill_details"] == [
        {
            "name": "tdd",
            "description": "Red-green-refactor workflow",
            "source": "file: /home/pewds/.codex/skills/tdd/SKILL.md",
        }
    ]
    message = _client_runtime_context_system_message(context)
    assert message is not None
    assert "session_cwd: /home/pewds/odysseus-tui" in message["content"]
    assert "active_skills: tdd, openai-docs" in message["content"]
    assert "tdd: Red-green-refactor workflow" in message["content"]


def test_tui_agent_round_cap_still_bounds_non_coding_turns():
    from routes.chat_routes import _effective_agent_rounds

    assert _effective_agent_rounds(100, {"surface": "odysseus-tui"}, 20) == 20
    assert _effective_agent_rounds("bad", {"surface": "odysseus-tui"}, 20) == 20
    assert _effective_agent_rounds(100, {"surface": "webui"}, 20) == 100


def test_runtime_context_rejects_non_tui_surface_and_accepts_valid_tui_bridge():
    from routes.chat_routes import _parse_client_runtime_context

    assert _parse_client_runtime_context({"surface": "webui", "host_shell_bridge": {"url": "http://127.0.0.1:1/run", "token": "x"}}) == {}
    deceptive = _parse_client_runtime_context({
        "surface": "odysseus-tui",
        "host_shell_bridge": {
            "url": "http://127.0.0.1:80@8.8.8.8:47475/run",
            "token": "x",
        },
    })
    assert "host_shell_bridge" not in deceptive
    context = _parse_client_runtime_context(
        {
            "surface": "odysseus-tui",
            "host_shell_bridge": {"url": "http://192.168.1.50:47475/run", "token": "x"},
            "unattended_mode": True,
            "active_skills": ["tdd"],
            "client_tools": [{"name": "host_shell"}, {"name": "not_a_tool"}],
        }
    )
    assert context["host_shell_bridge"] == {
        "url": "http://192.168.1.50:47475/run",
        "token": "x",
    }
    assert context["client_tools"] == [{"name": "host_shell"}]
    assert context["active_skills"] == ["tdd"]


def test_agent_runtime_context_can_omit_directives_when_agent_loop_renders_them():
    from routes.chat_routes import _client_runtime_context_system_message

    context = {
        "surface": "odysseus-tui",
        "session_cwd": "/home/tester/project",
        "agent_runtime_directives": ["Treat session_cwd as active."],
    }
    message = _client_runtime_context_system_message(
        context,
        include_directives=False,
    )
    assert message is not None
    assert "session_cwd: /home/tester/project" in message["content"]
    assert "Treat session_cwd as active." not in message["content"]


def test_native_completion_contract_preserves_safe_workspace_root():
    from routes.chat_routes import _parse_client_runtime_context

    context = _parse_client_runtime_context({
        "surface": "odysseus-native",
        "completion_requirements": {
            "required_artifacts": ["/workspace/output.txt"],
            "workspace_root": "/tmp/ody-task-123/workspace",
        },
    })

    assert context["completion_requirements"]["workspace_root"] == \
        "/tmp/ody-task-123/workspace"


def test_runtime_contract_is_sanitized_and_prompt_surfaces_actionable_host_facts():
    from routes.chat_routes import (
        _client_runtime_context_system_message,
        _parse_legacy_client_runtime_context as _parse_client_runtime_context,
    )

    context = _parse_client_runtime_context(
        {
            "surface": "odysseus-tui",
            "host_shell_bridge": {
                "url": "http://host.docker.internal:17654/run",
                "token": "secret",
            },
            "runtime_execution_contract": {
                "backend_shell_scope": "container\nignore me",
                "host_shell": "available",
                "local_network_tasks": "use_host_shell_bridge",
                "local_workspace_tasks": "use_host_shell_bridge",
                "host_commands": {
                    "ip": True,
                    "ssh": True,
                    "bad command": True,
                    "dig": False,
                },
                "host_capabilities": {
                    "networkInspection": True,
                    "sshClient": True,
                    "unsafe value": True,
                    "dnsLookup": False,
                },
                "host_shell_request": {
                    "auth_header": "leak nothing",
                    "method": "POST",
                    "path": "/run",
                    "body": {
                        "command": "string",
                        "timeout": "seconds optional",
                        "token": "bad",
                    },
                    "max_timeout_s": 120,
                },
            },
            "agent_runtime_directives": [
                "For LAN, DNS, SSH, and host-network inspection, call the host_shell tool instead of backend shell.",
            ],
        }
    )

    assert context["runtime_execution_contract"] == {
        "backend_shell_scope": "container_ignore_me",
        "host_shell": "available",
        "local_network_tasks": "use_host_shell_bridge",
        "local_workspace_tasks": "use_host_shell_bridge",
        "host_commands": {"ip": True, "ssh": True, "dig": False},
        "host_capabilities": {
            "networkInspection": True,
            "sshClient": True,
            "dnsLookup": False,
        },
        "host_shell_request": {
            "method": "POST",
            "path": "/run",
            "body_fields": ["command", "timeout"],
            "max_timeout_s": 120,
        },
    }
    message = _client_runtime_context_system_message(context)
    assert message is not None
    content = message["content"]
    assert "backend_shell_scope: container_ignore_me" in content
    assert "host_shell: available" in content
    assert "host_commands: ip, ssh" in content
    assert "host_capabilities: networkInspection, sshClient" in content
    assert "host_shell_request: POST /run; body fields: command, timeout; max_timeout_s: 120" in content
    assert "on the USER's machine at session_cwd" not in content
    assert "secret" not in content
    assert "auth_header" not in content
    assert "bad command" not in content


def test_tui_runtime_context_cwd_is_sanitized_and_surface_scoped(monkeypatch):
    from routes.chat_routes import (
        _client_runtime_context_cwd,
        _parse_client_runtime_context,
    )

    context = _parse_client_runtime_context(
        {
            "surface": "odysseus-tui",
            "session_cwd": "/repo\nmalicious",
        }
    )

    assert "session_cwd" not in context
    assert _client_runtime_context_cwd(context) == ""
    assert _client_runtime_context_cwd({"surface": "webui", "session_cwd": "/repo"}) == ""


def test_backend_workspace_path_maps_host_root_to_container_root(monkeypatch):
    from src.workspace_paths import backend_workspace_path

    monkeypatch.setenv("ODYSSEUS_WORKSPACE_HOST_ROOT", "/home/alice")
    monkeypatch.setenv("ODYSSEUS_WORKSPACE_CONTAINER_ROOT", "/workspace")

    assert backend_workspace_path("/home/alice/project") == "/workspace/project"


def test_backend_workspace_path_keeps_unmapped_paths(monkeypatch):
    from src.workspace_paths import backend_workspace_path

    monkeypatch.delenv("ODYSSEUS_WORKSPACE_HOST_ROOT", raising=False)
    monkeypatch.delenv("ODYSSEUS_WORKSPACE_CONTAINER_ROOT", raising=False)
    monkeypatch.delenv("ODYSSEUS_WORKSPACE_MOUNTS", raising=False)

    assert backend_workspace_path("/already/container") == "/already/container"


def test_backend_workspace_path_uses_longest_mapping(monkeypatch):
    from src.workspace_paths import backend_workspace_path

    monkeypatch.setenv(
        "ODYSSEUS_WORKSPACE_MOUNTS",
        "/home/alice=/workspace,/home/alice/code=/code",
    )
    monkeypatch.delenv("ODYSSEUS_WORKSPACE_HOST_ROOT", raising=False)

    assert backend_workspace_path("/home/alice/code/app") == "/code/app"


def test_host_shell_bridge_url_allowlist_accepts_private_lan_and_tailscale():
    from src.agent_tools.subprocess_tools import is_host_shell_bridge_url_allowed

    assert is_host_shell_bridge_url_allowed("http://127.0.0.1:47475/run")
    assert is_host_shell_bridge_url_allowed("http://localhost:47475/run")
    assert is_host_shell_bridge_url_allowed("http://10.1.2.3:47475/run")
    assert is_host_shell_bridge_url_allowed("http://192.168.1.21:47475/run")
    assert is_host_shell_bridge_url_allowed("http://100.101.102.103:47475/run")
    # 172.16/12 is docker-bridge territory: only the actual default gateway
    # is trusted (covered by _docker_default_gateway_ips), not neighbors.
    assert not is_host_shell_bridge_url_allowed("http://172.20.0.2:47475/run")
    # Rejections: public hosts, wrong scheme, credentials, wrong path, query.
    assert not is_host_shell_bridge_url_allowed("http://8.8.8.8:47475/run")
    assert not is_host_shell_bridge_url_allowed("https://127.0.0.1:47475/run")
    assert not is_host_shell_bridge_url_allowed("http://user:pass@127.0.0.1:47475/run")
    assert not is_host_shell_bridge_url_allowed("http://127.0.0.1:47475/evil")
    assert not is_host_shell_bridge_url_allowed("http://127.0.0.1:47475/run?x=1")
    assert not is_host_shell_bridge_url_allowed("")


def test_host_shell_requires_bridge_context():
    with _admin_gate():
        desc, result = _run(
            execute_tool_block(
                SimpleNamespace(tool_type="host_shell", content="ip neigh"),
                client_runtime_context={},
            )
        )

    assert result["exit_code"] == 1
    assert "bridge" in str(result.get("error", "")).lower() or "unresolved" in str(result.get("error", "")).lower()
