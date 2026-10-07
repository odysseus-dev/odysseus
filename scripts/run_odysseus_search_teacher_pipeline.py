#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib import request


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SFT_DIR = (Path(os.environ["ODYSSEUS_SFT_DIR"]) if os.environ.get("ODYSSEUS_SFT_DIR") else None)
DEFAULT_RUN_ROOT = REPO_ROOT / "data/evals/ody_search_teacher_pipeline_20260821"

SOURCE_DUMP_RE = re.compile(
    r"WEB SEARCH RESULTS|SEARCH RESULTS SUMMARY|```sources|\b\d+\s+Web sources\b|Here are links",
    re.IGNORECASE,
)
META_FINAL_RE = re.compile(
    r"\b(the user (asked|is asking|wants)|tool evidence|search result|according to the snippets|i should answer)\b",
    re.IGNORECASE,
)

WEB_TOOLS = {"web_search", "web_fetch"}
SFT_TOOL_OUTPUT_MAX_CHARS = 2400

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the public web for source-backed information.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch a specific URL when search snippets do not contain enough evidence.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
]


def stable_id(prefix: str, obj: dict[str, Any]) -> str:
    payload = json.dumps(obj, sort_keys=True, ensure_ascii=True)
    return prefix + "_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def db_deepseek_endpoint() -> dict[str, str]:
    env_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if env_key:
        return {
            "id": os.environ.get("DEEPSEEK_ENDPOINT_ID", "e17d4b33"),
            "name": "DeepSeek",
            "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/"),
            "api_key": env_key,
            "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        }
    conn = sqlite3.connect(str(REPO_ROOT / "data/app.db"))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, name, base_url, api_key, cached_models
            FROM model_endpoints
            WHERE (
                lower(name) LIKE '%deepseek%'
                OR lower(base_url) LIKE '%deepseek%'
                OR lower(cached_models) LIKE '%deepseek%'
            )
              AND COALESCE(is_enabled, 0) = 1
              AND COALESCE(api_key, '') != ''
            ORDER BY updated_at DESC
            """
        ).fetchall()
        if not rows:
            raise RuntimeError("No enabled DeepSeek endpoint with an API key in data/app.db")
        row = rows[0]
        model = "deepseek-v4-flash"
        try:
            cached = json.loads(row["cached_models"] or "[]")
            if isinstance(cached, list) and "deepseek-v4-flash" in cached:
                model = "deepseek-v4-flash"
            elif isinstance(cached, list) and "deepseek/deepseek-v4-flash" in cached:
                model = "deepseek/deepseek-v4-flash"
            elif isinstance(cached, list) and "deepseek/deepseek-chat" in cached:
                model = "deepseek/deepseek-chat"
            elif isinstance(cached, list) and cached:
                deepseek_model = next((str(m) for m in cached if "deepseek" in str(m).lower()), "")
                model = deepseek_model or str(cached[0])
        except Exception:
            pass
        return {
            "id": str(row["id"]),
            "name": str(row["name"]),
            "base_url": str(row["base_url"]).rstrip("/"),
            "api_key": str(row["api_key"]),
            "model": model,
        }
    finally:
        conn.close()


def call_deepseek_json(
    endpoint: dict[str, str],
    payload: dict[str, Any],
    *,
    max_tokens: int = 8000,
    temperature: float = 0.7,
    json_mode: bool = False,
) -> dict[str, Any]:
    last_error = ""
    parsed: dict[str, Any] = {}
    text = ""
    for attempt in range(1, 5):
        body = {
            "model": endpoint["model"],
            "messages": [
                {
                    "role": "system",
                    "content": "Return strict JSON only. No markdown, no prose outside JSON, no secrets.",
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        req = request.Request(
            endpoint["base_url"] + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {endpoint['api_key']}",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=45) as resp:
                parsed = json.loads(resp.read().decode("utf-8"))
            text = str(parsed["choices"][0]["message"].get("content") or "").strip()
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
            if not text.startswith("{"):
                match = re.search(r"\{.*\}", text, flags=re.DOTALL)
                if match:
                    text = match.group(0)
            return json.loads(text)
        except Exception as exc:
            last_error = repr(exc)
            if attempt < 4:
                time.sleep(1.5 * attempt)
                continue
            debug_dir = DEFAULT_RUN_ROOT / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            debug_path = debug_dir / f"deepseek_invalid_{int(time.time() * 1000)}.json"
            debug_path.write_text(json.dumps({
                "json_mode": json_mode,
                "finish_reason": (parsed.get("choices") or [{}])[0].get("finish_reason") if parsed else "",
                "content": text,
                "error": last_error,
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            raise RuntimeError(f"DeepSeek response was not valid JSON after retries; saved {debug_path}")
    raise RuntimeError(f"DeepSeek response was not valid JSON: {last_error}")


PROMPT_FAMILIES = [
    "current_numeric",
    "current_local",
    "date_or_event",
    "geography_coordinates",
    "product_identifier",
    "obscure_lookup",
    "science_explainer",
    "health_safety_general",
    "legal_regulatory_current",
    "conversion_or_unit",
    "bad_spelling",
    "ambiguous_followup_style",
]


def generate_case_batch(endpoint: dict[str, str], count: int, batch_index: int, seen_users: list[str]) -> dict[str, Any]:
    family = PROMPT_FAMILIES[batch_index % len(PROMPT_FAMILIES)]
    prompt = {
        "task": "Generate broad user requests that should trigger an AI web search tool.",
        "count": count,
        "batch_index": batch_index,
        "family_focus": family,
        "date_context": "Current date is 2026-08-21. The user may ask current, recent, local, or evergreen factual questions.",
        "requirements": [
            "Return JSON object with a prompts array.",
            "The prompts array must contain exactly count objects total, not count per category.",
            "Each prompt object must have id, user, family, and why_search_needed.",
            "Set family to the family_focus value.",
            "Do not include expected answer, search query, URL, or tool call.",
            "Do not copy any examples from this prompt.",
            "Vary phrasing, typos, brevity, ambiguity, and follow-up-like wording.",
            "Prompts must be public-web questions only, not private email/calendar/tasks/docs.",
            "Cover current prices/rates, local facts, product lookup, obscure identifiers, health/science explainers, geography, dates, conversions, safety, laws/regulations, weather/events, and cases where snippets may require a fetch.",
            "Avoid repeating or lightly paraphrasing the already_seen prompts.",
        ],
        "already_seen": seen_users[-80:],
    }
    return call_deepseek_json(
        endpoint,
        prompt,
        max_tokens=3000,
        temperature=0.7,
        json_mode=True,
    )


def generate_cases(endpoint: dict[str, str], count: int) -> dict[str, Any]:
    generated_batches: list[dict[str, Any]] = []
    all_prompts: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    seen_users_for_prompt: list[str] = []
    batch_size = max(1, int(endpoint.get("generation_batch_size") or 8))
    batch_index = 0
    max_batches = max(30, (count // batch_size + 1) * 6)
    while len(cases) < count and batch_index < max_batches:
        need = min(batch_size, count - len(cases))
        generated = generate_case_batch(endpoint, need, batch_index, seen_users_for_prompt)
        generated_batches.append(generated)
        for item in generated.get("prompts", []):
            if isinstance(item, dict):
                all_prompts.append(item)
                user = re.sub(r"\s+", " ", str(item.get("user") or "")).strip()
                seen_users_for_prompt.append(user)
                if len(user.split()) < 3 or len(user) > 220:
                    continue
                key = user.lower()
                if key in seen:
                    continue
                seen.add(key)
                cases.append({
                    "id": f"deepseek_search_prompt_{len(cases):03d}",
                    "kind": "web",
                    "family": re.sub(r"[^a-z0-9_ -]+", "", str(item.get("family") or "web")).strip().lower().replace(" ", "_") or "web",
                    "user": user,
                    "expect_first_tool": "web_search",
                    "allow_web_search": True,
                    "forbidden_final": ["WEB SEARCH RESULTS", "```sources", "Here are links", "Web sources"],
                    "teacher_seed_id": item.get("id") or f"generated_{len(all_prompts) - 1}",
                    "why_search_needed": item.get("why_search_needed") or "",
                })
                if len(cases) >= count:
                    break
        batch_index += 1
    generated = {"prompts": all_prompts, "batches": generated_batches}
    if len(cases) < max(20, count // 2):
        raise RuntimeError(f"DeepSeek generated too few valid cases: {len(cases)}")
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "generator": Path(__file__).name,
        "provider": endpoint["name"],
        "model": endpoint["model"],
        "cases": cases,
        "raw": generated,
    }


def run_app_route(cases_path: Path, out_dir: Path, endpoint: dict[str, str], args: argparse.Namespace) -> None:
    route_model = args.route_model or endpoint["model"]
    python = REPO_ROOT / ".venv/bin/python"
    cmd = [
        str(python if python.exists() else sys.executable),
        "scripts/eval_odysseus_app_route_smoke.py",
        "--base-url",
        args.base_url,
        "--endpoint",
        endpoint["base_url"],
        "--endpoint-id",
        endpoint["id"],
        "--model",
        route_model,
        "--cases-file",
        str(cases_path),
        "--out-dir",
        str(out_dir),
        "--email-fixture",
        "--timeout",
        str(args.timeout),
    ]
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)


def load_cases_payload(cases_path: Path) -> dict[str, Any]:
    payload = json.loads(cases_path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return {"cases": payload}
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise RuntimeError(f"Cases file must contain a cases array: {cases_path}")
    return payload


def write_cases_subset(source_payload: dict[str, Any], cases: list[dict[str, Any]], path: Path) -> None:
    subset = dict(source_payload)
    subset["cases"] = cases
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(subset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def merge_chunk_results(source_payload: dict[str, Any], chunk_paths: list[Path], out_dir: Path, args: argparse.Namespace, endpoint: dict[str, str]) -> Path:
    results: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []
    generated_at = ""
    for path in chunk_paths:
        if not path.exists():
            raise RuntimeError(f"Missing chunk results: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        generated_at = generated_at or str(payload.get("generated_at") or "")
        cases.extend(payload.get("cases") or [])
        results.extend(payload.get("results") or [])
    summary = {
        "total": len(results),
        "passed": sum(1 for result in results if result.get("pass") is True),
    }
    summary["failed"] = summary["total"] - summary["passed"]
    merged = {
        "generated_at": generated_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base_url": args.base_url,
        "endpoint": endpoint["base_url"],
        "endpoint_id": endpoint["id"],
        "model": args.route_model or endpoint["model"],
        "owner": "pewds",
        "timezone": "Asia/Tokyo",
        "tz_offset_min": 540,
        "summary": summary,
        "cases": cases,
        "results": results,
        "source_cases_metadata": {k: v for k, v in source_payload.items() if k != "cases"},
        "chunk_result_files": [str(path) for path in chunk_paths],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    actual_path = out_dir / "actual_results.json"
    actual_path.write_text(json.dumps(merged, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return actual_path


def run_app_route_chunked(cases_path: Path, out_dir: Path, endpoint: dict[str, str], args: argparse.Namespace) -> Path:
    source_payload = load_cases_payload(cases_path)
    all_cases = list(source_payload["cases"])
    chunk_size = max(1, int(args.chunk_size))
    chunks_dir = out_dir / "chunks"
    chunk_result_paths: list[Path] = []
    for start in range(0, len(all_cases), chunk_size):
        chunk_cases = all_cases[start:start + chunk_size]
        chunk_index = start // chunk_size
        chunk_dir = chunks_dir / f"chunk_{chunk_index:03d}_{start:03d}_{start + len(chunk_cases) - 1:03d}"
        chunk_cases_path = chunk_dir / "cases.json"
        chunk_result_path = chunk_dir / "actual_results.json"
        chunk_result_paths.append(chunk_result_path)
        if chunk_result_path.exists() and not args.force_chunks:
            print(json.dumps({
                "stage": "run_chunk",
                "status": "skip_existing",
                "chunk": chunk_index,
                "cases": len(chunk_cases),
                "actual_results": str(chunk_result_path),
            }))
            continue
        write_cases_subset(source_payload, chunk_cases, chunk_cases_path)
        print(json.dumps({
            "stage": "run_chunk",
            "status": "start",
            "chunk": chunk_index,
            "cases": len(chunk_cases),
            "cases_path": str(chunk_cases_path),
        }))
        route_model = args.route_model or endpoint["model"]
        python = REPO_ROOT / ".venv/bin/python"
        cmd = [
            str(python if python.exists() else sys.executable),
            "scripts/eval_odysseus_app_route_smoke.py",
            "--base-url",
            args.base_url,
            "--endpoint",
            endpoint["base_url"],
            "--endpoint-id",
            endpoint["id"],
            "--model",
            route_model,
            "--cases-file",
            str(chunk_cases_path),
            "--out-dir",
            str(chunk_dir),
            "--email-fixture",
            "--timeout",
            str(args.timeout),
        ]
        completed = subprocess.run(cmd, cwd=REPO_ROOT, check=False)
        if not chunk_result_path.exists():
            raise RuntimeError(f"Chunk {chunk_index} exited {completed.returncode} without writing {chunk_result_path}")
        print(json.dumps({
            "stage": "run_chunk",
            "status": "done",
            "chunk": chunk_index,
            "returncode": completed.returncode,
            "actual_results": str(chunk_result_path),
        }))
    return merge_chunk_results(source_payload, chunk_result_paths, out_dir, args, endpoint)


def visible_tool_output(result: dict[str, Any], index: int) -> str:
    outputs = result.get("tool_outputs") or []
    if 0 <= index < len(outputs):
        return str(outputs[index].get("output") or "")
    return ""


def compact_tool_output(text: str, *, max_chars: int = SFT_TOOL_OUTPUT_MAX_CHARS) -> str:
    text = str(text or "").strip()
    if len(text) <= max_chars:
        return text
    sources_match = re.search(r"```sources.*?```", text, flags=re.DOTALL)
    summary_match = re.search(
        r"SEARCH RESULTS SUMMARY:\s*-+\s*(.*?)(?:\n={20,}|\Z)",
        text,
        flags=re.DOTALL,
    )
    pieces: list[str] = []
    if sources_match:
        pieces.append(sources_match.group(0).strip())
    if summary_match:
        pieces.append("SEARCH RESULTS SUMMARY:\n" + summary_match.group(1).strip())
    compact = "\n\n".join(piece for piece in pieces if piece).strip()
    if compact and len(compact) <= max_chars:
        return compact
    return (compact or text)[:max_chars].rstrip() + "\n[tool output truncated for SFT]"


def trace_audit(result: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    tools = list(result.get("tool_names") or [])
    final = str(result.get("final_answer") or "").strip()
    if not tools:
        reasons.append("no_tool")
    if tools and tools[0] != "web_search":
        reasons.append("first_tool_not_web_search")
    if any(tool not in WEB_TOOLS for tool in tools):
        reasons.append("non_web_tool")
    if len(tools) > 3:
        reasons.append("too_many_tools")
    if not final:
        reasons.append("empty_final")
    if SOURCE_DUMP_RE.search(final):
        reasons.append("source_dump_final")
    if len(final.split()) < 8:
        reasons.append("too_short_final")
    if result.get("stream_errors"):
        reasons.append("stream_error")
    return not reasons, reasons


def corrected_final(endpoint: dict[str, str], result: dict[str, Any], reasons: list[str]) -> str:
    evidence = []
    for idx, call in enumerate(result.get("tool_calls") or []):
        evidence.append({
            "tool": call.get("tool"),
            "args": call.get("args"),
            "output": visible_tool_output(result, idx)[:5000],
        })
    prompt = {
        "task": "Write the final assistant answer for an Odysseus web-search trace.",
        "user": result.get("user"),
        "audit_reasons": reasons,
        "tool_evidence": evidence,
        "current_final": result.get("final_answer") or "",
        "requirements": [
            "Return JSON object with final only.",
            "The final must be exactly what the assistant should say to the user.",
            "Answer the user's question directly using the tool evidence.",
            "Do not analyze the trace.",
            "Do not write phrases like 'the user asked', 'the evidence says', 'I should answer', or 'tool evidence'.",
            "Do not mention search results, snippets, links, sources, tool calls, or wrappers unless a source name is essential.",
            "If the evidence genuinely lacks the answer, say what is missing and do not invent facts.",
            "Keep it concise, normally 1-4 sentences and under 900 characters.",
        ],
    }
    fixed = call_deepseek_json(endpoint, prompt, max_tokens=1200, temperature=0.25)
    final = re.sub(r"\s+", " ", str(fixed.get("final") or "")).strip()
    if not final or SOURCE_DUMP_RE.search(final) or META_FINAL_RE.search(final) or len(final) > 1400:
        return ""
    return final


def final_needs_rewrite(final: str) -> bool:
    final = str(final or "").strip()
    return bool(SOURCE_DUMP_RE.search(final) or META_FINAL_RE.search(final) or len(final) > 1400)


def make_tool_call(tool: str, args: Any, suffix: str) -> dict[str, Any]:
    if isinstance(args, str):
        payload = args
    else:
        payload = json.dumps(args or {}, separators=(",", ":"), ensure_ascii=True)
    return {
        "id": f"call_{suffix}",
        "type": "function",
        "function": {"name": tool, "arguments": payload},
    }


def build_sft_row(result: dict[str, Any], final: str, reasons: list[str]) -> dict[str, Any] | None:
    calls = result.get("tool_calls") or []
    if not calls or len(calls) > 3:
        return None
    if calls[0].get("tool") != "web_search":
        return None
    if any(call.get("tool") not in WEB_TOOLS for call in calls):
        return None
    messages: list[dict[str, Any]] = [{"role": "user", "content": result.get("user") or ""}]
    for idx, call in enumerate(calls):
        tool_name = str(call.get("tool") or "")
        tool_call = make_tool_call(tool_name, call.get("args"), f"{result.get('id', 'trace')}_{idx}")
        messages.append({"role": "assistant", "content": "", "tool_calls": [tool_call]})
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call["id"],
            "content": compact_tool_output(visible_tool_output(result, idx)),
        })
    messages.append({"role": "assistant", "content": final})
    item = {
        "messages": messages,
        "tools": TOOL_SCHEMAS,
        "generator": "odysseus_deepseek_search_trace_pipeline",
        "metadata": {
            "source_result_id": result.get("id"),
            "family": result.get("kind") or "web",
            "actual_tool_count": len(calls),
            "audit_reasons": reasons,
            "source_endpoint_id": "deepseek",
        },
    }
    item["uuid"] = stable_id("ody_v57_search_trace", item)
    return item


def audit_and_build_sft(actual_path: Path, out_dir: Path, endpoint: dict[str, str], *, max_corrections: int) -> dict[str, Any]:
    payload = json.loads(actual_path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    correction_count = 0
    for result in payload.get("results") or []:
        ok, reasons = trace_audit(result)
        final = str(result.get("final_answer") or "").strip()
        if (not ok or final_needs_rewrite(final)) and correction_count < max_corrections and result.get("tool_calls"):
            fixed = corrected_final(endpoint, result, reasons)
            if fixed:
                final = fixed
                correction_count += 1
                reasons = [reason for reason in reasons if reason not in {"empty_final", "source_dump_final", "too_short_final"}]
        row = build_sft_row(result, final, reasons)
        accepted = row is not None and not final_needs_rewrite(final) and bool(final.strip())
        if accepted:
            rows.append(row)
        audits.append({
            "id": result.get("id"),
            "user": result.get("user"),
            "tool_names": result.get("tool_names") or [],
            "actual_final": result.get("final_answer") or "",
            "accepted": accepted,
            "audit_reasons": reasons,
            "sft_uuid": row.get("uuid") if row else "",
        })
    out_dir.mkdir(parents=True, exist_ok=True)
    train: list[dict[str, Any]] = []
    val: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        (val if idx % 8 == 7 else train).append(row)
    for name, subset in [("all.jsonl", rows), ("train.jsonl", train), ("val.jsonl", val)]:
        (out_dir / name).write_text("".join(json.dumps(row, ensure_ascii=True) + "\n" for row in subset), encoding="utf-8")
    (out_dir / "audit.json").write_text(json.dumps({"audits": audits}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_actual_results": str(actual_path),
        "total_results": len(payload.get("results") or []),
        "accepted_sft_rows": len(rows),
        "train_rows": len(train),
        "val_rows": len(val),
        "corrections": correction_count,
        "max_tools": 3,
        "sft_tool_output_max_chars": SFT_TOOL_OUTPUT_MAX_CHARS,
        "allowed_tools": sorted(WEB_TOOLS),
        "files": {
            "train": str(out_dir / "train.jsonl"),
            "val": str(out_dir / "val.jsonl"),
            "all": str(out_dir / "all.jsonl"),
            "audit": str(out_dir / "audit.json"),
        },
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--sft-dir", type=Path, default=DEFAULT_SFT_DIR, required=DEFAULT_SFT_DIR is None)
    parser.add_argument("--count", type=int, default=150)
    parser.add_argument("--stage", choices=["all", "generate", "run", "audit"], default="all")
    parser.add_argument("--base-url", default="http://127.0.0.1:7011")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--max-corrections", type=int, default=200)
    parser.add_argument("--teacher-model", default=os.environ.get("DEEPSEEK_TEACHER_MODEL", "deepseek-chat"))
    parser.add_argument("--route-model", default=os.environ.get("DEEPSEEK_ROUTE_MODEL", "deepseek-v4-flash"))
    parser.add_argument("--chunk-size", type=int, default=10)
    parser.add_argument("--generation-batch-size", type=int, default=8)
    parser.add_argument("--force-chunks", action="store_true")
    args = parser.parse_args()

    endpoint = db_deepseek_endpoint()
    endpoint["model"] = args.teacher_model
    endpoint["generation_batch_size"] = str(args.generation_batch_size)
    args.run_root.mkdir(parents=True, exist_ok=True)
    cases_path = args.run_root / "cases.json"
    actual_dir = args.run_root / "deepseek_actual"
    actual_path = actual_dir / "actual_results.json"

    if args.stage in {"all", "generate"}:
        generated = generate_cases(endpoint, args.count)
        cases_path.write_text(json.dumps(generated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"stage": "generate", "cases": len(generated["cases"]), "path": str(cases_path)}, indent=2))
        if args.stage == "generate":
            return 0

    if args.stage in {"all", "run"}:
        if not cases_path.exists():
            raise RuntimeError(f"Missing cases file: {cases_path}")
        actual_path = run_app_route_chunked(cases_path, actual_dir, endpoint, args)
        print(json.dumps({"stage": "run", "actual_results": str(actual_path)}, indent=2))
        if args.stage == "run":
            return 0

    if args.stage in {"all", "audit"}:
        if not actual_path.exists():
            raise RuntimeError(f"Missing actual results file: {actual_path}")
        manifest = audit_and_build_sft(actual_path, args.sft_dir, endpoint, max_corrections=args.max_corrections)
        print(json.dumps({"stage": "audit", **manifest}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
