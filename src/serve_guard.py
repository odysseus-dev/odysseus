"""Model-serving safety: decide whether a new serve may proceed.

Pure, side-effect-free helpers so the policy is unit-testable without launching
real model servers. The HTTP serve endpoint (``routes/cookbook_routes.py``)
orchestrates: it reads cookbook state + probes VRAM, then calls these to decide
whether to proceed, stop the previously-loaded model first, or refuse.

Why this exists: serving a model never stopped the previous one and there was no
loaded-model cap, so an agent loop (or a fat-fingered remote tap) could stack
models until the GPU OOM'd and the box fell over. These helpers enforce a
default of ONE loaded model (stop-previous) plus a best-effort free-VRAM
pre-flight.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# Cookbook task statuses that mean a serve is still occupying the GPU. Anything
# else (stopped/error/done/…) is historical and doesn't count toward the cap.
LIVE_STATUSES = frozenset({
    "running", "loading", "warming", "warmup", "ready",
    "starting", "queued", "pending",
})


def live_serves(state: Dict[str, Any], host: str) -> List[Dict[str, Any]]:
    """Serve tasks currently occupying GPU on ``host`` (``""`` == local).

    Reads the cookbook-state task schema written by ``_cookbook_register_task``
    (keys: ``type``, ``status``, ``sessionId``, ``remoteHost``, ``modelId``).
    """
    out: List[Dict[str, Any]] = []
    target = (host or "").strip()
    for t in (state.get("tasks") or []):
        if not isinstance(t, dict):
            continue
        if t.get("type") != "serve":
            continue
        if (t.get("status") or "").lower() not in LIVE_STATUSES:
            continue
        if (t.get("remoteHost") or "").strip() == target:
            out.append(t)
    return out


def decide_serve(running_count: int, max_loaded: int,
                 replaces_previous: bool) -> Tuple[str, str]:
    """Decide the action for a new serve given how many already run on the host.

    Returns one of:
      ("proceed", "")         — under the cap, just serve.
      ("stop_previous", msg)  — cap is 1 and replace-mode: stop the running
                                serve(s) first, then serve.
      ("refuse", msg)         — at/over the cap and not in replace-mode.
    """
    max_loaded = max(1, int(max_loaded or 1))
    if running_count < max_loaded:
        return ("proceed", "")
    if max_loaded == 1 and replaces_previous:
        return ("stop_previous",
                "A model is already loaded; stopping it first (single-model mode).")
    return ("refuse",
            f"Already serving {running_count} model(s) and the limit is "
            f"{max_loaded}. Stop a running model first, or raise "
            f"'max_loaded_models' in Settings (only if your VRAM allows).")


# --- VRAM estimation (best-effort, heuristic) --------------------------------

# Quant hint -> bytes per parameter (weights only). Order matters: more specific
# tokens first so 'q4' isn't shadowed by a generic match.
_QUANT_BYTES = [
    (re.compile(r"\b(fp16|bf16|f16|float16|half)\b", re.I), 2.0),
    (re.compile(r"\b(fp8|q8|int8|8[\s_-]?bit|w8)\b", re.I), 1.0),
    (re.compile(r"\b(q6|6[\s_-]?bit)\b", re.I), 0.75),
    (re.compile(r"\b(q5|5[\s_-]?bit)\b", re.I), 0.65),
    (re.compile(r"\b(q4|int4|4[\s_-]?bit|awq|gptq|w4|nf4)\b", re.I), 0.55),
    (re.compile(r"\b(q3|3[\s_-]?bit)\b", re.I), 0.45),
    (re.compile(r"\b(q2|2[\s_-]?bit)\b", re.I), 0.35),
]
# "<number>B" parameter count, e.g. 7B, 8x7B (MoE — use total), 70b, 1.5B.
_PARAM_RE = re.compile(r"(?:(\d+)\s*[x*]\s*)?(\d+(?:\.\d+)?)\s*b\b", re.I)


def estimate_model_vram_gb(repo_id: str, cmd: str = "") -> Optional[float]:
    """Rough VRAM (GB) a model will need, from its name + serve command.

    Heuristic: parameter count from a '<n>B' token × bytes-per-param from a
    quant hint, plus ~25% for KV cache / activations / framework overhead.
    Returns None when no parameter count is parseable (caller then skips the
    VRAM gate rather than guessing).
    """
    text = f"{repo_id or ''} {cmd or ''}"
    m = _PARAM_RE.search(text)
    if not m:
        return None
    mult = float(m.group(1)) if m.group(1) else 1.0
    params_b = float(m.group(2)) * mult
    if params_b <= 0 or params_b > 2000:   # sanity bound
        return None
    bytes_per_param = 2.0
    for pat, val in _QUANT_BYTES:
        if pat.search(text):
            bytes_per_param = val
            break
    weights_gb = params_b * bytes_per_param
    return round(weights_gb * 1.25, 1)


def parse_free_vram_gb(smi_output: str) -> Optional[float]:
    """Total free VRAM (GB) from `nvidia-smi --query-gpu=memory.free` MiB lines.

    Sums across GPUs (a tensor-parallel serve spans them). Returns None on
    unparseable/empty output.
    """
    if not smi_output:
        return None
    total_mib = 0.0
    found = False
    for line in smi_output.splitlines():
        tok = line.strip().split(",")[0].strip()
        mm = re.match(r"^(\d+(?:\.\d+)?)", tok)
        if mm:
            total_mib += float(mm.group(1))
            found = True
    if not found:
        return None
    return round(total_mib / 1024.0, 1)


def vram_verdict(free_gb: Optional[float], est_gb: Optional[float],
                 headroom_gb: float) -> Tuple[str, str]:
    """("ok"|"refuse"|"skip", message). 'skip' when we can't measure/estimate."""
    if free_gb is None or est_gb is None:
        return ("skip", "")
    need = est_gb + max(0.0, float(headroom_gb or 0))
    if free_gb >= need:
        return ("ok", "")
    return ("refuse",
            f"Not enough free VRAM to load this model safely: it needs about "
            f"{est_gb:.1f} GB plus {headroom_gb:g} GB headroom (~{need:.1f} GB), "
            f"but only {free_gb:.1f} GB is free on the target. Stop a running "
            f"model, pick a smaller/more-quantized model, or lower "
            f"'serve_vram_headroom_gb' in Settings.")
