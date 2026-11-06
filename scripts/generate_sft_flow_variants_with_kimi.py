#!/usr/bin/env python3
"""Generate non-duplicate Odysseus flow variants from behavioral seed flows."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Any

from odysseus_related_flow_audit import Flow, flow_matrix
from repair_sft_corpus_with_kimi import endpoint, parse_json

ROOT = Path(__file__).resolve().parents[1]


def normalize_prompt(value: str) -> str:
    value = value.lower().replace("{marker}", " marker ")
    value = re.sub(r"\b\d{8}_\d{6}(?:-[a-f0-9]+)?\b", " marker ", value)
    value = re.sub(r"\b[a-f0-9]{8,}\b", " marker ", value)
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def existing_prompts(path: Path) -> set[str]:
    if not path.exists():
        return set()
    prompts = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        value = row.get("user")
        if isinstance(value, str) and value.strip():
            prompts.add(normalize_prompt(value))
    return prompts


def seed_payload(flow: Flow) -> dict[str, Any]:
    return {
        "id": flow.id,
        "domain": flow.domain,
        "title": flow.title,
        "turns": [
            {
                "id": turn.id,
                "prompt": turn.prompt,
                "tools": list(turn.tools),
                "dry_run": turn.dry_run,
            }
            for turn in flow.turns
        ],
    }


def generate(ep: dict[str, str], flow: Flow, count: int, timeout: float, retries: int) -> list[dict[str, Any]]:
    system = """You create realistic multi-turn user workflows for testing an assistant UI.
Return strict JSON only: {"flows":[...]}. Each flow must contain id, domain, title, and turns.

Treat the supplied flow as a behavioral seed, never as text to paraphrase mechanically.
- Produce the requested number of substantially different scenarios.
- Preserve the exact turn count, turn IDs, expected tools, dry_run values, and tool order.
- Each conversation must remain coherent: follow-ups refer naturally to prior results or objects.
- Change entities, goals, wording, and realistic task details across variants.
- Keep {marker} exactly where a temporary unique name is required.
- Never mention tests, audits, fixtures, harnesses, SFT, synthetic data, schemas, or training.
- Do not use private real-world personal data. Invent ordinary benign names and content.
- Do not add unsupported IDs or claim results before a tool has produced them.
- Dry-run turns must explicitly avoid state changes; mutation turns should request the action clearly.
- User prompts should sound casual and varied, including occasional concise follow-ups.
"""
    body = {
        "model": ep["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps({"count": count, "seed": seed_payload(flow)}, ensure_ascii=False)},
        ],
        "temperature": 0.85,
        "max_tokens": 9000,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        ep["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {ep['api_key']}"},
        method="POST",
    )
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode())
            break
        except Exception as exc:
            last_error = exc
            if attempt == retries:
                raise
            time.sleep(2 * (attempt + 1))
    else:
        raise RuntimeError("Kimi generation failed") from last_error
    message = payload["choices"][0]["message"]
    parsed = parse_json(str(message.get("content") or message.get("reasoning_content") or ""))
    flows = parsed.get("flows")
    if not isinstance(flows, list):
        raise ValueError(f"Kimi returned no flows for {flow.id}")
    return flows


def validate_variant(seed: Flow, raw: dict[str, Any], index: int) -> dict[str, Any]:
    turns = raw.get("turns")
    if not isinstance(turns, list) or len(turns) != len(seed.turns):
        raise ValueError(f"{seed.id} variant {index}: wrong turn count")
    clean_turns = []
    for expected, actual in zip(seed.turns, turns):
        if not isinstance(actual, dict):
            raise ValueError(f"{seed.id} variant {index}: invalid turn")
        tools = actual.get("tools")
        if tools != list(expected.tools) or bool(actual.get("dry_run", False)) != expected.dry_run:
            raise ValueError(f"{seed.id} variant {index}: tool contract changed")
        prompt = str(actual.get("prompt") or "").strip()
        if not prompt:
            raise ValueError(f"{seed.id} variant {index}: empty prompt")
        clean_turns.append({
            "id": expected.id,
            "prompt": prompt,
            "tools": list(expected.tools),
            "dry_run": expected.dry_run,
        })
    return {
        "id": f"{seed.id}_v{index:02d}",
        "domain": seed.domain,
        "title": str(raw.get("title") or f"{seed.title} variant {index}"),
        "turns": clean_turns,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", required=True, help="Comma-separated built-in flow IDs")
    parser.add_argument("--variants-per-seed", type=int, default=3)
    parser.add_argument("--trace", type=Path, default=ROOT / "data/sft_traces/sft_alex_creator.jsonl")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--endpoint-id", default="f3904562")
    parser.add_argument("--model", default="moonshotai/kimi-k3")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--retries", type=int, default=1)
    args = parser.parse_args()

    matrix = {flow.id: flow for flow in flow_matrix()}
    seed_ids = [value.strip() for value in args.seeds.split(",") if value.strip()]
    missing = [value for value in seed_ids if value not in matrix]
    if missing:
        parser.error(f"unknown seeds: {', '.join(missing)}")

    seen = existing_prompts(args.trace)
    ep = endpoint(args.endpoint_id, args.model)
    output = []
    rejected = []
    generated: dict[str, list[dict[str, Any]]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(generate, ep, matrix[seed_id], args.variants_per_seed + 2, args.timeout, args.retries): seed_id
            for seed_id in seed_ids
        }
        for future in concurrent.futures.as_completed(futures):
            seed_id = futures[future]
            try:
                generated[seed_id] = future.result()
                print(f"generated {seed_id}", flush=True)
            except Exception as exc:
                rejected.append({"seed": seed_id, "reason": f"provider failure: {exc!r}"})
                print(f"failed {seed_id}: {exc!r}", flush=True)

    for seed_id in seed_ids:
        seed = matrix[seed_id]
        candidates = generated.get(seed_id, [])
        accepted_for_seed = 0
        for candidate in candidates:
            if accepted_for_seed >= args.variants_per_seed:
                break
            try:
                clean = validate_variant(seed, candidate, accepted_for_seed + 1)
            except (KeyError, TypeError, ValueError) as exc:
                rejected.append({"seed": seed_id, "reason": str(exc)})
                continue
            normalized = [normalize_prompt(turn["prompt"]) for turn in clean["turns"]]
            if len(set(normalized)) != len(normalized) or any(prompt in seen for prompt in normalized):
                rejected.append({"seed": seed_id, "reason": "duplicate prompt"})
                continue
            if any(re.search(r"\b(?:sft|fixture|harness|synthetic|audit)\b", turn["prompt"], re.I) for turn in clean["turns"]):
                rejected.append({"seed": seed_id, "reason": "training-meta language"})
                continue
            output.append(clean)
            seen.update(normalized)
            accepted_for_seed += 1
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps({"flows": output, "rejected": rejected}, ensure_ascii=False, indent=2), encoding="utf-8")
        if accepted_for_seed < args.variants_per_seed:
            rejected.append({"seed": seed_id, "reason": f"only accepted {accepted_for_seed} variants"})

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"flows": output, "rejected": rejected}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.out), "flows": len(output), "turns": sum(len(row["turns"]) for row in output), "rejected": len(rejected)}, indent=2))


if __name__ == "__main__":
    main()
