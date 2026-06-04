"""Tests for the in-process NobodyWho provider.

The `nobodywho` package is an optional native dependency and is not installed
in CI, so these tests drive the manager through a fake module object injected
directly (`mgr._mod = _FakeNobodyWho()`), plus explicit unavailability via
`mgr._import_error`. Both paths are deterministic regardless of whether the
real package happens to be importable.
"""
import asyncio
import json
import os

import pytest

from src import llm_core
from src.nobodywho_provider import (
    CANONICAL_URL,
    NobodyWhoManager,
    NobodyWhoModelNotFound,
    NobodyWhoUnavailable,
    is_nobodywho_url,
)


# ---------------------------------------------------------------------------
# Fake nobodywho module
# ---------------------------------------------------------------------------

class _FakeTokenStream:
    def __init__(self, tokens, chat):
        self._tokens = list(tokens)
        self._chat = chat

    async def next_token(self):
        if self._chat.stopped and self._tokens:
            # A stopped generation ends promptly: one buffered token may still
            # arrive, then the stream terminates.
            self._tokens = self._tokens[:1]
        if not self._tokens:
            return None
        return self._tokens.pop(0)


class _FakeChatAsync:
    def __init__(self, model, n_ctx=4096, system_prompt=None, template_variables=None):
        self.model = model
        self.n_ctx = n_ctx
        self.template_variables = template_variables
        self.system_prompt = None
        self.history = None
        self.sampler = None
        self.stopped = False
        self.asked = []
        self.tokens_to_yield = ["Hel", "lo", " there", "!"]

    async def set_sampler_config(self, sampler):
        self.sampler = sampler

    async def set_system_prompt(self, system_prompt):
        self.system_prompt = system_prompt

    async def set_chat_history(self, msgs):
        self.history = msgs

    async def stop_generation(self):
        self.stopped = True

    def ask(self, prompt):
        self.asked.append(prompt)
        self.stopped = False
        return _FakeTokenStream(self.tokens_to_yield, self)


class _FakeModel:
    def __init__(self, model_path, *a, **k):
        self.model_path = model_path

    @staticmethod
    async def load_model_async(model_path, *a, **k):
        return _FakeModel(model_path)


class _FakeSamplerPresets:
    @staticmethod
    def default():
        return ("default",)

    @staticmethod
    def temperature(t):
        return ("temperature", t)


class _FakeNobodyWho:
    Model = _FakeModel
    ChatAsync = _FakeChatAsync
    SamplerPresets = _FakeSamplerPresets

    def __init__(self, cached_models=()):
        self._cached = list(cached_models)

    def get_cached_models(self):
        return [(p, 123) for p in self._cached]


def _available_manager(cached_models=()):
    mgr = NobodyWhoManager()
    mgr._mod = _FakeNobodyWho(cached_models)
    return mgr


def _unavailable_manager():
    mgr = NobodyWhoManager()
    mgr._import_error = "NobodyWho is not installed. Install it with: pip install nobodywho"
    return mgr


def _gguf(tmp_path, rel):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"GGUF fake")
    return p


# ---------------------------------------------------------------------------
# URL detection / provider plumbing
# ---------------------------------------------------------------------------

def test_is_nobodywho_url():
    assert is_nobodywho_url("nobodywho:local")
    assert is_nobodywho_url("NOBODYWHO:local")
    assert is_nobodywho_url("nobodywho:")
    assert not is_nobodywho_url("")
    assert not is_nobodywho_url(None)
    assert not is_nobodywho_url("http://localhost:8000/v1")
    # 'nobodywho' in a path must not trigger the in-process provider
    assert not is_nobodywho_url("https://example.com/nobodywho")


def test_detect_provider_and_label():
    assert llm_core._detect_provider(CANONICAL_URL) == "nobodywho"
    assert llm_core._provider_label(CANONICAL_URL) == "NobodyWho"


def test_endpoint_resolver_passthrough():
    from src.endpoint_resolver import build_chat_url, build_headers, build_models_url, resolve_url

    assert resolve_url(CANONICAL_URL) == CANONICAL_URL
    assert build_chat_url(CANONICAL_URL) == CANONICAL_URL
    assert build_models_url(CANONICAL_URL) == CANONICAL_URL
    assert build_headers("some-key", CANONICAL_URL) == {}


def test_classify_endpoint_local():
    from routes.model_routes import _classify_endpoint

    assert _classify_endpoint(CANONICAL_URL, "auto") == "local"
    # explicit kind still wins
    assert _classify_endpoint(CANONICAL_URL, "api") == "api"


# ---------------------------------------------------------------------------
# Conversation mapping
# ---------------------------------------------------------------------------

def test_prepare_conversation_basic_split():
    system, history, prompt = NobodyWhoManager.prepare_conversation([
        {"role": "system", "content": "Be terse."},
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello!"},
        {"role": "user", "content": "How are you?"},
    ])
    assert system == "Be terse."
    assert history == [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello!"},
    ]
    assert prompt == "How are you?"


def test_prepare_conversation_folds_tools_and_merges_roles():
    system, history, prompt = NobodyWhoManager.prepare_conversation([
        {"role": "user", "content": "list files"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"function": {"name": "ls", "arguments": "{\"path\": \".\"}"}}]},
        {"role": "tool", "content": "a.txt b.txt", "tool_call_id": "1"},
        {"role": "user", "content": "thanks — now what?"},
    ])
    assert system is None
    # assistant tool_calls render as text; the tool result becomes a user turn
    # that merges with the trailing user message into the prompt (strict
    # user/assistant alternation for llama.cpp chat templates).
    assert history == [
        {"role": "user", "content": "list files"},
        {"role": "assistant", "content": '[Called: ls({"path": "."})]'},
    ]
    assert prompt == "[Tool result]\na.txt b.txt\n\nthanks — now what?"


def test_prepare_conversation_multimodal_and_no_trailing_user():
    system, history, prompt = NobodyWhoManager.prepare_conversation([
        {"role": "user", "content": [
            {"type": "text", "text": "what is this?"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}},
        ]},
        {"role": "assistant", "content": "A penguin."},
    ])
    assert history == [
        {"role": "user", "content": "what is this?"},
        {"role": "assistant", "content": "A penguin."},
    ]
    assert prompt == "Continue."


def test_prepare_conversation_merges_consecutive_users():
    _, history, prompt = NobodyWhoManager.prepare_conversation([
        {"role": "user", "content": "part one"},
        {"role": "user", "content": "part two"},
        {"role": "user", "content": "the question"},
    ])
    assert history == []
    # all user turns merge; the merged trailing user turn is the prompt
    assert prompt == "part one\n\npart two\n\nthe question"


# ---------------------------------------------------------------------------
# Model discovery / resolution
# ---------------------------------------------------------------------------

def test_list_models_scans_dir_without_package(tmp_path, monkeypatch):
    monkeypatch.setenv("NOBODYWHO_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "no-hub-here"))
    _gguf(tmp_path, "Qwen3-4B-Q4_K_M.gguf")
    _gguf(tmp_path, "sub/SmolLM2-135M.gguf")
    _gguf(tmp_path, "mmproj-Qwen3-VL.gguf")  # projector — excluded
    _gguf(tmp_path, "notes.txt")  # not a gguf

    mgr = _unavailable_manager()  # dir scan must work without the package
    models = mgr.list_models(max_age=0.0)
    assert sorted(models) == ["Qwen3-4B-Q4_K_M", "SmolLM2-135M"]


def test_list_models_disambiguates_duplicate_stems(tmp_path, monkeypatch):
    monkeypatch.setenv("NOBODYWHO_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "no-hub-here"))
    a = _gguf(tmp_path, "a/model.gguf")
    b = _gguf(tmp_path, "b/model.gguf")

    mgr = _unavailable_manager()
    models = mgr.list_models(max_age=0.0)
    assert sorted(models) == ["a/model", "b/model"]
    assert mgr.resolve_source("a/model") == os.path.realpath(str(a))
    assert mgr.resolve_source("b/model") == os.path.realpath(str(b))


def test_resolve_source_paths_and_refs(tmp_path, monkeypatch):
    monkeypatch.setenv("NOBODYWHO_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "no-hub-here"))
    p = _gguf(tmp_path, "TinyChat.gguf")

    mgr = _unavailable_manager()
    # remote refs pass straight through (NobodyWho downloads + caches them)
    ref = "huggingface:NobodyWho/Qwen_Qwen3-0.6B-GGUF/Qwen_Qwen3-0.6B-Q4_K_M.gguf"
    assert mgr.resolve_source(ref) == ref
    # known id resolves to the file
    assert mgr.resolve_source("TinyChat") == os.path.realpath(str(p))
    # direct path to an existing gguf is accepted
    assert mgr.resolve_source(str(p)) == os.path.realpath(str(p))
    with pytest.raises(NobodyWhoModelNotFound):
        mgr.resolve_source("does-not-exist")
    with pytest.raises(NobodyWhoModelNotFound):
        mgr.resolve_source("")


def test_list_models_includes_nobodywho_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("NOBODYWHO_MODELS_DIR", str(tmp_path / "empty"))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "no-hub-here"))
    cached = _gguf(tmp_path, "cache/Downloaded-Q4.gguf")

    mgr = _available_manager(cached_models=[str(cached)])
    assert mgr.list_models(max_age=0.0) == ["Downloaded-Q4"]


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

async def test_astream_replays_history_and_reports_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("NOBODYWHO_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "no-hub-here"))
    monkeypatch.setenv("NOBODYWHO_CTX", "2048")
    _gguf(tmp_path, "TinyChat.gguf")
    mgr = _available_manager()

    messages = [
        {"role": "system", "content": "Be nice."},
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello!"},
        {"role": "user", "content": "Say hello again"},
    ]
    events = [e async for e in mgr.astream("TinyChat", messages, temperature=0.4)]
    deltas = [e["delta"] for e in events if "delta" in e]
    assert "".join(deltas) == "Hello there!"
    usage = events[-1]["usage"]
    assert usage["output_tokens"] == 4
    assert usage["input_tokens"] > 0

    chat = mgr._chats[mgr.resolve_source("TinyChat")].chat
    assert chat.n_ctx == 2048
    # Bare-`tools` templates (Gemma 4) need the name defined or rendering fails
    assert chat.template_variables == {"tools": False}
    assert chat.system_prompt == "Be nice."
    assert chat.history == [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello!"},
    ]
    assert chat.asked == ["Say hello again"]
    assert chat.sampler == ("temperature", 0.4)
    # generation lock must be released after the stream completes
    assert not mgr._chats[mgr.resolve_source("TinyChat")].lock.locked()


async def test_astream_enforces_max_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("NOBODYWHO_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "no-hub-here"))
    _gguf(tmp_path, "TinyChat.gguf")
    mgr = _available_manager()

    events = [e async for e in mgr.astream(
        "TinyChat", [{"role": "user", "content": "go"}], max_tokens=2,
    )]
    deltas = [e["delta"] for e in events if "delta" in e]
    assert deltas == ["Hel", "lo"]
    chat = mgr._chats[mgr.resolve_source("TinyChat")].chat
    assert chat.stopped is True


async def test_acomplete_and_default_sampler(tmp_path, monkeypatch):
    monkeypatch.setenv("NOBODYWHO_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "no-hub-here"))
    _gguf(tmp_path, "TinyChat.gguf")
    mgr = _available_manager()

    text = await mgr.acomplete("TinyChat", [{"role": "user", "content": "hello"}], temperature=1.0)
    assert text == "Hello there!"
    chat = mgr._chats[mgr.resolve_source("TinyChat")].chat
    assert chat.sampler == ("default",)  # temperature 1.0 → default preset


def test_complete_sync_runs_without_event_loop(tmp_path, monkeypatch):
    monkeypatch.setenv("NOBODYWHO_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "no-hub-here"))
    _gguf(tmp_path, "TinyChat.gguf")
    mgr = _available_manager()

    text = mgr.complete_sync("TinyChat", [{"role": "user", "content": "hello"}])
    assert text == "Hello there!"


async def test_astream_unavailable_raises():
    mgr = _unavailable_manager()
    with pytest.raises(NobodyWhoModelNotFound):
        # model resolution fails before the package import is even attempted
        [e async for e in mgr.astream("nope", [{"role": "user", "content": "x"}])]


# ---------------------------------------------------------------------------
# llm_core integration (SSE protocol)
# ---------------------------------------------------------------------------

async def test_stream_llm_emits_sse(tmp_path, monkeypatch):
    monkeypatch.setenv("NOBODYWHO_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "no-hub-here"))
    _gguf(tmp_path, "TinyChat.gguf")
    mgr = _available_manager()
    import src.nobodywho_provider as nbw_mod
    monkeypatch.setattr(nbw_mod, "manager", mgr)

    chunks = []
    async for chunk in llm_core.stream_llm(
        CANONICAL_URL, "TinyChat",
        [{"role": "user", "content": "stream please"}],
        temperature=0.2,
    ):
        chunks.append(chunk)

    assert chunks[-1] == "data: [DONE]\n\n"
    deltas = []
    usage = None
    for c in chunks[:-1]:
        assert c.startswith("data: ")
        j = json.loads(c[len("data: "):].strip())
        if "delta" in j:
            deltas.append(j["delta"])
        elif j.get("type") == "usage":
            usage = j["data"]
    assert "".join(deltas) == "Hello there!"
    assert usage and usage["output_tokens"] == 4


async def test_stream_llm_unavailable_yields_error_event(monkeypatch):
    mgr = _unavailable_manager()
    import src.nobodywho_provider as nbw_mod
    monkeypatch.setattr(nbw_mod, "manager", mgr)

    chunks = [c async for c in llm_core.stream_llm(
        CANONICAL_URL, "any-model", [{"role": "user", "content": "x"}],
    )]
    assert len(chunks) == 1
    assert chunks[0].startswith("event: error\n")
    payload = json.loads(chunks[0].split("data: ", 1)[1].strip())
    assert payload["status"] == 503
    assert "NobodyWho" in payload["error"]


async def test_llm_call_async_nobodywho(tmp_path, monkeypatch):
    monkeypatch.setenv("NOBODYWHO_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "no-hub-here"))
    _gguf(tmp_path, "TinyChat.gguf")
    mgr = _available_manager()
    import src.nobodywho_provider as nbw_mod
    monkeypatch.setattr(nbw_mod, "manager", mgr)

    result = await llm_core.llm_call_async(
        CANONICAL_URL, "TinyChat",
        [{"role": "user", "content": f"unique-{tmp_path}"}],
    )
    assert result == "Hello there!"


def test_list_model_ids_uses_manager(monkeypatch):
    mgr = _unavailable_manager()
    monkeypatch.setattr(mgr, "list_models", lambda max_age=10.0: ["ModelA", "ModelB"])
    import src.nobodywho_provider as nbw_mod
    monkeypatch.setattr(nbw_mod, "manager", mgr)
    # Avoid DB-cached models interfering
    monkeypatch.setattr(llm_core, "_configured_cached_model_ids", lambda url: [])

    assert llm_core.list_model_ids(CANONICAL_URL) == ["ModelA", "ModelB"]


async def test_stream_llm_early_break_stops_generation_and_releases_lock(tmp_path, monkeypatch):
    """A consumer abandoning the SSE stream (stop button, fallback break) must
    stop the in-flight generation and free the model's generation lock."""
    monkeypatch.setenv("NOBODYWHO_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "no-hub-here"))
    _gguf(tmp_path, "TinyChat.gguf")
    mgr = _available_manager()
    import src.nobodywho_provider as nbw_mod
    monkeypatch.setattr(nbw_mod, "manager", mgr)

    gen = llm_core.stream_llm(CANONICAL_URL, "TinyChat", [{"role": "user", "content": "x"}])
    first = await gen.__anext__()
    assert json.loads(first[len("data: "):].strip())["delta"] == "Hel"
    await gen.aclose()

    lc = mgr._chats[mgr.resolve_source("TinyChat")]
    assert not lc.lock.locked(), "generation lock leaked after early close"
    assert lc.chat.stopped is True, "abandoned generation was not stopped"


async def test_engine_crash_evicts_chat_for_reload(tmp_path, monkeypatch):
    """A worker crash (e.g. chat-template render failure) must evict the dead
    chat so the next request reloads instead of failing until restart."""
    monkeypatch.setenv("NOBODYWHO_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "no-hub-here"))
    _gguf(tmp_path, "TinyChat.gguf")
    mgr = _available_manager()

    async def _boom(_msgs):
        raise RuntimeError("Worker terminated before processing setter: set_chat_history")

    source = mgr.resolve_source("TinyChat")
    # First call loads the chat, then we break it.
    [e async for e in mgr.astream("TinyChat", [{"role": "user", "content": "x"}])]
    lc = mgr._chats[source]
    monkeypatch.setattr(lc.chat, "set_chat_history", _boom)

    with pytest.raises(RuntimeError, match="Worker terminated"):
        [e async for e in mgr.astream("TinyChat", [{"role": "user", "content": "y"}])]

    assert source not in mgr._chats, "dead chat was not evicted"
    assert not lc.lock.locked(), "lock leaked on engine crash"
    # Next request reloads a fresh instance and works again.
    events = [e async for e in mgr.astream("TinyChat", [{"role": "user", "content": "z"}])]
    assert "".join(e["delta"] for e in events if "delta" in e) == "Hello there!"


async def test_acquire_cancel_safe_does_not_leak_lock():
    """Cancelling a task queued on the generation lock must not leave the lock
    held forever once the current holder releases it."""
    import threading
    from src.nobodywho_provider import _acquire_cancel_safe

    lock = threading.Lock()
    lock.acquire()  # someone else is mid-generation

    task = asyncio.ensure_future(_acquire_cancel_safe(lock))
    await asyncio.sleep(0.05)  # let the helper thread block on acquire
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    lock.release()  # the original generation finishes
    # The abandoned helper thread acquires and self-releases; wait for it.
    for _ in range(100):
        if not lock.locked():
            break
        await asyncio.sleep(0.01)
    assert not lock.locked(), "cancelled waiter leaked the lock"


# ---------------------------------------------------------------------------
# Routes: ping / probe / context length
# ---------------------------------------------------------------------------

def test_ping_endpoint_reports_availability(monkeypatch):
    import routes.model_routes as mr

    monkeypatch.setattr(mr, "_nobodywho", _unavailable_manager())
    res = mr._ping_endpoint(CANONICAL_URL)
    assert res["reachable"] is False
    assert "pip install nobodywho" in (res["error"] or "")

    monkeypatch.setattr(mr, "_nobodywho", _available_manager())
    res = mr._ping_endpoint(CANONICAL_URL)
    assert res["reachable"] is True
    assert res["error"] is None


def test_probe_endpoint_lists_local_ggufs(tmp_path, monkeypatch):
    import routes.model_routes as mr

    monkeypatch.setenv("NOBODYWHO_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "no-hub-here"))
    _gguf(tmp_path, "Qwen3-4B-Q4_K_M.gguf")
    monkeypatch.setattr(mr, "_nobodywho", _unavailable_manager())

    assert mr._probe_endpoint(CANONICAL_URL) == ["Qwen3-4B-Q4_K_M"]


def test_get_context_length_uses_configured_ctx(monkeypatch):
    from src.model_context import get_context_length

    monkeypatch.setenv("NOBODYWHO_CTX", "4096")
    assert get_context_length(CANONICAL_URL, "AnyModel") == 4096
