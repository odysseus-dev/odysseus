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
        self.system_prompt_calls = 0
        self.history = None
        self.history_calls = []
        self.sampler = None
        self.stopped = False
        self.asked = []
        self.tokens_to_yield = ["Hel", "lo", " there", "!"]

    async def set_sampler_config(self, sampler):
        self.sampler = sampler

    async def set_system_prompt(self, system_prompt):
        self.system_prompt = system_prompt
        self.system_prompt_calls += 1

    async def set_chat_history(self, msgs):
        self.history = msgs
        self.history_calls.append(list(msgs))

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
    import time as _time

    mgr = NobodyWhoManager()
    mgr._import_error = "NobodyWho is not installed. Install it with: pip install nobodywho"
    mgr._import_failed_at = _time.time()  # fresh failure — within the retry TTL
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


def test_list_models_finds_symlinked_hf_hub_ggufs(tmp_path, monkeypatch):
    """The HuggingFace cache stores GGUFs as snapshots/<sha>/<name>.gguf
    symlinks pointing at extensionless blobs/<hash> files. Discovery must
    judge the visible name (and use realpath only for dedup) — checking the
    resolved path's extension made every Cookbook download invisible."""
    monkeypatch.setenv("NOBODYWHO_MODELS_DIR", str(tmp_path / "empty"))
    hf_home = tmp_path / "hf"
    monkeypatch.setenv("HF_HOME", str(hf_home))

    repo = hf_home / "hub" / "models--bartowski--Tiny-Coder-GGUF"
    blob = repo / "blobs" / "abc123def456"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"GGUF fake")
    snap = repo / "snapshots" / "8f248fa2"
    snap.mkdir(parents=True)
    (snap / "Tiny-Coder-Q4_K_M.gguf").symlink_to(blob)
    # mmproj symlinks in the same snapshot must still be excluded
    (snap / "mmproj-Tiny-Coder.gguf").symlink_to(blob)

    mgr = _unavailable_manager()
    assert mgr.list_models(max_age=0.0) == ["Tiny-Coder-Q4_K_M"]
    # and the resolved source loads through the symlink path
    assert mgr.resolve_source("Tiny-Coder-Q4_K_M").endswith("Tiny-Coder-Q4_K_M.gguf")


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
    assert chat.system_prompt == "Be nice."
    assert chat.history == [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello!"},
    ]
    assert chat.asked == ["Say hello again"]
    assert chat.sampler == ("temperature", 0.4)
    # generation lock must be released after the stream completes
    assert not mgr._chats[mgr.resolve_source("TinyChat")].lock.locked()


async def test_astream_defaults_system_prompt_when_absent(tmp_path, monkeypatch):
    """Setters must never run against an empty conversation: NobodyWho
    (<= 1.4.0) sync-renders inside setters, and templates that index
    `messages[0]` unguarded (Gemma 4) crash that empty render and kill the
    worker. A request with no system message gets the default system prompt."""
    from src.nobodywho_provider import DEFAULT_SYSTEM_PROMPT

    monkeypatch.setenv("NOBODYWHO_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "no-hub-here"))
    _gguf(tmp_path, "TinyChat.gguf")
    mgr = _available_manager()

    [e async for e in mgr.astream("TinyChat", [{"role": "user", "content": "hi"}])]
    chat = mgr._chats[mgr.resolve_source("TinyChat")].chat
    assert chat.system_prompt == DEFAULT_SYSTEM_PROMPT  # never None on an empty history
    assert chat.history == []
    assert chat.asked == ["hi"]


async def test_system_prompt_sync_is_staged_and_skipped(tmp_path, monkeypatch):
    """set_system_prompt is NobodyWho's only eagerly-syncing setter and its
    render kills the worker on non-renderable states (empty: Gemma/Qwen3;
    system-only: Qwen3.5's "No user query found"). So when it must run, the
    history is staged WITH the user prompt first; and when the system prompt
    is unchanged it must not run at all."""
    monkeypatch.setenv("NOBODYWHO_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "no-hub-here"))
    _gguf(tmp_path, "TinyChat.gguf")
    mgr = _available_manager()

    msgs = [{"role": "user", "content": "first question"}]
    [e async for e in mgr.astream("TinyChat", msgs)]
    chat = mgr._chats[mgr.resolve_source("TinyChat")].chat

    # The sync-triggering setter saw a renderable state: history staged with
    # the user prompt BEFORE set_system_prompt ran, then un-staged.
    assert chat.system_prompt_calls == 1
    assert chat.history_calls[0] == [{"role": "user", "content": "first question"}]
    assert chat.history_calls[-1] == []  # un-staged before ask()

    # Second request, same (default) system prompt: setter skipped entirely.
    [e async for e in mgr.astream("TinyChat", [{"role": "user", "content": "second"}])]
    assert chat.system_prompt_calls == 1

    # Changed system prompt: setter runs again, staged the same way.
    msgs3 = [
        {"role": "system", "content": "Be a pirate."},
        {"role": "user", "content": "third"},
    ]
    [e async for e in mgr.astream("TinyChat", msgs3)]
    assert chat.system_prompt_calls == 2
    assert chat.system_prompt == "Be a pirate."
    assert chat.history_calls[-2] == [{"role": "user", "content": "third"}]  # staged
    assert chat.history_calls[-1] == []  # un-staged


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
    monkeypatch.setattr(llm_core, "_configured_cached_model_ids", lambda url, **kw: [])

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


async def test_switching_models_evicts_before_load(tmp_path, monkeypatch):
    """The resident model must be gone BEFORE the replacement's weights load:
    the loader sizes its GPU offload against free VRAM at load time, so
    load-then-evict commits the new model to CPU layers while the VRAM the
    eviction frees moments later sits idle."""
    monkeypatch.setenv("NOBODYWHO_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "no-hub-here"))
    monkeypatch.delenv("NOBODYWHO_MAX_LOADED_MODELS", raising=False)
    _gguf(tmp_path, "Alpha.gguf")
    _gguf(tmp_path, "Beta.gguf")
    mgr = _available_manager()

    resident_at_load = []

    class _RecordingModel(_FakeModel):
        @staticmethod
        async def load_model_async(model_path, *a, **k):
            resident_at_load.append(
                sorted(os.path.basename(k) for k in mgr._chats)
            )
            return _FakeModel(model_path)

    mgr._mod.Model = _RecordingModel

    [e async for e in mgr.astream("Alpha", [{"role": "user", "content": "x"}])]
    [e async for e in mgr.astream("Beta", [{"role": "user", "content": "x"}])]

    # Alpha was idle, so it must have been evicted before Beta's load ran.
    assert resident_at_load == [[], []]
    assert sorted(os.path.basename(k) for k in mgr._chats) == ["Beta.gguf"]


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
    monkeypatch.setattr(mr, "_nobodywho", _available_manager())

    assert mr._probe_endpoint(CANONICAL_URL) == ["Qwen3-4B-Q4_K_M"]


def test_probe_reports_no_models_when_engine_missing(tmp_path, monkeypatch):
    """GGUFs on disk are unusable without the package — the probe must say
    "no models" so the endpoint pings and surfaces the install offer, instead
    of looking healthy and failing on the first chat."""
    import routes.model_routes as mr

    monkeypatch.setenv("NOBODYWHO_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "no-hub-here"))
    _gguf(tmp_path, "Qwen3-4B-Q4_K_M.gguf")
    monkeypatch.setattr(mr, "_nobodywho", _unavailable_manager())

    assert mr._probe_endpoint(CANONICAL_URL) == []


def _write_gguf(path, kvs, tensors=None):
    """Minimal GGUF v3 writer: header + metadata (uint32 / string / bool
    array) and an optional tensor table.

    ``tensors`` is a list of ``(name, nbytes)`` laid out in order with
    32-byte-aligned offsets and zero-filled data, like the real format — so
    the offset-delta size scan has something true to measure."""
    import struct as _s

    def _str(s):
        b = s.encode()
        return _s.pack("<Q", len(b)) + b

    tensors = tensors or []
    blob = _s.pack("<IIQQ", 0x46554747, 3, len(tensors), len(kvs))
    for key, val in kvs.items():
        blob += _str(key)
        if isinstance(val, str):
            blob += _s.pack("<I", 8) + _str(val)
        elif isinstance(val, list) and val and isinstance(val[0], bool):
            blob += _s.pack("<IIQ", 9, 7, len(val))  # array of bool (SWA pattern)
            blob += b"".join(_s.pack("<?", bool(v)) for v in val)
        elif isinstance(val, list):  # array of uint32 (per-layer head counts etc.)
            blob += _s.pack("<IIQ", 9, 4, len(val))
            blob += b"".join(_s.pack("<I", int(v)) for v in val)
        else:
            blob += _s.pack("<I", 4) + _s.pack("<I", int(val))  # uint32
    offset = 0
    for name, nbytes in tensors:
        blob += _str(name) + _s.pack("<IQIQ", 1, nbytes, 0, offset)  # 1-dim, f32
        last_end = offset + nbytes
        offset = last_end + (-last_end) % 32  # next tensor starts aligned
    if tensors:
        blob += b"\0" * ((-len(blob)) % 32)   # data section starts aligned
        blob += b"\0" * last_end
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)


def test_resolve_n_ctx_auto_sizes_from_header_and_budget(tmp_path, monkeypatch):
    """Auto n_ctx = min(trained max, fits-in-memory, cap): the GGUF header
    gives trained context + real KV shape; the hwfit budget gives memory."""
    import src.nobodywho_provider as nbw

    monkeypatch.delenv("NOBODYWHO_CTX", raising=False)
    monkeypatch.setenv("NOBODYWHO_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "no-hub-here"))
    # 32 layers x 8 kv-heads x 128 head-dim x 2 (K+V) x 2B = 131072 B/token
    _write_gguf(tmp_path / "Dense-32L.gguf", {
        "general.architecture": "llama",
        "llama.context_length": 131072,
        "llama.block_count": 32,
        "llama.attention.head_count": 32,
        "llama.attention.head_count_kv": 8,
        "llama.embedding_length": 4096,
    })
    monkeypatch.setattr(nbw, "_memory_budget_gb", lambda: 9.0)

    mgr = _unavailable_manager()
    src = mgr.resolve_source("Dense-32L")
    # ample budget: fit (~21k here) exceeds the conservative default cap, so
    # the cap binds — the budget is shared unified memory, never ours alone
    assert mgr.resolve_n_ctx(src) == 16384

    # tight budget: 3GB*0.8 - 2GB reserve leaves ~0.4GB; KV may use half of
    # it (~1.6k tokens) — the 2048 floor binds so the chat stays usable
    monkeypatch.setattr(nbw, "_memory_budget_gb", lambda: 3.0)
    mgr2 = _unavailable_manager()
    assert mgr2.resolve_n_ctx(src) == 2048

    # explicit env override wins, clamped to the trained max
    monkeypatch.setenv("NOBODYWHO_CTX", "200000")
    mgr3 = _unavailable_manager()
    assert mgr3.resolve_n_ctx(src) == 131072
    monkeypatch.setenv("NOBODYWHO_CTX", "2048")
    mgr4 = _unavailable_manager()
    assert mgr4.resolve_n_ctx(src) == 2048


def test_resolve_n_ctx_respects_small_trained_max(tmp_path, monkeypatch):
    import src.nobodywho_provider as nbw

    monkeypatch.delenv("NOBODYWHO_CTX", raising=False)
    monkeypatch.setenv("NOBODYWHO_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "no-hub-here"))
    _write_gguf(tmp_path / "Tiny-2k.gguf", {
        "general.architecture": "llama",
        "llama.context_length": 2048,
        "llama.block_count": 12,
        "llama.attention.head_count": 12,
        "llama.embedding_length": 768,
    })
    monkeypatch.setattr(nbw, "_memory_budget_gb", lambda: 18.0)
    mgr = _unavailable_manager()
    # never allocate beyond what the model was trained for
    assert mgr.resolve_n_ctx(mgr.resolve_source("Tiny-2k")) == 2048


def test_kv_cost_sliding_window_and_shared_layers(tmp_path):
    """gemma-3n/4-style headers: only global non-shared layers cost per-token
    KV; sliding-window layers cost a fixed window at their own (narrower)
    head dims; trailing shared-KV layers allocate nothing of their own."""
    import src.nobodywho_provider as nbw

    path = tmp_path / "Swa-42L.gguf"
    _write_gguf(path, {
        "general.architecture": "gemma4",
        "gemma4.context_length": 131072,
        "gemma4.block_count": 42,
        "gemma4.embedding_length": 2560,
        "gemma4.attention.head_count": 8,
        "gemma4.attention.head_count_kv": 2,
        "gemma4.attention.key_length": 512,
        "gemma4.attention.value_length": 512,
        "gemma4.attention.key_length_swa": 256,
        "gemma4.attention.value_length_swa": 256,
        "gemma4.attention.sliding_window": 512,
        # 5 windowed : 1 global, like the real family
        "gemma4.attention.sliding_window_pattern": ([True] * 5 + [False]) * 7,
        "gemma4.attention.shared_kv_layers": 18,
    })
    per_token, fixed = nbw._kv_cache_cost(nbw._gguf_metadata(str(path)))
    # own layers = first 24: 4 global (every 6th) + 20 windowed
    assert per_token == 4 * 2 * (512 + 512) * 2
    assert fixed == 20 * 2 * (256 + 256) * 2 * 512


def test_kv_cost_per_layer_array_fields(tmp_path):
    """gemma-4-12b ships attention.head_count_kv (and friends) as per-layer
    ARRAYS, not scalars — int() on a list crashed resolve_n_ctx and 503'd the
    first chat. Per-layer values must be summed exactly, and any genuinely
    unexpected shape must degrade to None, never raise."""
    import src.nobodywho_provider as nbw

    path = tmp_path / "ArrayHeads.gguf"
    _write_gguf(path, {
        "general.architecture": "gemma4",
        "gemma4.context_length": 131072,
        "gemma4.block_count": 4,
        "gemma4.embedding_length": 1024,
        "gemma4.attention.head_count": 8,                  # head_dim 128
        "gemma4.attention.head_count_kv": [4, 4, 2, 2],    # per-layer array
    })
    per_token, fixed = nbw._kv_cache_cost(nbw._gguf_metadata(str(path)))
    assert per_token == (4 + 4 + 2 + 2) * (128 + 128) * 2
    assert fixed == 0

    # Unexpected shape (string where a number belongs) -> None, not a crash
    assert nbw._kv_cache_cost({
        "general.architecture": "weird",
        "weird.block_count": 2,
        "weird.embedding_length": 64,
        "weird.attention.head_count": 2,
        "weird.attention.head_count_kv": "junk",
    }) is None


def test_kv_cost_periodic_pattern_int_form():
    """The pattern can also be a period (HF convention): every Nth layer is
    global, the rest are windowed at the same dims when no *_swa keys exist."""
    import src.nobodywho_provider as nbw

    per_token, fixed = nbw._kv_cache_cost({
        "general.architecture": "toy",
        "toy.block_count": 8,
        "toy.embedding_length": 512,   # head_dim 128
        "toy.attention.head_count": 4,
        "toy.attention.sliding_window": 1024,
        "toy.attention.sliding_window_pattern": 4,
    })
    assert per_token == 2 * 4 * (128 + 128) * 2          # layers 4 and 8
    assert fixed == 6 * 4 * (128 + 128) * 2 * 1024


def test_resolve_n_ctx_all_swa_layers_unbound_by_memory(tmp_path, monkeypatch):
    """When every own layer is windowed, more context costs no extra KV — a
    tight budget must not collapse n_ctx to the floor; the cap decides."""
    import src.nobodywho_provider as nbw

    monkeypatch.delenv("NOBODYWHO_CTX", raising=False)
    monkeypatch.setenv("NOBODYWHO_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "no-hub-here"))
    _write_gguf(tmp_path / "AllSwa.gguf", {
        "general.architecture": "swaonly",
        "swaonly.context_length": 131072,
        "swaonly.block_count": 16,
        "swaonly.embedding_length": 1024,
        "swaonly.attention.head_count": 8,
        "swaonly.attention.sliding_window": 512,
        "swaonly.attention.sliding_window_pattern": [True] * 16,
    })
    # 3GB budget floors the Dense-32L case above; here it must not bind
    monkeypatch.setattr(nbw, "_memory_budget_gb", lambda: 3.0)
    mgr = _unavailable_manager()
    assert mgr.resolve_n_ctx(mgr.resolve_source("AllSwa")) == 16384


def test_gguf_host_resident_bytes_measures_per_layer_embeddings(tmp_path):
    """Tensor sizes come from offset deltas (no quant math): the scan must
    report exactly the per-layer-embedding slice, and 0 when there is none."""
    import src.nobodywho_provider as nbw

    path = tmp_path / "Ple.gguf"
    _write_gguf(path, {"general.architecture": "gemma4"}, tensors=[
        ("token_embd.weight", 1000),            # pads to 1024
        ("per_layer_token_embd.weight", 4096),  # already aligned
        ("blk.0.attn_q.weight", 333),
    ])
    assert nbw._gguf_host_resident_bytes(str(path)) == 4096

    no_ple = tmp_path / "NoPle.gguf"
    _write_gguf(no_ple, {"general.architecture": "llama"},
                tensors=[("token_embd.weight", 64)])
    assert nbw._gguf_host_resident_bytes(str(no_ple)) == 0


def test_resolve_n_ctx_gates_host_tensor_scan_on_ple_marker(tmp_path, monkeypatch):
    """The full-header tensor walk runs only for archs whose header marks
    per-layer embeddings — everything else keeps the cheap path."""
    import src.nobodywho_provider as nbw

    monkeypatch.delenv("NOBODYWHO_CTX", raising=False)
    monkeypatch.setenv("NOBODYWHO_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "no-hub-here"))
    base = {
        "context_length": 131072,
        "block_count": 32,
        "attention.head_count": 32,
        "attention.head_count_kv": 8,
        "embedding_length": 4096,
    }
    _write_gguf(tmp_path / "Plain.gguf",
                {"general.architecture": "llama",
                 **{f"llama.{k}": v for k, v in base.items()}})
    _write_gguf(tmp_path / "Ple.gguf",
                {"general.architecture": "gemma4",
                 "gemma4.embedding_length_per_layer_input": 256,
                 **{f"gemma4.{k}": v for k, v in base.items()}})
    monkeypatch.setattr(nbw, "_memory_budget_gb", lambda: 9.0)
    scanned = []
    monkeypatch.setattr(nbw, "_gguf_host_resident_bytes",
                        lambda p: scanned.append(p) or 0)

    mgr = _unavailable_manager()
    mgr.resolve_n_ctx(mgr.resolve_source("Plain"))
    assert scanned == []
    mgr.resolve_n_ctx(mgr.resolve_source("Ple"))
    assert [os.path.basename(p) for p in scanned] == ["Ple.gguf"]


def test_reset_import_cache_clears_failure_immediately(monkeypatch):
    """The Cookbook install endpoint resets the cache on success so the very
    next probe sees the package without waiting out the retry TTL."""
    import sys as _sys

    mgr = _unavailable_manager()
    monkeypatch.setitem(_sys.modules, "nobodywho", _FakeNobodyWho())
    assert not mgr.is_available()  # failure still cached
    mgr.reset_import_cache()
    assert mgr.is_available()  # retried right away


def test_failed_import_is_retried_after_ttl(monkeypatch):
    """Cookbook's Dependencies tab can pip-install nobodywho into the running
    interpreter; a permanent negative import cache would keep the endpoint
    saying "not installed" until restart. Failures must expire and retry."""
    import sys as _sys
    import time as _time

    mgr = _unavailable_manager()
    # The package "gets installed" while we're running:
    monkeypatch.setitem(_sys.modules, "nobodywho", _FakeNobodyWho())
    assert not mgr.is_available()  # fresh failure still cached (within TTL)
    mgr._import_failed_at = _time.time() - 60  # age the failure past the TTL
    assert mgr.is_available()  # retried, found the new install
    assert mgr.availability_error() is None


def test_empty_state_hint_is_actionable(monkeypatch):
    """A reachable NobodyWho endpoint with zero models must tell a
    non-technical user what to do next, not just "no models found"."""
    import routes.model_routes as mr

    monkeypatch.setattr(mr, "_nobodywho", _available_manager())
    hint = mr._empty_state_hint(CANONICAL_URL)
    assert hint and "Cookbook" in hint and ".gguf" in hint

    # No hint for other providers (their empty states have other causes)...
    assert mr._empty_state_hint("http://localhost:11434/v1") is None
    # ...and none when the package isn't even installed (the install hint
    # path owns that case).
    monkeypatch.setattr(mr, "_nobodywho", _unavailable_manager())
    assert mr._empty_state_hint(CANONICAL_URL) is None


def test_get_context_length_uses_configured_ctx(monkeypatch):
    from src.model_context import get_context_length

    monkeypatch.setenv("NOBODYWHO_CTX", "4096")
    assert get_context_length(CANONICAL_URL, "AnyModel") == 4096
