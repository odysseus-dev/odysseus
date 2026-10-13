#!/usr/bin/env python3
"""Select diverse owner-bound seed families for a fixed-size cross-environment expansion."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


DOMAIN_CASE_QUOTAS = {
    "email": 28,
    "calendar": 24,
    "web": 20,
    "skills": 16,
    "memory": 16,
    "tasks": 16,
    "notes": 16,
    "documents": 16,
    "cookbook": 12,
    "sessions": 12,
    "admin": 12,
    "orchestration": 12,
}

DOMAIN_TOOLS = {
    "email": {"resolve_contact", "manage_contact"},
    "calendar": {"manage_calendar"},
    "web": {"web_search", "web_fetch", "private_browser", "youtube_tool", "trigger_research", "manage_research"},
    "skills": {"manage_skills"},
    "memory": {"manage_memory"},
    "tasks": {"manage_tasks"},
    "notes": {"manage_notes"},
    "documents": {"create_document", "edit_document", "update_document", "suggest_document", "manage_documents"},
    "cookbook": {
        "list_cookbook_servers", "list_served_models", "list_downloads", "list_cached_models",
        "list_serve_presets", "search_hf_models", "serve_preset", "serve_model", "stop_served_model",
        "download_model", "cancel_download", "adopt_served_model", "tail_serve_output",
    },
    "sessions": {"create_session", "list_sessions", "send_to_session", "manage_session", "search_chats"},
    "admin": {"manage_endpoints", "manage_mcp", "manage_tokens", "manage_webhooks", "manage_settings", "app_api"},
    "orchestration": {"chat_with_model", "ask_teacher", "pipeline", "update_plan"},
}


def seed_domains(seed: dict[str, Any]) -> set[str]:
    tools = set(seed.get("tools") or [])
    domains = {name for name, domain_tools in DOMAIN_TOOLS.items() if tools & domain_tools}
    if any(tool.startswith("mcp__email__") for tool in tools):
        domains.add("email")
    return domains


def score(seed: dict[str, Any], selected_tools: Counter[str], source_tools: Counter[str]) -> tuple[float, str]:
    tools = set(seed.get("tools") or [])
    rarity = sum(1.0 / max(1, source_tools[tool]) for tool in tools)
    balance = sum(1.0 / (1 + selected_tools[tool]) for tool in tools)
    turns = min(int(seed.get("turn_count") or 1), 4) * 0.03
    return rarity * 8 + balance + turns, str(seed.get("seed_family_id") or "")


def projected_cases(seed: dict[str, Any], environments_per_seed: int) -> int:
    return environments_per_seed if seed.get("owner_bound") is True else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--target-cases", type=int, default=200)
    parser.add_argument("--environments-per-seed", type=int, default=4)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    # Gallery image mutation is not yet transactionally reversible in the
    # expansion runner, so keep those seeds in the immutable source corpus but
    # do not synthesize additional live executions from them.
    candidates = [seed for seed in manifest["seeds"] if "edit_image" not in set(seed.get("tools") or [])]
    source_tools = Counter(tool for seed in candidates for tool in set(seed.get("tools") or []))
    selected_tools: Counter[str] = Counter()
    selected: list[dict[str, Any]] = []
    scaled_quotas = dict(DOMAIN_CASE_QUOTAS)
    quota_total = sum(scaled_quotas.values())
    if args.target_cases != quota_total:
        scaled_quotas = {
            domain: max(1, round(args.target_cases * quota / quota_total))
            for domain, quota in DOMAIN_CASE_QUOTAS.items()
        }
        while sum(scaled_quotas.values()) > args.target_cases:
            domain = max(scaled_quotas, key=lambda item: scaled_quotas[item])
            scaled_quotas[domain] -= 1
        while sum(scaled_quotas.values()) < args.target_cases:
            domain = min(scaled_quotas, key=lambda item: scaled_quotas[item])
            scaled_quotas[domain] += 1

    selected_ids: set[str] = set()
    domain_seed_counts: Counter[str] = Counter()
    domain_case_counts: Counter[str] = Counter()
    for domain, quota in scaled_quotas.items():
        while domain_case_counts[domain] < quota:
            eligible = [
                seed for seed in candidates
                if str(seed.get("seed_family_id")) not in selected_ids and domain in seed_domains(seed)
            ]
            if not eligible:
                break
            # Environment-specific seeds create four genuinely different cases;
            # prefer them except for global Cookbook inventory workflows.
            choice = max(
                eligible,
                key=lambda seed: (
                    domain not in {"cookbook", "orchestration"} and seed.get("owner_bound") is True,
                    score(seed, selected_tools, source_tools),
                ),
            )
            selected.append(choice)
            selected_ids.add(str(choice.get("seed_family_id")))
            selected_tools.update(set(choice.get("tools") or []))
            domain_seed_counts[domain] += 1
            domain_case_counts[domain] += projected_cases(choice, args.environments_per_seed)

    candidates = [seed for seed in candidates if str(seed.get("seed_family_id")) not in selected_ids]
    selected_case_count = sum(projected_cases(seed, args.environments_per_seed) for seed in selected)
    while candidates and selected_case_count < args.target_cases:
        choice = max(candidates, key=lambda seed: score(seed, selected_tools, source_tools))
        candidates.remove(choice)
        size = projected_cases(choice, args.environments_per_seed)
        if selected_case_count + size > args.target_cases:
            continue
        selected.append(choice)
        selected_tools.update(set(choice.get("tools") or []))
        selected_case_count += size

    payload = {
        "selection": {
            "target_cases": args.target_cases,
            "environments_per_seed": args.environments_per_seed,
            "selected_seeds": len(selected),
            "projected_cases": sum(projected_cases(seed, args.environments_per_seed) for seed in selected),
            "domain_seed_counts": dict(domain_seed_counts),
            "domain_case_counts": dict(domain_case_counts),
            "unfilled_domain_cases": {
                domain: quota - domain_case_counts[domain]
                for domain, quota in scaled_quotas.items()
                if domain_case_counts[domain] < quota
            },
            "tool_seed_counts": dict(selected_tools.most_common()),
        },
        "seeds": selected,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["selection"], indent=2))


if __name__ == "__main__":
    main()
