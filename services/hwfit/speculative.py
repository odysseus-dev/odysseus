"""Speculative decoding draft-model pairing, CLI flag generation, and evaluation.

Investigates compatible draft-model configurations for a target model on detected
hardware (llama.cpp, vLLM, SGLang) and evaluates end-to-end correctness and
time-to-success against live OpenAI-compatible endpoints.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from services.hwfit.models import (
    QUANT_BPP,
    get_models,
    is_prequantized,
    params_b,
)

# Tokenizer/architecture families that share compatible vocabularies for
# lossless speculative decoding.
_FAMILY_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("qwen3", ("qwen3", "qwen-3")),
    ("qwen2.5", ("qwen2.5", "qwen2_5", "qwen2-5")),
    ("qwen2", ("qwen2", "qwen-2")),
    ("llama3", ("llama-3", "llama3", "llama_3")),
    ("llama2", ("llama-2", "llama2", "llama_2")),
    ("gemma4", ("gemma-4", "gemma4", "gemma_4")),
    ("gemma3", ("gemma-3", "gemma3", "gemma_3")),
    ("gemma2", ("gemma-2", "gemma2", "gemma_2")),
    ("deepseek_v3", ("deepseek-v3", "deepseek_v3", "deepseek-r1")),
    ("deepseek_v2", ("deepseek-v2", "deepseek_v2")),
    ("mistral", ("mistral", "mixtral", "ministral")),
    ("phi4", ("phi-4", "phi4", "phi_4")),
    ("phi3", ("phi-3", "phi3", "phi_3")),
    ("smollm", ("smollm",)),
]

DEFAULT_EVAL_PROMPTS: list[dict[str, Any]] = [
    {
        "id": "deterministic_arithmetic",
        "prompt": "Compute 37 * 43 step by step, then end with the exact line: FINAL: 1591",
        "expected_substring": "1591",
    },
    {
        "id": "structured_json",
        "prompt": 'Return ONLY valid JSON with keys "status" (value "ok") and "items" (array [1, 2, 3]).',
        "expect_json": True,
    },
    {
        "id": "code_completion",
        "prompt": "Write a Python function `def fib(n: int) -> int:` that returns the nth Fibonacci number (fib(0)=0, fib(1)=1). Output only code.",
        "expected_substring": "def fib",
    },
]


def detect_model_family(model: dict[str, Any]) -> str:
    """Identify the normalized tokenizer/model family for draft compatibility."""
    text = " ".join(
        str(model.get(k) or "")
        for k in ("name", "id", "architecture", "family")
    ).lower()
    for family, markers in _FAMILY_PATTERNS:
        if any(m in text for m in markers):
            return family
    arch = str(model.get("architecture") or "").strip().lower()
    return arch or "unknown"


def _estimate_model_vram_gb(model: dict[str, Any], quant: str, ctx: int) -> float:
    pb = params_b(model)
    if pb <= 0:
        return 0.0
    bpp = QUANT_BPP.get(quant, 0.58)
    weights = pb * bpp
    kv = 0.000008 * pb * max(int(ctx or 4096), 512)
    return round(weights + kv + 0.35, 2)


def validate_draft_compatibility(
    target_model: dict[str, Any],
    draft_model: dict[str, Any],
) -> dict[str, Any]:
    """Validate whether `draft_model` is a sound speculative draft for `target_model`."""
    reasons: list[str] = []
    warnings: list[str] = []

    target_name = str(target_model.get("name") or target_model.get("id") or "")
    draft_name = str(draft_model.get("name") or draft_model.get("id") or "")
    if target_name and draft_name and target_name == draft_name:
        reasons.append("Draft model must be smaller than and distinct from target model.")

    target_pb = params_b(target_model)
    draft_pb = params_b(draft_model)
    if target_pb <= 0 or draft_pb <= 0:
        reasons.append("Both target and draft models must have valid parameter counts.")
    else:
        ratio = draft_pb / target_pb
        if draft_pb >= target_pb:
            reasons.append(f"Draft ({draft_pb}B) is not smaller than target ({target_pb}B).")
        elif ratio > 0.40:
            reasons.append(
                f"Draft-to-target parameter ratio ({ratio:.2f}) exceeds 0.40; draft overhead outweighs acceptance gains."
            )
        elif ratio > 0.25:
            warnings.append(
                f"Draft-to-target ratio ({ratio:.2f}) is relatively large; optimal ratios are 0.05-0.20."
            )

    target_family = detect_model_family(target_model)
    draft_family = detect_model_family(draft_model)
    if target_family == "unknown" or draft_family == "unknown":
        warnings.append("Unable to confirm exact tokenizer lineage; verify matching vocab_size before production use.")
    elif target_family != draft_family:
        reasons.append(
            f"Tokenizer/family mismatch: target is '{target_family}' while draft is '{draft_family}'."
        )

    return {
        "compatible": len(reasons) == 0,
        "target_family": target_family,
        "draft_family": draft_family,
        "param_ratio": round(draft_pb / target_pb, 4) if target_pb > 0 and draft_pb > 0 else None,
        "reasons": reasons,
        "warnings": warnings,
    }


def build_speculative_cli_flags(
    backend: str,
    target_ref: str,
    draft_ref: str,
    draft_tokens: int = 5,
    gpu_layers: int = 999,
) -> dict[str, Any]:
    """Build backend-specific CLI flags for serving target + draft models."""
    b = (backend or "llamacpp").strip().lower()
    k = max(2, min(int(draft_tokens or 5), 12))
    if b == "vllm":
        args = [
            "vllm", "serve", target_ref,
            "--speculative-model", draft_ref,
            "--num-speculative-tokens", str(k),
            "--speculative-draft-tensor-parallel-size", "1",
        ]
    elif b == "sglang":
        args = [
            "python3", "-m", "sglang.launch_server",
            "--model-path", target_ref,
            "--speculative-draft-model-path", draft_ref,
            "--speculative-num-steps", str(k),
            "--speculative-eagle-topk", "1",
            "--speculative-num-draft-tokens", str(k + 1),
        ]
    else:
        b = "llamacpp"
        args = [
            "llama-server",
            "-m", target_ref,
            "--model-draft", draft_ref,
            "--draft-max", str(k),
            "--draft-min", "1",
            "--draft-p-min", "0.75",
            "-ngl", str(gpu_layers),
            "-ngld", str(gpu_layers),
        ]
    return {
        "backend": b,
        "args": args,
        "command": " ".join(args),
        "draft_tokens": k,
    }


def find_compatible_draft_models(
    target_model: dict[str, Any],
    system: dict[str, Any],
    catalog: list[dict[str, Any]] | None = None,
    backend: str | None = None,
    target_quant: str | None = None,
    draft_quant: str = "Q4_K_M",
    ctx: int = 8192,
) -> dict[str, Any]:
    """Find and rank compatible speculative draft configurations for `target_model`."""
    models = catalog if catalog is not None else (get_models() or [])
    target_pb = params_b(target_model)
    target_family = detect_model_family(target_model)
    sys_backend = (system.get("backend") or "cpu_x86").lower()
    is_windows = (system.get("platform") or "").lower() == "windows"
    apple_silicon = sys_backend in ("mps", "metal", "apple")

    resolved_backend = (backend or "").strip().lower()
    if not resolved_backend:
        if apple_silicon or is_windows:
            resolved_backend = "llamacpp"
        elif sys_backend in ("cuda", "rocm") and is_prequantized(target_model):
            resolved_backend = "vllm"
        else:
            resolved_backend = "llamacpp"

    t_quant = target_quant or target_model.get("quantization") or ("BF16" if resolved_backend in ("vllm", "sglang") else "Q4_K_M")
    d_quant = draft_quant or ("BF16" if resolved_backend in ("vllm", "sglang") else "Q4_K_M")
    target_vram_gb = _estimate_model_vram_gb(target_model, t_quant, ctx)

    has_gpu = bool(system.get("has_gpu"))
    avail_vram = float(system.get("gpu_vram_gb") or 0) if has_gpu else 0.0
    avail_ram = float(system.get("available_ram_gb") or 0)
    budget_gb = avail_vram if avail_vram > 0 else avail_ram

    candidates: list[dict[str, Any]] = []
    for m in models:
        compat = validate_draft_compatibility(target_model, m)
        if not compat["compatible"]:
            continue

        draft_pb = params_b(m)
        ratio = draft_pb / max(target_pb, 0.1)
        draft_vram_gb = _estimate_model_vram_gb(m, d_quant, ctx)
        combined_vram_gb = round(target_vram_gb + draft_vram_gb, 2)
        fits_vram = avail_vram > 0 and combined_vram_gb <= avail_vram
        fits_budget = budget_gb > 0 and combined_vram_gb <= budget_gb

        # Optimal draft size sits around 6%-15% of target parameters
        ideal_ratio = 0.10
        ratio_penalty = abs(ratio - ideal_ratio) * 140.0
        est_acceptance = max(0.45, min(0.88, 0.82 - abs(ratio - 0.12) * 0.9))
        draft_steps = 6 if est_acceptance >= 0.72 else 4

        # Theoretical speedup: (1 + alpha * k) / (1 + k * (draft_cost / target_cost))
        cost_ratio = max(0.03, ratio * 1.15)
        raw_speedup = (1.0 + est_acceptance * draft_steps) / (1.0 + draft_steps * cost_ratio)
        if not fits_vram:
            raw_speedup *= 0.65  # CPU offload severely degrades draft verification latency
        est_speedup = round(max(0.75, min(2.8, raw_speedup)), 2)

        if fits_vram and est_speedup >= 1.45:
            tts_rating = "fast"
        elif fits_budget and est_speedup >= 1.15:
            tts_rating = "moderate"
        else:
            tts_rating = "slower_than_baseline"

        fit_bonus = 25.0 if fits_vram else (5.0 if fits_budget else -30.0)
        pair_score = round(
            max(0.0, min(100.0, 75.0 - ratio_penalty + fit_bonus + (est_speedup - 1.0) * 18.0)),
            1,
        )

        target_ref = str(target_model.get("name") or target_model.get("id") or "target-model")
        draft_ref = str(m.get("name") or m.get("id") or "draft-model")
        cli = build_speculative_cli_flags(resolved_backend, target_ref, draft_ref, draft_tokens=draft_steps)

        candidates.append({
            "draft_model": draft_ref,
            "draft_params_b": round(draft_pb, 2),
            "param_ratio": round(ratio, 4),
            "family": target_family,
            "draft_quant": d_quant,
            "draft_vram_gb": draft_vram_gb,
            "combined_vram_gb": combined_vram_gb,
            "fits_vram": fits_vram,
            "fits_budget": fits_budget,
            "estimated_acceptance_rate": round(est_acceptance, 2),
            "estimated_speedup": est_speedup,
            "time_to_success_rating": tts_rating,
            "score": pair_score,
            "warnings": compat["warnings"] + ([] if fits_vram else ["Combined target + draft exceeds GPU VRAM; offload will limit speedup."]),
            "cli": cli,
        })

    candidates.sort(key=lambda c: (c["fits_vram"], c["score"], c["estimated_speedup"]), reverse=True)
    return {
        "target_model": target_model.get("name") or target_model.get("id"),
        "target_params_b": round(target_pb, 2),
        "target_family": target_family,
        "target_quant": t_quant,
        "target_vram_gb": target_vram_gb,
        "available_vram_gb": avail_vram,
        "backend": resolved_backend,
        "compatible_drafts": candidates,
        "recommended": candidates[0] if candidates else None,
    }


def _check_prompt_correctness(prompt_spec: dict[str, Any], text: str) -> tuple[bool, str]:
    clean = (text or "").strip()
    if not clean:
        return False, "empty completion"
    if prompt_spec.get("expect_json"):
        fenced = re.sub(r"^```(?:json)?\\s*|\\s*```$", "", clean, flags=re.IGNORECASE | re.DOTALL).strip()
        try:
            parsed = json.loads(fenced)
            if isinstance(parsed, dict) and parsed.get("status") == "ok":
                return True, "valid JSON"
            return False, "JSON missing expected keys"
        except Exception as exc:
            return False, f"invalid JSON: {exc}"
    expected = prompt_spec.get("expected_substring")
    if expected and expected not in clean:
        return False, f"missing expected output '{expected}'"
    return True, "ok"


def _run_completion_probe(
    base_url: str,
    model_id: str,
    prompt: str,
    api_key: str | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 128,
    }
    t0 = time.perf_counter()
    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=timeout)
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        if not resp.is_success:
            return {
                "ok": False,
                "error": f"HTTP {resp.status_code}",
                "tts_ms": elapsed_ms,
                "text": "",
                "completion_tokens": 0,
            }
        data = resp.json()
        choices = data.get("choices") or []
        msg = (choices[0].get("message") or {}) if choices else {}
        text = str(msg.get("content") or "")
        usage = data.get("usage") or {}
        comp_tokens = int(usage.get("completion_tokens") or max(1, len(text.split())))
        return {
            "ok": True,
            "error": None,
            "tts_ms": elapsed_ms,
            "text": text,
            "completion_tokens": comp_tokens,
        }
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        return {
            "ok": False,
            "error": str(exc),
            "tts_ms": elapsed_ms,
            "text": "",
            "completion_tokens": 0,
        }


def evaluate_speculative_decoding(
    speculative_base_url: str,
    model_id: str,
    baseline_base_url: str | None = None,
    baseline_model_id: str | None = None,
    api_key: str | None = None,
    prompts: list[dict[str, Any]] | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Evaluate end-to-end correctness and time-to-success of speculative decoding."""
    suite = prompts or DEFAULT_EVAL_PROMPTS
    case_results: list[dict[str, Any]] = []
    passed_count = 0
    spec_tts_list: list[float] = []
    base_tts_list: list[float] = []
    total_tokens = 0
    total_sec = 0.0

    for item in suite:
        prompt_text = str(item.get("prompt") or "")
        spec_run = _run_completion_probe(speculative_base_url, model_id, prompt_text, api_key=api_key, timeout=timeout)
        correct, detail = (
            _check_prompt_correctness(item, spec_run["text"])
            if spec_run["ok"]
            else (False, spec_run["error"] or "request failed")
        )

        base_run = None
        if baseline_base_url:
            base_run = _run_completion_probe(
                baseline_base_url,
                baseline_model_id or model_id,
                prompt_text,
                api_key=api_key,
                timeout=timeout,
            )
            if base_run["ok"] and spec_run["ok"] and not item.get("expect_json") and not item.get("expected_substring"):
                if base_run["text"].strip() != spec_run["text"].strip():
                    correct = False
                    detail = "output diverged from greedy baseline"

        if correct:
            passed_count += 1
        if spec_run["ok"]:
            spec_tts_list.append(spec_run["tts_ms"])
            total_tokens += spec_run["completion_tokens"]
            total_sec += max(spec_run["tts_ms"] / 1000.0, 1e-4)
        if base_run and base_run["ok"]:
            base_tts_list.append(base_run["tts_ms"])

        speedup = (
            round(base_run["tts_ms"] / max(spec_run["tts_ms"], 0.1), 2)
            if base_run and base_run["ok"] and spec_run["ok"]
            else None
        )
        case_results.append({
            "id": item.get("id") or "prompt",
            "passed": correct,
            "detail": detail,
            "speculative_tts_ms": spec_run["tts_ms"],
            "baseline_tts_ms": base_run["tts_ms"] if base_run else None,
            "speedup_ratio": speedup,
            "completion_tokens": spec_run["completion_tokens"],
        })

    total_cases = len(suite)
    pass_rate = round(passed_count / max(total_cases, 1), 3)
    avg_spec_tts = round(sum(spec_tts_list) / len(spec_tts_list), 1) if spec_tts_list else 0.0
    avg_base_tts = round(sum(base_tts_list) / len(base_tts_list), 1) if base_tts_list else None
    overall_speedup = (
        round(avg_base_tts / max(avg_spec_tts, 0.1), 2)
        if avg_base_tts is not None and avg_spec_tts > 0
        else None
    )
    effective_tps = round(total_tokens / total_sec, 1) if total_sec > 0 else 0.0

    return {
        "passed": passed_count == total_cases and total_cases > 0,
        "correctness_pass_rate": pass_rate,
        "passed_cases": passed_count,
        "total_cases": total_cases,
        "speculative_avg_tts_ms": avg_spec_tts,
        "baseline_avg_tts_ms": avg_base_tts,
        "speedup_ratio": overall_speedup,
        "effective_tps": effective_tps,
        "cases": case_results,
    }
