#!/usr/bin/env python3
"""Evaluate follow-up domain continuity against an OpenAI-compatible API."""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tool_policy import ToolPolicy
from src.tool_routing_experiment import MODEL_CHOICE_MODE, select_experiment_inventory
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.turn_contract import (
    _families_for_tool,
    canonical_tool,
    requested_capabilities,
    resolve_full_inventory_contract,
    resolve_turn_contract,
)


FAMILY_TOOL = {
    "search_browser": "web_search",
    "notes": "manage_notes",
    "documents": "manage_documents",
    "email": "search_emails",
    "calendar": "manage_calendar",
    "tasks": "manage_tasks",
    "skills": "manage_skills",
    "memory": "manage_memory",
}

SUBJECTS = [
    "Rocket League", "Project Juniper", "Aurora Seven", "Kyoto Railway Museum",
    "Framework Laptop", "Blue Harbor", "Atlas Report", "Orion Browser",
    "Maple Invoice", "Cobalt Launch", "Sakura Booking", "Nimbus Checklist",
]

FOLLOWUPS = [
    "I searched {subject} and cannot find it",
    "{subject} is not showing for me",
    "where is {subject} listed",
    "I looked for {subject} but got nothing",
    "why does {subject} not appear in the results",
    "still no sign of {subject}",
    "the result for {subject} seems to be missing",
    "I tried again and {subject} is absent",
]


def history(subject: str, family: str) -> list[dict]:
    tool = FAMILY_TOOL[family]
    return [
        {"role": "user", "content": f"Find {subject}"},
        {"role": "assistant", "content": f"I found information about {subject}.", "metadata": {
            "tool_events": [{"tool": tool, "exit_code": 0, "error": False}],
        }},
    ]


def build_cases(count: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    families = tuple(FAMILY_TOOL)
    cases = []
    for index in range(count):
        source = families[index % len(families)]
        subject = f"{rng.choice(SUBJECTS)} {index + 1}"
        if index % 4:
            prompt = rng.choice(FOLLOWUPS).format(subject=subject)
            expected = source
            kind = "continuation"
        else:
            target = families[(families.index(source) + 1 + index) % len(families)]
            noun = {
                "search_browser": "the web", "notes": "my notes",
                "documents": "my documents", "email": "my email",
                "calendar": "my calendar", "tasks": "my tasks",
                "skills": "my skills", "memory": "my memories",
            }[target]
            prompt = f"Search {noun} for {subject} instead"
            expected = target
            kind = "explicit_switch"
        cases.append({
            "id": index + 1, "kind": kind, "source": source,
            "expected": expected, "subject": subject, "prompt": prompt,
        })
    rng.shuffle(cases)
    return cases


def contract_for(case: dict):
    prior = history(case["subject"], case["source"])
    policy = ToolPolicy()
    capabilities = requested_capabilities(case["prompt"], prior)
    inventory = resolve_full_inventory_contract(
        schemas=FUNCTION_TOOL_SCHEMAS, policy=policy,
    )
    routed = resolve_turn_contract(
        capabilities=capabilities, schemas=FUNCTION_TOOL_SCHEMAS, policy=policy,
    )
    contract = select_experiment_inventory(
        inventory, routed, prior, MODEL_CHOICE_MODE, user_text=case["prompt"],
    )
    return prior, capabilities, contract


def endpoint_from_database(path: Path, session_id: str) -> tuple[str, str, dict]:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        row = db.execute(
            "select endpoint_url, model, headers from sessions where id=?", (session_id,),
        ).fetchone()
    if not row:
        raise SystemExit(f"Session {session_id} was not found in {path}")
    return row[0], row[1], json.loads(row[2] or "{}")


async def run_case(client, semaphore, endpoint, model, headers, case):
    prior, capabilities, contract = contract_for(case)
    schemas = contract.schemas()
    exposed = {
        family for schema in schemas
        for family in _families_for_tool(canonical_tool(schema["function"]["name"]))
    }
    allowed_families = {case["expected"]}
    if case["expected"] == "email":
        allowed_families.add("contacts")
    result = {**case, "capabilities": sorted(capabilities), "exposed": sorted(exposed)}
    result["contract_ok"] = bool(exposed) and exposed <= allowed_families
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Continue the conversation. Use an offered tool when evidence is needed."},
            *[{"role": row["role"], "content": row["content"]} for row in prior],
            {"role": "user", "content": case["prompt"]},
        ],
        "tools": schemas,
        "tool_choice": "auto",
        "temperature": 0.7,
        "max_tokens": 96,
    }
    async with semaphore:
        for attempt in range(4):
            try:
                response = await client.post(endpoint, headers=headers, json=payload)
                if response.status_code == 429 and attempt < 3:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                response.raise_for_status()
                message = response.json()["choices"][0]["message"]
                calls = message.get("tool_calls") or []
                tools = [canonical_tool(call["function"]["name"]) for call in calls]
                called_families = sorted({
                    family for tool in tools for family in _families_for_tool(tool)
                })
                result.update({"tools": tools, "called_families": called_families})
                result["cross_domain"] = bool(set(called_families) - allowed_families)
                result["expected_call"] = case["expected"] in called_families
                return result
            except Exception as exc:
                if attempt == 3:
                    result["error"] = f"{type(exc).__name__}: {exc}"
                    return result
                await asyncio.sleep(0.5 * (attempt + 1))


async def main(args):
    endpoint, model, headers = endpoint_from_database(args.database, args.session_id)
    cases = build_cases(args.count, args.seed)
    timeout = httpx.Timeout(45, connect=10)
    semaphore = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient(timeout=timeout) as client:
        results = await asyncio.gather(*(
            run_case(client, semaphore, endpoint, model, headers, case)
            for case in cases
        ))
    summary = {
        "count": len(results),
        "contract_pass": sum(bool(row.get("contract_ok")) for row in results),
        "cross_domain_calls": sum(bool(row.get("cross_domain")) for row in results),
        "expected_tool_calls": sum(bool(row.get("expected_call")) for row in results),
        "no_tool": sum(not row.get("tools") and not row.get("error") for row in results),
        "errors": sum("error" in row for row in results),
        "kinds": Counter(row["kind"] for row in results),
    }
    output = {"summary": summary, "results": results}
    args.output.write_text(json.dumps(output, indent=2, default=dict) + "\n")
    print(json.dumps(summary, indent=2, default=dict))
    raise SystemExit(1 if summary["contract_pass"] != len(results) or summary["cross_domain_calls"] else 0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument("--seed", type=int, default=20260921)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(main(parser.parse_args()))
