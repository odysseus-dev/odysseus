"""Adversarial coverage of the TUI bridge's outbound URL boundary."""

import asyncio
import json

import httpx
import pytest

from src import tool_execution as te


def _context(url):
    return {
        "surface": "odysseus-tui",
        "host_shell_bridge": {"url": url, "token": "secret"},
    }


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:17654",
    "http://localhost:17654",
    "http://[::1]:17654",
    "http://10.1.2.3:17654",
    "http://192.168.1.2:17654",
    "http://100.64.0.1:17654",
    "http://host.docker.internal:17654",
    "http://127.0.0.1:17654/run",
])
def test_cancel_preserves_validated_bridge_authority(monkeypatch, url):
    seen = []
    client_type = httpx.AsyncClient

    async def respond(request):
        seen.append(request)
        return httpx.Response(200, json={"exit_code": 0})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_type(
        transport=httpx.MockTransport(respond), **kwargs,
    ))
    asyncio.run(te._cancel_bridge_request(_context(url)["host_shell_bridge"], "request-1"))

    assert len(seen) == 1
    assert seen[0].url.host == httpx.URL(url).host
    assert seen[0].url.port == 17654
    assert seen[0].url.path == "/cancel"
    assert seen[0].headers["x-odysseus-tui-bridge-token"] == "secret"


@pytest.mark.parametrize("suffix", ["?", "#", "/run?", "/run#"])
@pytest.mark.parametrize("operation", ["patch", "read", "cancel"])
def test_endpoint_uses_path_even_with_empty_url_delimiters(monkeypatch, suffix, operation):
    seen = []
    client_type = httpx.AsyncClient

    async def respond(request):
        seen.append(request)
        return httpx.Response(200, json={"exit_code": 0})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_type(
        transport=httpx.MockTransport(respond), **kwargs,
    ))
    context = _context("http://127.0.0.1:17654" + suffix)
    if operation == "patch":
        asyncio.run(te._apply_patch_via_tui_host_bridge("patch", context))
    elif operation == "read":
        asyncio.run(te._bridge_post(context["host_shell_bridge"], "/read", {},
                                   timeout_s=1, err_prefix="read_file"))
    else:
        asyncio.run(te._cancel_bridge_request(context["host_shell_bridge"], "request-1"))

    assert len(seen) == 1
    assert seen[0].url.host == "127.0.0.1"
    assert seen[0].url.port == 17654
    assert seen[0].url.path == "/" + operation
    assert not seen[0].url.query
    assert not seen[0].url.fragment


@pytest.mark.parametrize("url", [
    "https://127.0.0.1:17654/run",
    "file:///run",
    "http://example.com/run",
    "http://127.0.0.1.evil.test/run",
    "http://host.docker.internal.evil.test/run",
    "http://169.254.169.254/run",
    "http://172.20.0.2/run",
    "http://8.8.8.8/run",
    "http://100.63.255.255/run",
    "http://100.128.0.0/run",
    "http://user:pass@127.0.0.1/run",
    "http://127.0.0.1@evil.test/run",
    "http://127.0.0.1\\@evil.test/run",
    "http://127%2e0%2e0%2e1/run",
    "http://2130706433/run",
    "http://0x7f000001/run",
    "http://0177.0.0.1/run",
    "http://127.1/run",
    "http://[::ffff:127.0.0.1]/run",
    "http://[::ffff:169.254.169.254]/run",
    "http://[fc00::1]/run",
    "http://[fe80::1%25eth0]/run",
    "http://[2001:4860:4860::8888]/run",
    "http://localhost\u3002evil.test/run",
    "http://127.0.0.1/../run",
    "http://127.0.0.1/%72un",
    "http://127.0.0.1/patch",
    "http://127.0.0.1/run?target=http://evil.test",
    "http://127.0.0.1/run#evil",
])
def test_untrusted_bridge_targets_never_construct_a_client(monkeypatch, url):
    from src.agent_tools import subprocess_tools

    monkeypatch.setattr(subprocess_tools, "_docker_default_gateway_ips", lambda: set())

    def unexpected_client(**kwargs):
        pytest.fail("rejected URL reached HTTPX")

    monkeypatch.setattr(httpx, "AsyncClient", unexpected_client)
    context = _context(url)
    assert te._tui_host_bridge_patch_url(context) is None
    assert te._client_bridge(context) is None
    assert asyncio.run(te._apply_patch_via_tui_host_bridge("patch", context))["exit_code"] == 1
    assert asyncio.run(te._bridge_post(context["host_shell_bridge"], "/read", {},
                                      timeout_s=1, err_prefix="read_file"))["exit_code"] == 1
    asyncio.run(te._cancel_bridge_request(context["host_shell_bridge"], "request-1"))
    asyncio.run(subprocess_tools._cancel_host_shell_bridge_request(url, "secret", "request-1"))


@pytest.mark.parametrize("operation", ["patch", "read", "cancel", "host_run", "host_cancel"])
def test_bridge_does_not_follow_redirect_to_untrusted_host(monkeypatch, operation):
    from src.agent_tools import subprocess_tools

    seen = []
    client_type = httpx.AsyncClient

    async def respond(request):
        seen.append(request)
        return httpx.Response(307, headers={"Location": "http://evil.test/collect"},
                              json={"exit_code": 1})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_type(
        transport=httpx.MockTransport(respond), **kwargs,
    ))
    context = _context("http://127.0.0.1:17654/run")
    if operation == "patch":
        asyncio.run(te._apply_patch_via_tui_host_bridge("patch", context))
    elif operation == "read":
        asyncio.run(te._bridge_post(context["host_shell_bridge"], "/read", {},
                                   timeout_s=1, err_prefix="read_file"))
    elif operation == "cancel":
        asyncio.run(te._cancel_bridge_request(context["host_shell_bridge"], "request-1"))
    elif operation == "host_run":
        asyncio.run(subprocess_tools.HostShellTool().execute(
            '{"command":"printf ready"}', {"client_runtime_context": context},
        ))
    else:
        asyncio.run(subprocess_tools._cancel_host_shell_bridge_request(
            context["host_shell_bridge"]["url"], "secret", "request-1",
        ))
    assert len(seen) == 1
    assert seen[0].url.host == "127.0.0.1"


def test_cancelled_patch_with_bare_bridge_url_stays_on_bridge(monkeypatch):
    seen = []
    posted = asyncio.Event()
    client_type = httpx.AsyncClient

    async def respond(request):
        seen.append(request)
        if request.url.path == "/patch":
            posted.set()
            await asyncio.Future()
        return httpx.Response(200, json={"exit_code": 0})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_type(
        transport=httpx.MockTransport(respond), **kwargs,
    ))

    async def scenario():
        task = asyncio.create_task(te._apply_patch_via_tui_host_bridge(
            "patch", _context("http://127.0.0.1:17654"),
        ))
        await posted.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.gather(*te._bridge_cancel_tasks)

    asyncio.run(scenario())
    assert [str(request.url) for request in seen] == [
        "http://127.0.0.1:17654/patch", "http://127.0.0.1:17654/cancel",
    ]


def test_only_discovered_docker_gateway_is_allowed(monkeypatch):
    from src.agent_tools import subprocess_tools

    monkeypatch.setattr(subprocess_tools, "_docker_default_gateway_ips",
                        lambda: {"172.20.0.1"})
    assert te._tui_host_bridge_patch_url(_context("http://172.20.0.1:17654/run")) == (
        "http://172.20.0.1:17654/patch", "secret",
    )
    assert te._tui_host_bridge_patch_url(_context("http://172.20.0.2:17654/run")) is None


@pytest.mark.parametrize("authority", ["127.0.0.1:17654", "[::1]:17654"])
@pytest.mark.parametrize("suffix", ["", "/run", "?", "#", "/run?", "/run#"])
def test_host_shell_cancellation_preserves_authority(monkeypatch, authority, suffix):
    from src.agent_tools import subprocess_tools

    seen = []
    client_type = httpx.AsyncClient

    async def respond(request):
        seen.append(request)
        return httpx.Response(200, json={"exit_code": 0})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_type(
        transport=httpx.MockTransport(respond), **kwargs,
    ))
    asyncio.run(subprocess_tools._cancel_host_shell_bridge_request(
        "http://" + authority + suffix, "secret", "request-1",
    ))
    assert [str(request.url) for request in seen] == ["http://" + authority + "/cancel"]
    assert seen[0].headers["x-odysseus-tui-bridge-token"] == "secret"


@pytest.mark.parametrize("authority", ["127.0.0.1:17654", "[::1]:17654"])
@pytest.mark.parametrize("suffix", ["", "/run", "?", "#", "/run?", "/run#"])
def test_real_host_shell_cancellation_stays_on_bridge(monkeypatch, authority, suffix):
    from src.agent_tools import subprocess_tools

    seen = []
    entered = asyncio.Event()
    client_type = httpx.AsyncClient

    async def respond(request):
        seen.append(request)
        if "command" in json.loads(request.content):
            entered.set()
            await asyncio.Future()
        return httpx.Response(200, json={"exit_code": 0})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_type(
        transport=httpx.MockTransport(respond), **kwargs,
    ))

    async def scenario():
        task = asyncio.create_task(subprocess_tools.HostShellTool().execute(
            '{"command":"printf ready"}',
            {"client_runtime_context": _context("http://" + authority + suffix)},
        ))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.gather(*subprocess_tools._HOST_SHELL_CANCEL_TASKS)

    asyncio.run(scenario())
    assert [str(request.url) for request in seen] == [
        "http://" + authority + "/run", "http://" + authority + "/cancel",
    ]
    assert json.loads(seen[0].content)["request_id"] == json.loads(seen[1].content)["request_id"]


@pytest.mark.parametrize("proxy_var", ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"])
def test_bridge_requests_ignore_environment_proxies(monkeypatch, proxy_var):
    from src.agent_tools import subprocess_tools

    direct_requests = []
    proxy_requests = []
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    monkeypatch.delenv("REQUEST_METHOD", raising=False)

    async def respond(reader, writer, requests):
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            headers = dict(line.split(b":", 1) for line in head.split(b"\r\n")[1:] if b":" in line)
            headers = {key.lower(): value.strip() for key, value in headers.items()}
            body = await reader.readexactly(int(headers.get(b"content-length", b"0")))
            requests.append((head.split(b"\r\n", 1)[0], headers, json.loads(body)))
            payload = {"exit_code": 0, "output": "done", "status": "completed"}
            if json.loads(body).get("command", "").startswith("sleep 22"):
                payload = {"status": "running", "job_id": "job-1"}
            raw = json.dumps(payload).encode()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                         + f"Content-Length: {len(raw)}\r\nConnection: close\r\n\r\n".encode() + raw)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def scenario():
        direct = await asyncio.start_server(
            lambda r, w: respond(r, w, direct_requests), "127.0.0.1", 0,
        )
        proxy = await asyncio.start_server(
            lambda r, w: respond(r, w, proxy_requests), "127.0.0.1", 0,
        )
        async with direct, proxy:
            direct_port = direct.sockets[0].getsockname()[1]
            proxy_port = proxy.sockets[0].getsockname()[1]
            monkeypatch.setenv(proxy_var, f"http://127.0.0.1:{proxy_port}")
            monkeypatch.setenv("NO_PROXY", "")
            context = _context(f"http://127.0.0.1:{direct_port}/run")
            bridge = context["host_shell_bridge"]
            assert (await te._apply_patch_via_tui_host_bridge("patch", context))["exit_code"] == 0
            assert (await te._bridge_post(bridge, "/read", {}, timeout_s=1,
                                         err_prefix="read_file"))["exit_code"] == 0
            await te._cancel_bridge_request(bridge, "request-1")
            await subprocess_tools._cancel_host_shell_bridge_request(bridge["url"], "secret", "request-2")
            for command in ("printf ready", "sleep 22; printf ready"):
                result = await subprocess_tools.HostShellTool().execute(
                    json.dumps({"command": command}), {"client_runtime_context": context},
                )
                assert result["exit_code"] == 0
            assert not proxy_requests, "bridge traffic and its token reached an environment proxy"
            assert len(direct_requests) == 7
            assert all(headers[b"x-odysseus-tui-bridge-token"] == b"secret"
                       for _, headers, _ in direct_requests)

    asyncio.run(asyncio.wait_for(scenario(), 10))
