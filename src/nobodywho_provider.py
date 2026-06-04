# src/nobodywho_provider.py
"""In-process NobodyWho provider.

NobodyWho (https://nobodywho.ooo) is an embedded llama.cpp-based inference
library with a Python binding — it has no HTTP server. This module adapts it
to Odysseus' provider plumbing, which is otherwise URL-based: endpoints with
the pseudo base URL ``nobodywho:local`` are routed here instead of httpx.

Key differences from HTTP providers, handled in this module:
  - Chat is *stateful* in NobodyWho (a ``ChatAsync`` keeps its own history and
    KV cache). Odysseus sends the full OpenAI-style message array on every
    request, so each request replays the conversation via
    ``set_system_prompt`` + ``set_chat_history`` and then ``ask()``s the last
    user message. NobodyWho's context shifting keeps the replay cheap when the
    prefix is unchanged.
  - Models are GGUF files, not server-listed IDs. Discovery scans the local
    models directory, NobodyWho's own download cache, and the HuggingFace hub
    cache (where Cookbook downloads land). ``huggingface:owner/repo/file.gguf``
    refs (e.g. pinned models) are passed straight through — NobodyWho
    downloads and caches them on first use.
  - Loaded models are expensive (GB of RAM/VRAM). A small cache keeps the most
    recently used model(s) resident — default 1 — and evicts idle ones when a
    different model is requested, mirroring LM Studio's JIT behaviour.

The ``nobodywho`` package is an optional dependency (requirements-optional.txt)
and is imported lazily so the rest of the app never pays for it.
"""

import asyncio
import gc
import glob
import json
import logging
import os
import struct
import threading
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Canonical pseudo base URL stored on the ModelEndpoint row. Anything with the
# `nobodywho:` scheme is accepted; this is what the UI quick-add fills in.
CANONICAL_URL = "nobodywho:local"

INSTALL_HINT = (
    "NobodyWho is not installed. Install it with: pip install nobodywho "
    "(see requirements-optional.txt), then restart Odysseus."
)

# Shown when the endpoint is healthy but no GGUF files were found — phrased as
# a next step a non-technical user can actually follow (Cookbook downloads
# land in the HuggingFace cache, which this provider scans automatically).
EMPTY_MODELS_HINT = (
    "no models yet. Open Cookbook and download one — it shows up here "
    "automatically. (Or drop a .gguf file into data/models.)"
)

# Filename markers for GGUFs that are not chat models (multimodal projectors).
_NON_CHAT_GGUF_MARKERS = ("mmproj", "projector")

_DEFAULT_N_CTX = 8192

# Used when a request carries no system message. NobodyWho (<= 1.4.0)
# sync-renders the chat template inside setters (set_system_prompt,
# set_chat_history) even while the conversation is still empty, and templates
# that index `messages[0]` without a guard — e.g. Gemma 4's, line 179 — fail
# that empty render with a minijinja "undefined value" error, which kills the
# worker thread. A non-empty system prompt guarantees every setter sees at
# least one message, so the empty render can never happen.
DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."


def is_nobodywho_url(url: str) -> bool:
    """True when a configured endpoint URL routes to the in-process provider."""
    if not url:
        return False
    try:
        return (urlparse(url.strip()).scheme or "").lower() == "nobodywho"
    except Exception:
        return False


def _models_dir() -> str:
    override = os.getenv("NOBODYWHO_MODELS_DIR", "").strip()
    if override:
        return override
    try:
        from core.constants import DATA_DIR
        return os.path.join(DATA_DIR, "models")
    except Exception:
        return os.path.join("data", "models")


def _hf_hub_dir() -> str:
    hf_home = os.getenv("HF_HOME", "").strip() or os.path.join(
        os.path.expanduser("~"), ".cache", "huggingface"
    )
    return os.path.join(hf_home, "hub")


def configured_n_ctx() -> int:
    """Context window allocated per loaded chat (``NOBODYWHO_CTX`` env)."""
    try:
        val = int(os.getenv("NOBODYWHO_CTX", "").strip() or _DEFAULT_N_CTX)
        return max(512, val)
    except Exception:
        return _DEFAULT_N_CTX


# ── GGUF header metadata (for per-model context sizing) ──

_GGUF_SCALARS = {
    0: ("<B", 1), 1: ("<b", 1), 2: ("<H", 2), 3: ("<h", 2), 4: ("<I", 4),
    5: ("<i", 4), 6: ("<f", 4), 7: ("<?", 1), 10: ("<Q", 8), 11: ("<q", 8),
    12: ("<d", 8),
}


def _gguf_metadata(path: str, max_keys: int = 256) -> Dict[str, Any]:
    """Read scalar key/values from a GGUF header (architecture, context
    length, attention shape). Cheap: stops at the tokenizer section or after
    ``max_keys`` — the keys we need come first by convention."""
    meta: Dict[str, Any] = {}
    try:
        with open(path, "rb") as f:
            magic, version, _n_tensors, n_kv = struct.unpack("<IIQQ", f.read(24))
            if magic != 0x46554747 or version < 2:  # b"GGUF", v2+ (u64 counts)
                return meta

            def rd_str() -> str:
                (n,) = struct.unpack("<Q", f.read(8))
                return f.read(n).decode("utf-8", errors="replace")

            def rd_val(t: int):
                if t in _GGUF_SCALARS:
                    fmt, size = _GGUF_SCALARS[t]
                    return struct.unpack(fmt, f.read(size))[0]
                if t == 8:
                    return rd_str()
                if t == 9:  # array: parse through small ones, bail on huge
                    (et,) = struct.unpack("<I", f.read(4))
                    (n,) = struct.unpack("<Q", f.read(8))
                    if n > 4096:
                        raise StopIteration  # vocab etc. — past what we need
                    return [rd_val(et) for _ in range(n)]
                raise StopIteration  # unknown type — stop cleanly

            for _ in range(min(n_kv, max_keys)):
                key = rd_str()
                if key.startswith("tokenizer."):
                    break  # everything we care about precedes the tokenizer
                (t,) = struct.unpack("<I", f.read(4))
                meta[key] = rd_val(t)
    except StopIteration:
        pass
    except Exception as e:
        logger.debug(f"GGUF metadata read failed for {path}: {e}")
    return meta


def _kv_bytes_per_token(meta: Dict[str, Any]) -> Optional[int]:
    """Bytes of KV cache one context token costs, from real header shape.

    Standard attention: K+V, f16, per layer: 2 * n_kv_heads * head_dim * 2.
    deepseek2 (MLA) caches a compressed latent instead — far smaller.
    Returns None when the arch keys aren't recognized (caller falls back).
    """
    arch = meta.get("general.architecture")
    if not arch:
        return None

    def g(key: str, default=None):
        return meta.get(f"{arch}.{key}", default)

    n_layer = g("block_count")
    if not n_layer:
        return None
    if arch == "deepseek2":
        rank = g("attention.kv_lora_rank")
        rope = g("rope.dimension_count") or 0
        if rank:
            return int(n_layer) * (int(rank) + int(rope)) * 2  # f16 latent
        return None
    n_head = g("attention.head_count")
    emb = g("embedding_length")
    if not (n_head and emb):
        return None
    n_kv_head = g("attention.head_count_kv") or n_head
    head_dim = g("attention.key_length") or (int(emb) // int(n_head))
    return 2 * int(n_layer) * int(n_kv_head) * int(head_dim) * 2


def _memory_budget_gb() -> Optional[float]:
    """Usable model memory on this machine — the same number Cookbook's fit
    estimates use (services/hwfit), so both surfaces agree by construction."""
    try:
        from services.hwfit.hardware import detect_system
        system = detect_system()
        vram = max((g.get("vram_gb") or 0) for g in system.get("gpus") or [{}])
        if vram:
            return float(vram)
        ram = system.get("ram_gb")
        if ram:
            return float(ram) * 0.6  # CPU-only: leave room for the OS
    except Exception as e:
        logger.debug(f"hardware detection unavailable: {e}")
    return None


def _is_remote_ref(model_id: str) -> bool:
    """Refs NobodyWho resolves itself (downloads + caches on first use)."""
    mid = (model_id or "").strip()
    return mid.startswith(("huggingface:", "hf://", "https://", "http://"))


async def _acquire_cancel_safe(lock: threading.Lock) -> None:
    """Acquire a threading.Lock from async code without leaking it on cancel.

    ``asyncio.to_thread(lock.acquire)`` alone is unsafe: if the awaiting task
    is cancelled while queued, the helper thread still completes the acquire
    with nobody left to release — wedging the model for the process lifetime.
    The shared ``state`` dict closes that window: whichever side loses the
    race (the cancelled awaiter or the acquiring thread) releases the lock.
    """
    state: Dict[str, bool] = {}

    def _acquire():
        lock.acquire()
        if state.get("abandoned"):
            lock.release()
        else:
            state["acquired"] = True

    try:
        await asyncio.to_thread(_acquire)
    except BaseException:
        state["abandoned"] = True
        if state.get("acquired"):
            lock.release()
        raise


class NobodyWhoUnavailable(RuntimeError):
    """The optional `nobodywho` package is not importable."""


class NobodyWhoModelNotFound(RuntimeError):
    """The requested model ID matches no known GGUF file or remote ref."""


class _LoadedChat:
    """A resident ChatAsync plus the lock that serializes generations on it."""

    __slots__ = ("chat", "lock", "source", "last_used", "applied_system")

    def __init__(self, chat, source: str):
        self.chat = chat
        self.source = source
        # System prompt last applied to this chat (None = fresh chat, none set).
        # Lets astream skip set_system_prompt when unchanged — that setter is
        # the only one that sync-renders eagerly in NobodyWho <= 1.4.0, and its
        # render crashes the worker on strict templates (see astream).
        self.applied_system = None
        # threading.Lock (not asyncio.Lock): generations are awaited from the
        # main event loop, but sync utility calls (llm_call in FastAPI's
        # threadpool) run under their own ad-hoc loop via asyncio.run(). A
        # thread lock is loop-agnostic; async paths acquire it off-loop with
        # asyncio.to_thread so the event loop never blocks on it.
        self.lock = threading.Lock()
        self.last_used = time.time()


class NobodyWhoManager:
    def __init__(self):
        self._mod = None
        self._import_error: Optional[str] = None
        self._import_failed_at: float = 0.0
        self._registry_lock = threading.Lock()
        self._chats: Dict[str, _LoadedChat] = {}
        # Per-source load locks so two requests for the same model don't load
        # it twice (double the RAM), while loads of different models and
        # list_models() stay unblocked.
        self._load_locks: Dict[str, threading.Lock] = {}
        # model id -> absolute gguf path, rebuilt by list_models()
        self._id_to_path: Dict[str, str] = {}
        self._scan_cache: Tuple[float, List[str]] = (0.0, [])
        # source -> (file signature, resolved n_ctx)
        self._n_ctx_cache: Dict[str, Tuple[Tuple[int, int], int]] = {}

    # ── availability ──

    def _import(self):
        """Import nobodywho; cache success forever, failure only briefly.

        The failure cache must expire: Cookbook's Dependencies tab can pip
        install the package into this very interpreter while it runs, and a
        permanent negative cache would keep reporting "not installed" until a
        restart. A failed import attempt is cheap, so retry every 15s.
        """
        if self._mod is not None:
            return self._mod
        if self._import_error is not None:
            if time.time() - self._import_failed_at < 15.0:
                raise NobodyWhoUnavailable(self._import_error)
            self._import_error = None  # may have just been installed — retry
        try:
            import nobodywho  # noqa: PLC0415 — heavy native lib, lazy on purpose
            self._mod = nobodywho
            return nobodywho
        except Exception as e:  # ImportError or native-lib load failure
            self._import_error = f"{INSTALL_HINT} ({type(e).__name__}: {e})"
            self._import_failed_at = time.time()
            raise NobodyWhoUnavailable(self._import_error)

    def is_available(self) -> bool:
        try:
            self._import()
            return True
        except NobodyWhoUnavailable:
            return False

    def availability_error(self) -> Optional[str]:
        if self.is_available():
            return None
        return self._import_error or INSTALL_HINT

    def ping(self) -> Dict[str, object]:
        """Reachability shaped like model_routes._ping_endpoint results."""
        if self.is_available():
            return {"reachable": True, "status_code": 200, "error": None}
        return {"reachable": False, "status_code": None, "error": self.availability_error()}

    # ── model discovery ──

    def list_models(self, max_age: float = 10.0) -> List[str]:
        """Model IDs from local GGUF files (cached briefly — called per UI refresh)."""
        now = time.time()
        cached_at, cached = self._scan_cache
        if cached and (now - cached_at) < max_age:
            return list(cached)

        paths: List[str] = []
        seen = set()

        def _add(path: str):
            # Judge the VISIBLE name, then resolve. In the HuggingFace cache
            # the .gguf under snapshots/ is a symlink to an extensionless
            # blobs/<hash> file — realpath-first made every hub model invisible.
            base = os.path.basename(path).lower()
            if not base.endswith(".gguf"):
                return
            if any(m in base for m in _NON_CHAT_GGUF_MARKERS):
                return  # vision projectors etc. — not standalone chat models
            try:
                real = os.path.realpath(path)  # dedup key only
            except Exception:
                return
            if real in seen or not os.path.isfile(real):
                return
            seen.add(real)
            paths.append(path)  # keep the symlink path — its stem is the model name

        # 1. The Odysseus-managed models directory (user drops GGUFs here).
        models_dir = _models_dir()
        if os.path.isdir(models_dir):
            for p in glob.glob(os.path.join(models_dir, "**", "*.gguf"), recursive=True):
                _add(p)

        # 2. NobodyWho's own download cache (populated by huggingface:/URL refs).
        try:
            mod = self._import()
            for path, _size in mod.get_cached_models():
                _add(str(path))
        except NobodyWhoUnavailable:
            pass
        except Exception as e:
            logger.debug(f"nobodywho.get_cached_models failed: {e}")

        # 3. The HuggingFace hub cache — Cookbook downloads land here. Walk
        # instead of glob: the files are symlinks and we only want resolved
        # snapshot trees (blobs/ holds the same data under hash names).
        hub = _hf_hub_dir()
        if os.path.isdir(hub):
            for root, _dirs, files in os.walk(hub):
                if f"{os.sep}snapshots" not in root:
                    continue
                for fname in files:
                    _add(os.path.join(root, fname))

        # Stable, human-friendly IDs: the file stem; disambiguate duplicates
        # with their parent directory.
        id_to_path: Dict[str, str] = {}
        stems: Dict[str, int] = {}
        for p in paths:
            stem = os.path.splitext(os.path.basename(p))[0]
            stems[stem] = stems.get(stem, 0) + 1
        for p in sorted(paths):
            stem = os.path.splitext(os.path.basename(p))[0]
            mid = stem
            if stems[stem] > 1:
                mid = f"{os.path.basename(os.path.dirname(p))}/{stem}"
            if mid in id_to_path:  # same dir-qualified name twice — last wins
                mid = p
            id_to_path[mid] = p

        with self._registry_lock:
            self._id_to_path = id_to_path
            self._scan_cache = (now, list(id_to_path.keys()))
        return list(id_to_path.keys())

    def resolve_source(self, model_id: str) -> str:
        """Map a model ID to something ChatAsync accepts (path or remote ref)."""
        mid = (model_id or "").strip()
        if not mid:
            raise NobodyWhoModelNotFound("No model specified for the NobodyWho endpoint")
        if _is_remote_ref(mid):
            return mid
        if os.path.isfile(mid) and mid.lower().endswith(".gguf"):
            return os.path.realpath(mid)
        with self._registry_lock:
            path = self._id_to_path.get(mid)
        if path is None:
            self.list_models(max_age=0.0)  # rescan, the file may be new
            with self._registry_lock:
                path = self._id_to_path.get(mid)
        if path is None:
            # Bare-stem match (normalize_model_id may hand us a basename)
            with self._registry_lock:
                for known, p in self._id_to_path.items():
                    if known.split("/")[-1] == mid.split("/")[-1]:
                        return p
            raise NobodyWhoModelNotFound(
                f"NobodyWho model '{mid}' not found. Put the .gguf in {_models_dir()} "
                "or pin a 'huggingface:owner/repo/file.gguf' ref on the endpoint."
            )
        return path

    # ── context sizing ──

    def resolve_n_ctx(self, source: str) -> int:
        """Context window to allocate for ``source``.

        NOBODYWHO_CTX set explicitly  -> use it (clamped to the trained max)
        unset                         -> min(trained max, fits-in-memory, cap)
        no local file / unknown arch  -> conservative default

        "fits-in-memory" inverts Cookbook's estimate with better inputs: real
        weight size from disk, real KV bytes/token from the GGUF header, and
        the same hardware budget services/hwfit reports — so the number here
        matches what Cookbook promised for this machine.
        """
        explicit = os.getenv("NOBODYWHO_CTX", "").strip()
        is_local = os.path.isfile(source)
        sig = (0, 0)
        if is_local:
            try:
                st = os.stat(source)
                sig = (int(st.st_size), int(st.st_mtime))
            except Exception:
                is_local = False
        cached = self._n_ctx_cache.get(source)
        if cached and cached[0] == sig:
            return cached[1]

        meta = _gguf_metadata(source) if is_local else {}
        arch = meta.get("general.architecture")
        trained = meta.get(f"{arch}.context_length") if arch else None

        if explicit:
            n_ctx = configured_n_ctx()
            if trained:
                n_ctx = min(n_ctx, int(trained))
        else:
            try:
                cap = max(2048, int(os.getenv("NOBODYWHO_MAX_CTX", "16384")))
            except Exception:
                cap = 16384
            candidates = [cap]
            if trained:
                candidates.append(int(trained))
            kv_per_token = _kv_bytes_per_token(meta)
            budget_gb = _memory_budget_gb() if kv_per_token else None
            if kv_per_token and budget_gb:
                # The budget is a CEILING, not free memory — on unified-memory
                # machines it's the same pool the OS, browser, and everything
                # else live in, and model memory is wired (unswappable).
                # Treating the ceiling as ours alone can freeze the whole
                # host. Margins, in order: take 80% of the ceiling, subtract
                # the weights, subtract a 2GB reserve for llama.cpp compute
                # buffers + the app, then let the KV cache use at most HALF
                # of whatever survives.
                usable = budget_gb * 0.8 * (1 << 30)
                free = usable - sig[0] - 2 * (1 << 30)
                kv_budget = max(0.0, free * 0.5)
                # Floor at 2048 (a tiny KV) so a barely-fitting model still
                # chats; we never inflate beyond that when memory says no.
                candidates.append(max(2048, int(kv_budget // kv_per_token)))
            elif not trained:
                candidates.append(_DEFAULT_N_CTX)  # nothing known — stay safe
            n_ctx = min(candidates)

        self._n_ctx_cache[source] = (sig, n_ctx)
        return n_ctx

    def context_length(self, model_id: str) -> int:
        """Per-model context for UI/budgeting — the allocation, honestly."""
        try:
            return self.resolve_n_ctx(self.resolve_source(model_id))
        except Exception:
            return configured_n_ctx()

    # ── chat lifecycle ──

    def _max_loaded(self) -> int:
        try:
            return max(1, int(os.getenv("NOBODYWHO_MAX_LOADED_MODELS", "1")))
        except Exception:
            return 1

    def _drop_chat(self, source: str) -> None:
        """Evict a chat whose worker may have died (e.g. template render crash)
        so the next request reloads a fresh instance instead of reusing a
        corpse that fails every call until restart."""
        with self._registry_lock:
            dropped = self._chats.pop(source, None) is not None
        if dropped:
            gc.collect()
            logger.warning(f"NobodyWho: dropped failed chat for {source}; will reload on next use")

    def _evict_idle(self, keep: str) -> None:
        """Drop least-recently-used resident chats beyond the cap (best effort)."""
        with self._registry_lock:
            candidates = sorted(
                (k for k in self._chats if k != keep),
                key=lambda k: self._chats[k].last_used,
            )
            excess = len(self._chats) - self._max_loaded()
            evicted = []
            for k in candidates:
                if excess <= 0:
                    break
                lc = self._chats[k]
                if lc.lock.locked():
                    continue  # mid-generation — never yank a busy model
                del self._chats[k]
                evicted.append(k)
                excess -= 1
        if evicted:
            gc.collect()  # release the model weights' refcounted buffers
            logger.info(f"NobodyWho: evicted idle model(s): {evicted}")

    async def _get_chat(self, source: str) -> _LoadedChat:
        with self._registry_lock:
            lc = self._chats.get(source)
            if lc is not None:
                lc.last_used = time.time()
                return lc
            load_lock = self._load_locks.setdefault(source, threading.Lock())

        await _acquire_cancel_safe(load_lock)
        try:
            with self._registry_lock:
                lc = self._chats.get(source)
                if lc is not None:
                    lc.last_used = time.time()
                    return lc
            mod = self._import()
            n_ctx = self.resolve_n_ctx(source)
            use_gpu = os.getenv("NOBODYWHO_USE_GPU", "1").strip().lower() not in ("0", "false", "no")
            logger.info(f"NobodyWho: loading model {source} (n_ctx={n_ctx}, gpu={use_gpu})")
            t0 = time.time()
            model = await mod.Model.load_model_async(source, use_gpu)
            # The constructor allocates the context — keep it off the event loop.
            chat = await asyncio.to_thread(mod.ChatAsync, model, n_ctx)
            logger.info(f"NobodyWho: model loaded in {time.time() - t0:.1f}s")
            lc = _LoadedChat(chat, source)
            with self._registry_lock:
                self._chats[source] = lc
        finally:
            load_lock.release()
        self._evict_idle(keep=source)
        return lc

    # ── request → conversation mapping ──

    @staticmethod
    def _content_text(content) -> Tuple[str, int]:
        """Flatten message content to text; returns (text, dropped_image_count)."""
        if isinstance(content, str):
            return content, 0
        if isinstance(content, list):
            parts, dropped = [], 0
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
                elif block.get("type") == "image_url":
                    dropped += 1
            return "\n".join(p for p in parts if p), dropped
        return ("" if content is None else str(content)), 0

    @classmethod
    def prepare_conversation(cls, messages: List[Dict]) -> Tuple[Optional[str], List[Dict], str]:
        """Convert OpenAI-style messages to (system_prompt, history, prompt).

        ``history`` is NobodyWho ``set_chat_history`` format: user/assistant
        dicts with plain-string content. Tool messages and assistant tool_calls
        (possible after compaction of old agent sessions) are folded into text;
        consecutive same-role messages are merged because llama.cpp chat
        templates commonly require strict alternation. The trailing user
        message becomes the ``ask()`` prompt.
        """
        system_parts: List[str] = []
        flat: List[Dict[str, str]] = []
        dropped_images = 0

        for m in messages or []:
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            text, dropped = cls._content_text(m.get("content"))
            dropped_images += dropped
            if role == "system":
                if text:
                    system_parts.append(text)
                continue
            if role == "tool":
                role, text = "user", f"[Tool result]\n{text}"
            elif role == "assistant" and m.get("tool_calls"):
                calls = []
                for tc in m.get("tool_calls") or []:
                    fn = (tc or {}).get("function") or {}
                    args = fn.get("arguments")
                    if not isinstance(args, str):
                        try:
                            args = json.dumps(args or {})
                        except Exception:
                            args = "{}"
                    calls.append(f"{fn.get('name', 'tool')}({args})")
                text = "\n".join(filter(None, [text, "[Called: " + "; ".join(calls) + "]"]))
            if role not in ("user", "assistant"):
                continue
            if not text:
                continue
            if flat and flat[-1]["role"] == role:
                flat[-1]["content"] += "\n\n" + text
            else:
                flat.append({"role": role, "content": text})

        if dropped_images:
            logger.info(
                f"NobodyWho: dropped {dropped_images} image attachment(s) — "
                "multimodal input is not wired up for this provider yet"
            )

        if flat and flat[-1]["role"] == "user":
            prompt = flat.pop()["content"]
        else:
            # No trailing user turn (rare: regenerate edge cases). Keep what we
            # have as history and nudge the model to carry on.
            prompt = "Continue."
            logger.debug("NobodyWho: no trailing user message; asking model to continue")

        system_text = "\n\n".join(system_parts) if system_parts else None
        return system_text, flat, prompt

    def _sampler_for(self, temperature: Optional[float]):
        mod = self._import()
        if temperature is None or abs(float(temperature) - 1.0) < 1e-6:
            return mod.SamplerPresets.default()
        # NobodyWho clamps internally; keep the OpenAI-style 0..2 value as-is.
        return mod.SamplerPresets.temperature(max(0.0, float(temperature)))

    # ── generation ──

    async def astream(
        self,
        model_id: str,
        messages: List[Dict],
        temperature: Optional[float] = None,
        max_tokens: int = 0,
    ) -> AsyncIterator[Dict]:
        """Token events for one chat turn.

        Yields ``{"delta": str}`` per token, then a final
        ``{"usage": {...}}`` summary. Errors raise; the caller formats SSE.
        """
        source = self.resolve_source(model_id)
        lc = await self._get_chat(source)

        await _acquire_cancel_safe(lc.lock)
        stream = None
        finished = False
        n_tokens = 0
        t_first: Optional[float] = None
        try:
            lc.last_used = time.time()
            system_text, history, prompt = self.prepare_conversation(messages)
            if not system_text:
                system_text = DEFAULT_SYSTEM_PROMPT
            chat = lc.chat
            await chat.set_sampler_config(self._sampler_for(temperature))

            # NobodyWho (<= 1.4.0) sync-renders the chat template inside
            # set_system_prompt — and that render kills the worker thread when
            # the conversation state isn't renderable: empty conversations
            # break templates that index messages[0] (Gemma 3/4, Qwen3), and
            # system-only conversations break templates that require a user
            # message (Qwen3.5 raises "No user query found in messages").
            # set_chat_history never syncs, so the safe sequence is:
            #   1. skip set_system_prompt entirely when unchanged (common case)
            #   2. when it must run, stage history WITH the user prompt first,
            #      so it renders [system, ...history, user] — acceptable to
            #      every template family, and the prefill is exactly the state
            #      ask() renders next (no wasted work)
            if system_text != lc.applied_system:
                await chat.set_chat_history(
                    history + [{"role": "user", "content": prompt}]
                )
                await chat.set_system_prompt(system_text)
                lc.applied_system = system_text
            # Un-stage / set the real history; ask() re-appends the prompt.
            await chat.set_chat_history(history)

            stream = chat.ask(prompt)
            stopped = False
            while True:
                token = await stream.next_token()
                if token is None:
                    break
                if t_first is None:
                    t_first = time.time()
                n_tokens += 1
                if not stopped:
                    yield {"delta": token}
                if max_tokens and max_tokens > 0 and n_tokens >= max_tokens and not stopped:
                    # NobodyWho has no num_predict; enforce the cap ourselves
                    # and drain the (now stopping) stream.
                    stopped = True
                    await chat.stop_generation()
            finished = True

            elapsed = (time.time() - t_first) if t_first else 0.0
            est_input = sum(
                4 + int(len(m.get("content") or "") * 0.3) for m in (history or [])
            ) + int(len(prompt) * 0.3) + (int(len(system_text) * 0.3) if system_text else 0)
            usage = {"input_tokens": est_input, "output_tokens": n_tokens, "estimated": True}
            if n_tokens > 1 and elapsed > 0:
                usage["gen_tps"] = round((n_tokens - 1) / elapsed, 2)
            yield {"usage": usage}
        except (GeneratorExit, asyncio.CancelledError):
            raise  # consumer went away — the chat itself is healthy
        except BaseException:
            # Engine-side failure (e.g. a chat-template render crash kills the
            # worker thread). The instance is unusable; drop it so the next
            # request reloads instead of failing forever.
            self._drop_chat(source)
            raise
        finally:
            if stream is not None and not finished:
                # Client disconnect / generator close / error mid-stream: stop
                # the worker so it doesn't burn GPU on an abandoned generation.
                try:
                    await lc.chat.stop_generation()
                except Exception:
                    pass
            lc.lock.release()

    async def acomplete(
        self,
        model_id: str,
        messages: List[Dict],
        temperature: Optional[float] = None,
        max_tokens: int = 0,
    ) -> str:
        parts: List[str] = []
        async for event in self.astream(model_id, messages, temperature, max_tokens):
            if "delta" in event:
                parts.append(event["delta"])
        return "".join(parts)

    def complete_sync(
        self,
        model_id: str,
        messages: List[Dict],
        temperature: Optional[float] = None,
        max_tokens: int = 0,
    ) -> str:
        """Blocking variant for sync callers (FastAPI threadpool utility calls)."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.acomplete(model_id, messages, temperature, max_tokens))
        raise RuntimeError(
            "complete_sync() called from the event loop — use acomplete() instead"
        )


manager = NobodyWhoManager()
