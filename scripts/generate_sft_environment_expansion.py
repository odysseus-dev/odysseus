#!/usr/bin/env python3
"""Generate grounded cross-environment workflow cases from approved seed families."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import re
import sys
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STYLE_CONTRACT = ROOT / "docs" / "sft-style-contract.md"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.repair_sft_corpus_with_kimi import endpoint, parse_json  # noqa: E402
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS  # noqa: E402


ALL_TOOL_NAMES = frozenset(
    str(schema.get("function", {}).get("name") or "")
    for schema in FUNCTION_TOOL_SCHEMAS
    if schema.get("function", {}).get("name") and schema.get("function", {}).get("name") != "host_shell"
)


def compact_tool_catalog() -> list[dict[str, Any]]:
    """Expose the complete product tool vocabulary to the scenario author."""
    catalog = []
    for schema in FUNCTION_TOOL_SCHEMAS:
        function = schema.get("function") or {}
        name = str(function.get("name") or "")
        if not name or name == "host_shell":
            continue
        parameters = function.get("parameters") or {}
        properties = parameters.get("properties") or {}
        entry: dict[str, Any] = {
            "name": name,
            "purpose": str(function.get("description") or "")[:700],
            "required": list(parameters.get("required") or []),
        }
        action = properties.get("action") if isinstance(properties, dict) else None
        if isinstance(action, dict) and isinstance(action.get("enum"), list):
            entry["actions"] = action["enum"]
        catalog.append(entry)
    return catalog

OWNERS = ["sft_maya_ops", "sft_jules_research", "sft_nora_design", "sft_omar_finance"]
EFFECTFUL_WITHOUT_DRY_RUN = {
    "edit_image",
    "mcp__email__unsubscribe_email",
    "mcp__email__send_email",
    "mcp__email__reply_to_email",
    "mcp__email__delete_email",
    "mcp__email__bulk_email",
    "mcp__email__block_sender",
}
META_RE = re.compile(
    r"\{marker\}|\b(?:sft|fixture|harness|synthetic|training trace|reversible marker|"
    r"marker[- ]scoped|marker recipient|cleanup test)\b",
    re.I,
)
COMPOUND_MUTATION_VERIFY_RE = re.compile(
    r"\b(?:add|create|schedule|book|move|update|change|delete|remove)\b.+"
    r"\b(?:then|and)\s+(?:show|list|open|check|verify|confirm)\b",
    re.I,
)
COMPOUND_MUTATIONS_RE = re.compile(
    r"\b(?:add|create|save|schedule|book|send|reply|archive|move|update|change|delete|remove)\b.+"
    r"\b(?:then|and then|;\s*then)\b.+"
    r"\b(?:add|create|save|schedule|book|send|reply|archive|move|update|change|delete|remove)\b",
    re.I,
)


def normalize(value: str) -> str:
    value = value.lower().replace("{marker}", " marker ")
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def compact_seed(seed: dict[str, Any]) -> dict[str, Any]:
    return {
        "seed_family_id": seed["seed_family_id"],
        "session_name": seed.get("session_name"),
        "owner_bound": seed["owner_bound"],
        "tools": seed["tools"],
        "turns": [
            {
                "user": str(turn.get("user") or "")[:1200],
                "assistant": str(turn.get("assistant") or "")[:1500],
                "tools": [event.get("tool") for event in turn.get("tool_events") or [] if event.get("tool")],
            }
            for turn in seed["turns"][:6]
        ],
    }


def compact_environment(environment: dict[str, Any]) -> dict[str, Any]:
    return {
        "owner": environment["owner"],
        "profile": environment["profile"],
        "counts": environment["counts"],
        "email_accounts": environment["email_accounts"],
        "emails": environment["emails"][:15],
        "notes": environment["notes"][:12],
        "memories": environment["memories"][:12],
        "documents": environment["documents"][:12],
        "tasks": environment["tasks"][:12],
        "calendars": environment["calendars"],
        "events": environment["events"][:12],
    }


def target_owners(seed: dict[str, Any], index: int) -> list[str]:
    if seed["owner_bound"]:
        return OWNERS
    return [OWNERS[index % len(OWNERS)]]


def request_variants(
    ep: dict[str, str], seed: dict[str, Any], targets: list[dict[str, Any]], timeout: float, retries: int
) -> list[dict[str, Any]]:
    system = """You design grounded multi-turn workflows for a real tool-using personal assistant.
Return strict JSON only: {"cases":[...]}. Return exactly one case per target environment.

For each case return:
- owner, title, domain
- turns: 3 or 4 objects with id, prompt, expected_tools (exactly one tool name), expected_actions (object mapping manager tool names to acceptable action strings), dry_run
- fixture_plan: zero or more objects with type and fields
- cleanup: fixture types that must be restored or removed

Rules:
- The source is behavioral evidence, not text to paraphrase and not an allowlist. Use the complete tool catalog to independently identify the best intended tool for each new turn. Preserve the useful outcome while changing scenario, entities, wording, and follow-up style.
- Distinguish tools with overlapping names by their documented purpose and required arguments. If the source used a less suitable tool, choose the catalog tool that actually fulfills the new prompt.
- Make the turns one coherent conversation. Later turns should naturally build on earlier tool results.
- Use exact IDs/titles/UIDs from the target inventory for read/update/delete workflows, or create a marker-scoped object first. Never invent an existing object.
- Give temporary objects ordinary, project-specific names that a real user might choose. Keep them distinct from supplied inventory names, but never expose run IDs, markers, fixtures, tests, audits, or cleanup mechanics to the user.
- Allowed fixture types: note, calendar_event, document, memory, task, email_state_snapshot. Prefer existing inventory for read-only workflows.
- expected_tools must contain exactly one name from allowed_tools. Give each turn to one tool family; never combine shell, memory, search, fetch, video, email, calendar, notes, or another unrelated capability in one prompt.
- Across the full conversation, use additional related schemas when they materially help. The 3-4 turns must still produce 3-4 tool calls, but do not force an unrelated UI or clarification tool into a coherent manager-tool lifecycle.
- Give each turn one atomic objective. Put mutation and verification in separate consecutive turns; never ask to create/update/delete and then show/check/verify in the same turn.
- Calendar create/update prompts must include an exact date and start time. If either is intentionally missing, make that turn an ambiguity-resolution turn with expected_tools including ask_user; words like morning or afternoon are not exact times.
- Do not mention dataset audits, fixtures, harnesses, SFT, synthetic data, schemas, or training. Ordinary user-domain audits such as a settings review or financial audit are fine.
- Match the source users' natural style: concise, direct follow-ups; avoid evaluator language such as "confirm the tool worked", "reversible", "marker", "cleanup test", or instructions about internal implementation.
- Do not copy source names, accounts, IDs, dates, or domain details unless they also appear in the target inventory.
- Never use real personal data. Use only supplied environment data or harmless marker-scoped values.
- Mutations must be reversible. External/global operations must be dry-run unless the source proves a safe reversible lifecycle.
- Never set dry_run=true for email send, reply, delete, bulk action, block, unsubscribe, or image editing: those tools do not support dry-run. Email mutations are safe here because the runner restores the supplied synthetic mailbox snapshot; unsupported global/image mutations must not be generated.
- Preserve ambiguity handling: if essential information is absent, expected_tools should include ask_user rather than guessing.
- The current date is supplied in the request. Relative language such as today, upcoming, this week, and next month must agree with it. Existing inventory items may be discussed historically, but must not be described as upcoming when they are in the past.
"""
    if STYLE_CONTRACT.exists():
        system += "\nApply this speaking-style contract to every generated conversation:\n\n" + STYLE_CONTRACT.read_text(encoding="utf-8")
    allowed_tools = sorted(ALL_TOOL_NAMES | set(seed["tools"]))
    payload = {
        "model": ep["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps({
                "seed": compact_seed(seed),
                "current_date": date.today().isoformat(),
                "allowed_tools": allowed_tools,
                "tool_catalog": compact_tool_catalog(),
                "targets": [compact_environment(target) for target in targets],
            }, ensure_ascii=False)},
        ],
        "temperature": 0.8,
        "max_tokens": 10000,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        ep["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {ep['api_key']}"},
        method="POST",
    )
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                result = json.loads(response.read().decode())
            message = result["choices"][0]["message"]
            parsed = parse_json(str(message.get("content") or message.get("reasoning_content") or ""))
            cases = parsed.get("cases")
            if not isinstance(cases, list):
                raise ValueError("missing cases list")
            return cases
        except Exception as exc:
            last = exc
            if attempt == retries:
                raise
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("generation failed") from last


def validate_case(
    seed: dict[str, Any], expected_owner: str, environment: dict[str, Any], raw: dict[str, Any], ordinal: int
) -> dict[str, Any]:
    if str(raw.get("owner")) != expected_owner:
        raise ValueError("owner mismatch")
    turns = raw.get("turns")
    if not isinstance(turns, list) or not 3 <= len(turns) <= 4:
        raise ValueError("case must contain 3-4 turns")
    allowed = set(ALL_TOOL_NAMES) | set(seed["tools"])
    clean_turns = []
    normalized = set()
    for index, turn in enumerate(turns, 1):
        prompt = str(turn.get("prompt") or "").strip()
        tools = turn.get("expected_tools") or []
        if isinstance(tools, str) and tools in allowed:
            tools = [tools]
        if not prompt or META_RE.search(prompt):
            raise ValueError("empty or meta prompt")
        if COMPOUND_MUTATION_VERIFY_RE.search(prompt):
            raise ValueError("compound mutation-and-verification prompt")
        if COMPOUND_MUTATIONS_RE.search(prompt):
            raise ValueError("multiple mutations in one turn")
        prompt_lower = prompt.lower()
        calendar_mutation = bool(
            "manage_calendar" in tools
            and re.search(
                r"\b(?:add|create|schedule|book|move|reschedule|change|update|edit)\b",
                prompt_lower,
            )
        )
        has_exact_time = bool(re.search(
            r"\b(?:all[ -]day)\b|\b(?:[01]?\d|2[0-3]):[0-5]\d\b|\b(?:1[0-2]|0?[1-9])(?:\s*:\s*[0-5]\d)?\s*(?:am|pm)\b",
            prompt_lower,
        ))
        if calendar_mutation and not has_exact_time and "ask_user" not in tools:
            raise ValueError("calendar mutation lacks exact time or ask_user")
        relative_date = None
        if re.search(r"\btoday\b", prompt_lower):
            relative_date = date.today()
        elif re.search(r"\btomorrow\b", prompt_lower):
            relative_date = date.today() + timedelta(days=1)
        if relative_date:
            for event in environment.get("events") or []:
                title = str(event.get("summary") or "").strip()
                start = str(event.get("start") or "")[:10]
                if title and title.lower() in prompt_lower and start and start != relative_date.isoformat():
                    raise ValueError(
                        f"relative date conflicts with inventory event {title!r}: {relative_date} != {start}"
                    )
        if not isinstance(tools, list) or len(tools) != 1 or not set(tools) <= allowed:
            raise ValueError(f"invalid expected tools: {tools}")
        if bool(turn.get("dry_run")) and set(tools) & EFFECTFUL_WITHOUT_DRY_RUN:
            raise ValueError("dry_run requested for an effectful tool without dry-run support")
        raw_expected_actions = turn.get("expected_actions")
        expected_actions = raw_expected_actions if isinstance(raw_expected_actions, dict) else {}
        unexpected_action_tools = set(expected_actions) - set(tools)
        if unexpected_action_tools:
            raise ValueError(f"expected_actions names tools outside expected_tools: {sorted(unexpected_action_tools)}")
        # Standalone email tools encode the operation in the tool name rather
        # than an `action` argument, so tool identity is the complete contract.
        expected_actions = {
            tool: actions for tool, actions in expected_actions.items()
            if not tool.startswith("mcp__email__")
        }
        digest = normalize(prompt)
        if digest in normalized:
            raise ValueError("duplicate prompt within case")
        normalized.add(digest)
        clean_turns.append({
            "id": str(turn.get("id") or f"turn_{index}"),
            "prompt": prompt,
            "expected_tools": tools,
            "expected_actions": expected_actions,
            "dry_run": bool(turn.get("dry_run", False)),
        })
    suffix = hashlib.sha1(f"{seed['seed_family_id']}:{expected_owner}".encode()).hexdigest()[:10]
    return {
        "case_id": f"expand-{suffix}",
        "seed_family_id": seed["seed_family_id"],
        "source_session_id": seed["source_session_id"],
        "split": seed["split"],
        "owner": expected_owner,
        "title": str(raw.get("title") or f"Expanded workflow {ordinal}"),
        "domain": str(raw.get("domain") or "other"),
        "source_tools": seed["tools"],
        "turns": clean_turns,
        "fixture_plan": raw.get("fixture_plan") if isinstance(raw.get("fixture_plan"), list) else [],
        "cleanup": raw.get("cleanup") if isinstance(raw.get("cleanup"), list) else [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-manifest", type=Path, required=True)
    parser.add_argument("--inventories", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, help="Limit source seed families for a pilot")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--endpoint-id", default="f3904562")
    parser.add_argument("--model", default="moonshotai/kimi-k3")
    parser.add_argument("--retry-failures", action="store_true")
    args = parser.parse_args()

    seeds = json.loads(args.seed_manifest.read_text(encoding="utf-8"))["seeds"]
    seeds = seeds[args.offset : args.offset + args.limit if args.limit else None]
    selected_seeds = list(seeds)
    environments = {row["owner"]: row for row in json.loads(args.inventories.read_text(encoding="utf-8"))["environments"]}
    ep = endpoint(args.endpoint_id, args.model)
    generated: dict[str, list[dict[str, Any]]] = {}
    failures: list[dict[str, Any]] = []
    if args.out.exists():
        previous = json.loads(args.out.read_text(encoding="utf-8"))
        failures = [row for row in previous.get("failures", []) if isinstance(row, dict)]
        seed_by_family = {str(seed["seed_family_id"]): seed for seed in selected_seeds}
        for case in previous.get("cases", []):
            if isinstance(case, dict) and case.get("seed_family_id"):
                family = str(case["seed_family_id"])
                owner = str(case.get("owner") or "")
                seed = seed_by_family.get(family)
                if not seed or owner not in environments:
                    continue
                try:
                    checked = validate_case(seed, owner, environments[owner], case, 1)
                except Exception as exc:
                    failures.append({"seed_family_id": family, "owner": owner, "error": repr(exc)})
                    continue
                generated.setdefault(family, []).append(checked)
    pending: list[tuple[dict[str, Any], list[str]]] = []
    for index, seed in enumerate(seeds):
        family = str(seed["seed_family_id"])
        expected_owners = target_owners(seed, args.offset + index)
        existing_owners = {str(case.get("owner") or "") for case in generated.get(family, [])}
        missing_owners = [owner for owner in expected_owners if owner not in existing_owners]
        has_recorded_failure = any(str(row.get("seed_family_id") or "") == family for row in failures)
        if not missing_owners:
            continue
        if has_recorded_failure and not args.retry_failures:
            continue
        pending.append((seed, missing_owners))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {}
        for seed, owners in pending:
            future = pool.submit(request_variants, ep, seed, [environments[owner] for owner in owners], args.timeout, args.retries)
            futures[future] = (seed, owners)
        for future in concurrent.futures.as_completed(futures):
            seed, owners = futures[future]
            family = str(seed["seed_family_id"])
            failures = [
                row for row in failures
                if not (
                    str(row.get("seed_family_id") or "") == family
                    and (not row.get("owner") or str(row.get("owner")) in set(owners))
                )
            ]
            try:
                raw_cases = future.result()
                by_owner = {str(case.get("owner")): case for case in raw_cases if isinstance(case, dict)}
                valid_cases = []
                for index, owner in enumerate(owners):
                    try:
                        valid_cases.append(
                            validate_case(seed, owner, environments[owner], by_owner[owner], index + 1)
                        )
                    except Exception as exc:
                        failures.append({
                            "seed_family_id": seed["seed_family_id"],
                            "owner": owner,
                            "error": repr(exc),
                        })
                merged = {
                    str(case.get("owner") or ""): case
                    for case in generated.get(family, [])
                }
                merged.update({str(case.get("owner") or ""): case for case in valid_cases})
                generated[family] = list(merged.values())
                print(f"generated {seed['seed_family_id']} x{len(valid_cases)}/{len(owners)}", flush=True)
            except Exception as exc:
                failures.append({"seed_family_id": seed["seed_family_id"], "error": repr(exc)})
                print(f"failed {seed['seed_family_id']}: {exc!r}", flush=True)
            ordered = [case for item in selected_seeds for case in generated.get(item["seed_family_id"], [])]
            args.out.parent.mkdir(parents=True, exist_ok=True)
            temp = args.out.with_name(f".{args.out.name}.tmp")
            temp.write_text(json.dumps({"cases": ordered, "failures": failures}, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(args.out)
    ordered = [case for seed in selected_seeds for case in generated.get(seed["seed_family_id"], [])]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temp = args.out.with_name(f".{args.out.name}.tmp")
    temp.write_text(json.dumps({"cases": ordered, "failures": failures}, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(args.out)
    print(json.dumps({"seeds": len(selected_seeds), "cases": len(ordered), "turns": sum(len(case["turns"]) for case in ordered), "failures": len(failures)}, indent=2))


if __name__ == "__main__":
    main()
