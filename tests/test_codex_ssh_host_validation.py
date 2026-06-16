"""The Codex cookbook bridge resolves a task's SSH target (remoteHost / sshPort)
from cookbook_state.json and interpolates it into an ``ssh ...`` command string
that runs through a shell. The command body is shlex-quoted, but the host and
port were not validated, so a tampered task entry carrying shell metacharacters
in ``remoteHost`` would be injected into that command.

These pin validation on the host/port before they reach the ssh string, matching
the validators the rest of the cookbook routes already apply.
"""
import asyncio

import pytest
from fastapi import APIRouter, HTTPException
from starlette.requests import Request

import routes.codex_routes as codex_routes


def _route_endpoint(path: str, method: str, router=None):
    router = router or codex_routes.setup_codex_routes()
    for route in router.routes:
        if route.path == path and method in route.methods:
            return route.endpoint
    raise AssertionError(f"{method} {path} route not found")


def _launch_request() -> Request:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/codex/cookbook/adopt",
            "headers": [],
            "state": {},
        }
    )
    request.state.api_token = True
    request.state.api_token_owner = "alice"
    request.state.api_token_scopes = ["cookbook:launch"]
    return request


def _codex_request(scopes) -> Request:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/codex/emails/draft-document",
            "headers": [],
            "state": {},
        }
    )
    request.state.api_token = True
    request.state.api_token_owner = "alice"
    request.state.api_token_scopes = list(scopes)
    return request


def test_rejects_remote_host_with_shell_metacharacters():
    task = {"remoteHost": "box; rm -rf ~", "sshPort": ""}
    with pytest.raises(HTTPException) as exc:
        codex_routes._ssh_prefix_for_task(task)
    assert exc.value.status_code == 400


def test_rejects_non_numeric_ssh_port():
    task = {"remoteHost": "box", "sshPort": "22; evil"}
    with pytest.raises(HTTPException) as exc:
        codex_routes._ssh_prefix_for_task(task)
    assert exc.value.status_code == 400


def test_local_task_has_no_host():
    host, port_flag = codex_routes._ssh_prefix_for_task({})
    assert host == ""
    assert port_flag == ""


def test_valid_remote_builds_port_flag():
    host, port_flag = codex_routes._ssh_prefix_for_task(
        {"remoteHost": "user@box", "sshPort": "2222"}
    )
    assert host == "user@box"
    assert port_flag == "-p 2222 "


def test_integer_ssh_port_in_stored_task_normalizes_without_crashing():
    host, port_flag = codex_routes._ssh_prefix_for_task(
        {"remoteHost": "user@box", "sshPort": 2222}
    )
    assert host == "user@box"
    assert port_flag == "-p 2222 "


def test_default_ssh_port_omits_flag():
    host, port_flag = codex_routes._ssh_prefix_for_task(
        {"remoteHost": "box", "sshPort": "22"}
    )
    assert host == "box"
    assert port_flag == ""


def test_adopt_rejects_ssh_option_host_before_shell(monkeypatch):
    calls = []

    async def fail_if_shell_runs(*args, **kwargs):
        calls.append((args, kwargs))
        raise RuntimeError("shell should not run for invalid host")

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fail_if_shell_runs)

    endpoint = _route_endpoint("/api/codex/cookbook/adopt", "POST")
    body = {
        "tmux_session": "serve_abc123",
        "model": "org/model",
        "host": "-oProxyCommand=sh",
    }

    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(_launch_request(), body))

    assert exc.value.status_code == 400
    assert calls == []


@pytest.mark.asyncio
async def test_email_draft_document_accepts_send_scope_with_document_write():
    calls = []
    document_router = APIRouter()

    @document_router.post("/api/document")
    async def create_document(request: Request, req):
        calls.append((request.state.current_user, req.title, req.language, req.content))
        return {"id": "doc-1", "title": req.title}

    router = codex_routes.setup_codex_routes(document_router=document_router)
    endpoint = _route_endpoint("/api/codex/emails/draft-document", "POST", router=router)

    result = await endpoint(
        _codex_request(["email:send", "documents:write"]),
        {"to": "recipient@example.com", "subject": "Subject", "body": "Body"},
    )

    assert result["draft_type"] == "document"
    assert result["send_required_confirmation"] is True
    assert calls == [
        (
            "alice",
            "Subject",
            "email",
            "To: recipient@example.com\nSubject: Subject\n---\nBody\n",
        )
    ]
