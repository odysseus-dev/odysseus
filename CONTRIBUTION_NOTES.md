# Contribution notes — AMD support, honest metrics, smarter search, opt-in sandbox

## What this is

A set of **additive, opt-in-where-it-matters** improvements to Odysseus, focused
on three things real self-hosters keep hitting:

1. **Running well on consumer AMD GPUs** (the Cookbook assumed CUDA/NVIDIA in
   several places).
2. **Telling the truth about performance** (tokens/sec was computed wrong;
   speed/fit estimates were off on unlisted GPUs).
3. **Search and code-execution quality** (literal SearXNG queries; no sandbox
   option for code tools).

Nothing here rewrites Odysseus or changes its look and feel. Every change
extends an existing system (the same `data-ui-key` toggles, the same
`rank_models` filter, the same column-header sort, the same serve-command
builder). New behaviours that could surprise someone are **off by default**.

## Provenance / attribution (please read)

- **Ideas, direction, and testing:** mine. Several of these started as features
  I'd built and tuned privately in my own local-first LLM **router** (a separate
  project that sits in front of an OpenAI-compatible backend) while running
  Odysseus on consumer AMD hardware day to day.
- **What's adapted from that private router work** (now generalised and made
  public here, fitted to Odysseus's architecture rather than copied):
  - LLM-assisted **search query rewrite** (let a small model turn a question into
    a good keyword query).
  - **Auto-search** (let the model decide when to search instead of a manual
    per-message toggle).
  - The **hardened container sandbox** approach for code execution.
- **Implementation:** written with **Claude** (Anthropic) as a pair-programming
  assistant — commits carry a `Co-Authored-By: Claude` trailer to be transparent
  about that.
- **Deliberately NOT ported:** the router-specific machinery (local-first/cloud
  cascade, slot management, budget tracking, OpenWebUI banner hacks). That only
  makes sense as a router and would not be Odysseus-shaped.

If any of this provenance should be surfaced differently for the project's
norms, happy to adjust.

## The changes, grouped

Each bullet is one or more focused commits; they're kept separate so they can be
reviewed (and merged) independently rather than as one big drop.

### Consumer AMD / Cookbook hardware
- **Recommend GGUF/llama.cpp on consumer AMD (RDNA), not vLLM-only AWQ/GPTQ.**
  Detects RDNA (gfx10/11/12) vs datacenter CDNA so it's correct for *all* AMD
  users — only consumer cards are steered to GGUF.
- **Build llama.cpp with Vulkan/ROCm on AMD, not CPU-only.** The from-source
  build only knew CUDA; on AMD it silently built CPU-only. Now auto-detects
  Vulkan (consumer) / ROCm-HIP (datacenter).
- **Accurate speed/fit estimates.** Added missing consumer AMD cards to the
  bandwidth table (they fell back to a crude constant) and model CPU offload
  properly instead of a flat halving.
- **Trending list shows models that actually use the hardware** instead of tiny
  1–3B models (it assumed fp16, so big quantised models looked too large and
  were filtered out).

### Cookbook serve
- **Engine filter** (All / llama.cpp / vLLM / SGLang) + **hardware-computed serve
  profiles** (Quality / Balanced / Speed) with concrete llama.cpp flags
  (n-cpu-moe, KV type, context).
- **Context clamped to the model's trained limit** (and a sane absolute ceiling)
  to prevent an oversized-KV GPU crash.
- **Serve profiles keep an on-disk file's quant fixed** (don't suggest
  re-quantising a file you already have).
- **Discoverable sorting** — the existing clickable column headers now look
  clickable; "Newest" added using the catalog's existing `release_date`.

### Chat quality
- **True tokens/sec** — show the backend's real generation speed
  (`timings.predicted_per_second`) instead of `tokens ÷ wall-clock`, which
  included prefill and read low.
- **Appearance toggles** (off by default): *Always Show Speed* and *Wide Chat*.

### Search
- **Query rewrite** — reformulate a conversational question into a keyword
  search query via the utility model, with a safe fallback to the original.
- **Auto-search (experimental, off by default)** — when on, the model gets the
  `web_search` tool in plain chat and decides itself when to use it.

### Code execution
- **Optional sandboxed bash/python (opt-in, off by default).** With
  `ODYSSEUS_SANDBOX=1` and podman/docker present, code tools run in a hardened
  throwaway container (no network, read-only root, dropped caps, resource caps).
  Falls back to the existing host execution when disabled or no runtime exists.

### Bug fix
- **Fixes #702** — `pip install --user` failing inside a venv. The `--user` flag
  is now decided at runtime in the shell (where the venv is visible) instead of
  from a UI guess.

## Safety / compatibility

- New behaviours that change anything are **off by default**: auto-search, the
  sandbox, and both appearance toggles.
- The search and metrics changes degrade to the prior behaviour on any failure.
- Tests accompany the non-trivial changes (`tests/test_hwfit_amd.py`,
  `test_serve_profiles.py`, `test_search_query_rewrite.py`, `test_auto_search.py`,
  `test_sandbox_exec.py`).

## Suggested PR grouping

To keep PRs small and themed (per CONTRIBUTING), these naturally split into:
1. **Consumer AMD / Cookbook hardware** (filter, Vulkan build, estimates, trending)
2. **Cookbook serve** (engine filter, profiles, ctx clamp, fixed quant, sorting)
3. **Chat quality** (true t/s + appearance toggles)
4. **Search** (query rewrite + auto-search)
5. **Optional sandbox**
6. **Fix #702** (the smallest, most self-contained — a good first PR)
