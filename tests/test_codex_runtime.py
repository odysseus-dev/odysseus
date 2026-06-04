import asyncio
import json

import pytest

from src import codex_runtime


@pytest.mark.asyncio
async def test_json_rpc_stream_maps_to_odysseus_sse(monkeypatch):
    monkeypatch.setenv("CODEX_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("CODEX_RUNTIME_MAX_CONCURRENT_REQUESTS", "2")
    monkeypatch.setattr(codex_runtime, "is_codex_auth_ready", lambda: True)

    class FakeSession:
        def __init__(self, *, model, timeout):
            self.model = model
            self.timeout = timeout
            self.messages = iter([
                {"id": 1, "result": {"turn": {"id": "turn_1"}}},
                {"method": "item/agentMessage/delta", "params": {"delta": "hel"}},
                {"method": "item/agentMessage/delta", "params": {"delta": "lo"}},
                {"method": "turn/completed", "params": {"turn": {"status": "completed", "items": []}}},
            ])

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def initialize(self):
            return None

        async def start_thread(self):
            return "thread_1"

        async def start_turn(self, thread_id, prompt):
            assert thread_id == "thread_1"
            assert "<USER>\nSay hello\n</USER>" in prompt
            return 1

        async def read_message(self):
            return next(self.messages)

        async def respond_to_server_request(self, msg):
            raise AssertionError(f"unexpected server request: {msg}")

    monkeypatch.setattr(codex_runtime, "CodexAppServerSession", FakeSession)

    chunks = [
        chunk
        async for chunk in codex_runtime.stream_codex(
            "gpt-5.5",
            [{"role": "user", "content": "Say hello"}],
            timeout=12,
        )
    ]

    assert json.loads(chunks[0][6:])["delta"] == "hel"
    assert json.loads(chunks[1][6:])["delta"] == "lo"
    assert chunks[-1] == "data: [DONE]\n\n"


@pytest.mark.asyncio
async def test_stream_codex_missing_auth_returns_setup_error(monkeypatch):
    monkeypatch.setenv("CODEX_RUNTIME_ENABLED", "true")
    monkeypatch.setattr(codex_runtime, "is_codex_auth_ready", lambda: False)

    chunks = [
        chunk
        async for chunk in codex_runtime.stream_codex(
            "gpt-5.5",
            [{"role": "user", "content": "hi"}],
        )
    ]

    assert len(chunks) == 1
    assert chunks[0].startswith("event: error")
    payload = json.loads(chunks[0].split("data:", 1)[1].strip())
    assert payload["code"] == "codex_runtime_auth_required"
    assert payload["status"] == 401
    assert payload["retryable"] is False
    assert "codex login --device-auth" in payload["error"]


def test_codex_runtime_models_env_override(monkeypatch):
    monkeypatch.setenv("CODEX_RUNTIME_MODELS", "alpha,beta,alpha")
    monkeypatch.setenv("CODEX_RUNTIME_DEFAULT_MODEL", "gamma")

    assert codex_runtime.codex_runtime_models() == ["gamma", "alpha", "beta"]
    assert codex_runtime.default_codex_model() == "gamma"


def test_messages_to_codex_prompt_flattens_roles():
    prompt = codex_runtime.messages_to_codex_prompt([
        {"role": "system", "content": "System text"},
        {"role": "developer", "content": "Developer text"},
        {"role": "user", "content": [{"type": "text", "text": "User text"}]},
        {"role": "assistant", "content": "Assistant text"},
        {"role": "tool", "tool_call_id": "call_1", "content": "Tool result"},
    ])

    assert "<SYSTEM>\nSystem text\n</SYSTEM>" in prompt
    assert "<DEVELOPER>\nDeveloper text\n</DEVELOPER>" in prompt
    assert "<USER>\nUser text\n</USER>" in prompt
    assert "<ASSISTANT>\nAssistant text\n</ASSISTANT>" in prompt
    assert "<TOOL RESULT call_1>\nTool result\n</TOOL RESULT call_1>" in prompt


def test_messages_to_codex_prompt_scrubs_unsupported_local_paths(monkeypatch):
    monkeypatch.setenv("CODEX_RUNTIME_MESSAGE_CHAR_LIMIT", "1000")
    prompt = codex_runtime.messages_to_codex_prompt([
        {
            "role": "user",
            "content": [
                {"type": "localImage", "path": "/Users/alice/private/photo.png"},
                {"type": "input_file", "path": "/Users/alice/private/secret.pdf"},
                {"type": "text", "text": "x" * 1500},
            ],
        },
    ])

    assert "/Users/alice/private" not in prompt
    assert "[unsupported image attachment omitted]" in prompt
    assert "[unsupported file attachment omitted]" in prompt
    assert "[... message truncated ...]" in prompt


@pytest.mark.asyncio
async def test_stream_codex_returns_busy_when_concurrency_limit_reached(monkeypatch):
    monkeypatch.setenv("CODEX_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("CODEX_RUNTIME_MAX_CONCURRENT_REQUESTS", "1")
    monkeypatch.setattr(codex_runtime, "is_codex_auth_ready", lambda: True)

    slot = codex_runtime._try_acquire_runtime_slot()
    try:
        chunks = [
            chunk
            async for chunk in codex_runtime.stream_codex(
                "gpt-5.5",
                [{"role": "user", "content": "hi"}],
            )
        ]
    finally:
        slot.release()

    assert len(chunks) == 1
    payload = json.loads(chunks[0].split("data:", 1)[1].strip())
    assert payload["code"] == "runtime_busy"
    assert payload["status"] == 429
    assert payload["retryable"] is True


@pytest.mark.asyncio
async def test_stream_codex_uses_completed_final_text_when_no_deltas(monkeypatch):
    monkeypatch.setenv("CODEX_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("CODEX_RUNTIME_MAX_CONCURRENT_REQUESTS", "2")
    monkeypatch.setattr(codex_runtime, "is_codex_auth_ready", lambda: True)

    class FakeSession:
        def __init__(self, *, model, timeout):
            self.messages = iter([
                {"id": 1, "result": {"turn": {"id": "turn_1"}}},
                {
                    "method": "turn/completed",
                    "params": {
                        "turn": {
                            "status": "completed",
                            "items": [{"type": "agentMessage", "text": "final text"}],
                        }
                    },
                },
            ])

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def initialize(self):
            return None

        async def start_thread(self):
            return "thread_1"

        async def start_turn(self, thread_id, prompt):
            return 1

        async def read_message(self):
            return next(self.messages)

        async def respond_to_server_request(self, msg):
            raise AssertionError(f"unexpected server request: {msg}")

    monkeypatch.setattr(codex_runtime, "CodexAppServerSession", FakeSession)

    chunks = [
        chunk
        async for chunk in codex_runtime.stream_codex(
            "gpt-5.5",
            [{"role": "user", "content": "Say hello"}],
        )
    ]

    assert json.loads(chunks[0][6:])["delta"] == "final text"
    assert chunks[-1] == "data: [DONE]\n\n"


@pytest.mark.asyncio
async def test_stream_codex_rejects_server_requests(monkeypatch):
    monkeypatch.setenv("CODEX_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("CODEX_RUNTIME_MAX_CONCURRENT_REQUESTS", "2")
    monkeypatch.setattr(codex_runtime, "is_codex_auth_ready", lambda: True)
    responded = []

    class FakeSession:
        def __init__(self, *, model, timeout):
            self.messages = iter([
                {"id": 1, "result": {"turn": {"id": "turn_1"}}},
                {"id": 99, "method": "approval/request", "params": {}},
                {"method": "item/agentMessage/delta", "params": {"delta": "ok"}},
                {"method": "turn/completed", "params": {"turn": {"status": "completed", "items": []}}},
            ])

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def initialize(self):
            return None

        async def start_thread(self):
            return "thread_1"

        async def start_turn(self, thread_id, prompt):
            return 1

        async def read_message(self):
            return next(self.messages)

        async def respond_to_server_request(self, msg):
            responded.append(msg)

    monkeypatch.setattr(codex_runtime, "CodexAppServerSession", FakeSession)

    chunks = [
        chunk
        async for chunk in codex_runtime.stream_codex(
            "gpt-5.5",
            [{"role": "user", "content": "hi"}],
        )
    ]

    assert responded and responded[0]["method"] == "approval/request"
    assert json.loads(chunks[0][6:])["delta"] == "ok"


@pytest.mark.asyncio
async def test_stream_codex_closes_session_on_cancellation(monkeypatch):
    monkeypatch.setenv("CODEX_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("CODEX_RUNTIME_MAX_CONCURRENT_REQUESTS", "2")
    monkeypatch.setattr(codex_runtime, "is_codex_auth_ready", lambda: True)
    entered = asyncio.Event()
    exited = False

    class SlowSession:
        def __init__(self, *, model, timeout):
            pass

        async def __aenter__(self):
            entered.set()
            return self

        async def __aexit__(self, exc_type, exc, tb):
            nonlocal exited
            exited = True

        async def initialize(self):
            return None

        async def start_thread(self):
            return "thread_1"

        async def start_turn(self, thread_id, prompt):
            return 1

        async def read_message(self):
            await asyncio.sleep(30)

    monkeypatch.setattr(codex_runtime, "CodexAppServerSession", SlowSession)

    gen = codex_runtime.stream_codex("gpt-5.5", [{"role": "user", "content": "hi"}])
    task = asyncio.create_task(anext(gen))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert exited is True


def test_codex_runtime_status_is_read_only(monkeypatch):
    monkeypatch.setenv("CODEX_RUNTIME_ENABLED", "true")
    monkeypatch.setattr(codex_runtime, "resolve_codex_cli", lambda: "/usr/local/bin/codex")
    monkeypatch.setattr(codex_runtime, "_version_for_cli", lambda cli: "codex-cli 0.137.0")
    monkeypatch.setattr(codex_runtime, "is_codex_auth_ready", lambda: True)
    monkeypatch.setattr(
        codex_runtime,
        "codex_runtime_endpoint_registration_status",
        lambda: {"registered": True, "endpoint_id": "abc123"},
    )

    data = codex_runtime.codex_runtime_status()

    assert data["state"] == "ready"
    assert data["registered"] is True
    assert data["limits"]["max_concurrent_requests"] >= 1


def test_codex_runtime_status_flags_unusable_cli(monkeypatch):
    monkeypatch.setenv("CODEX_RUNTIME_ENABLED", "true")
    monkeypatch.setattr(codex_runtime, "resolve_codex_cli", lambda: "/usr/local/bin/codex")
    monkeypatch.setattr(codex_runtime, "_version_for_cli", lambda cli: None)
    monkeypatch.setattr(codex_runtime, "is_codex_auth_ready", lambda: False)
    monkeypatch.setattr(
        codex_runtime,
        "codex_runtime_endpoint_registration_status",
        lambda: {"registered": True, "endpoint_id": "abc123"},
    )

    data = codex_runtime.codex_runtime_status()

    assert data["state"] == "cli_unavailable"
    assert data["cli_available"] is True
    assert data["cli_usable"] is False
    assert data["diagnostics"][0]["code"] == "cli_unavailable"


def test_codex_runtime_probe_uses_cli_login_status(monkeypatch):
    monkeypatch.setenv("CODEX_RUNTIME_ENABLED", "true")
    monkeypatch.delenv("CODEX_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(codex_runtime, "resolve_codex_cli", lambda: "/usr/local/bin/codex")
    monkeypatch.setattr(codex_runtime, "_version_for_cli", lambda cli: "codex-cli 0.137.0")
    monkeypatch.setattr(
        codex_runtime,
        "_run_codex_cli",
        lambda args, timeout=None: {
            "ok": True,
            "returncode": 0,
            "stdout": "Logged in using ChatGPT",
            "stderr": "",
        },
    )

    data = codex_runtime.codex_runtime_probe()

    assert data["state"] == "ready"
    assert data["auth_ready"] is True
    assert data["auth"]["method"] == "cli_login_status"
