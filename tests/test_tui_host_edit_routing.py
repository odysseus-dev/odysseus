import asyncio
import re

import src.tool_execution as tool_execution


def test_edit_file_routes_json_arguments_to_host_edit_endpoint(monkeypatch):
    calls = []

    async def fake_bridge_post(bridge, path, payload, *, timeout_s, err_prefix):
        calls.append((path, payload, timeout_s, err_prefix))
        return {"output": "Edited config.txt", "exit_code": 0}

    monkeypatch.setattr(tool_execution, "_bridge_post", fake_bridge_post)

    desc, result = asyncio.run(
        tool_execution._route_tool_via_bridge(
            "edit_file",
            '{"path":"config.txt","old_string":"status=old","new_string":"status=new"}',
            None,
            {
                "surface": "odysseus-tui",
                "host_shell_bridge": {
                    "url": "http://127.0.0.1:1/run",
                    "token": "test",
                }
            },
        )
    )

    assert desc == "edit_file: config.txt"
    assert result["exit_code"] == 0
    assert len(calls) == 1
    path, payload, timeout, prefix = calls[0]
    assert path == "/edit"
    assert payload | {"request_id": "<id>"} == {
        "path": "config.txt",
        "old_string": "status=old",
        "new_string": "status=new",
        "replace_all": False,
        "request_id": "<id>",
    }
    assert re.fullmatch(r"[A-Za-z0-9_-]+", payload["request_id"])
    assert timeout == 60.0
    assert prefix == "edit_file"


def test_edit_file_rejects_non_boolean_replace_all_without_host_call(monkeypatch):
    calls = []

    async def fake_bridge_post(*args, **kwargs):
        calls.append((args, kwargs))
        return {"output": "unexpected", "exit_code": 0}

    monkeypatch.setattr(tool_execution, "_bridge_post", fake_bridge_post)

    desc, result = asyncio.run(
        tool_execution._route_tool_via_bridge(
            "edit_file",
            '{"path":"config.txt","old_string":"x","new_string":"y","replace_all":"false"}',
            None,
            {
                "surface": "odysseus-tui",
                "host_shell_bridge": {
                    "url": "http://127.0.0.1:1/run",
                    "token": "test",
                }
            },
        )
    )

    assert desc == "edit_file: invalid arguments"
    assert result == {
        "error": "edit_file: replace_all must be a boolean",
        "exit_code": 1,
    }
    assert calls == []
