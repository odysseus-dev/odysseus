#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import random
import re
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib import request


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = Path(str(Path(__file__).resolve().parents[1] / "data" / "teacher_live_gaps" / "odysseus_v56_broad_web_teacher_20260821"))
DEFAULT_EVAL_OUT = REPO_ROOT / "data/evals/ody_v56_broad_web_teacher_heldout_20260821/cases.json"
DEFAULT_FAILURES = REPO_ROOT / "data/evals/ody_web_broad_live_search_v1_20260821/v56_targets/failure_targets.json"


WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for current or source-backed information.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
}


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def stable_id(prefix: str, obj: dict[str, Any]) -> str:
    payload = json.dumps(obj, sort_keys=True, ensure_ascii=True)
    return prefix + "_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def tool_call(name: str, arguments: dict[str, Any], suffix: str) -> dict[str, Any]:
    return {
        "id": f"call_{suffix}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments, separators=(",", ":"), ensure_ascii=True),
        },
    }


def source_block(query: str, rows: list[tuple[str, str]]) -> str:
    lines = [
        "```sources",
        *[f"[{idx}] {title}\n    https://example.test/{idx}" for idx, (title, _snippet) in enumerate(rows, start=1)],
        "```",
        "",
        "======================================================================",
        "WEB SEARCH RESULTS AND FETCHED CONTENT",
        f"Query: {query}",
        f"Searched {len(rows)} results, fetched {len(rows)} pages",
        "======================================================================",
        "",
        "SEARCH RESULTS SUMMARY:",
        "--------------------------------------------------",
    ]
    for idx, (title, snippet) in enumerate(rows, start=1):
        lines.extend([f"[{idx}] {title}", f"    URL: https://example.test/{idx}", f"    Snippet: {snippet}", ""])
    return "\n".join(lines).strip()


ANCHORS: list[dict[str, Any]] = [
    {
        "case_id": "web_current_exchange_rate_01",
        "family": "current_numeric_conversion",
        "topic": "JPY to EUR current conversion",
        "users": [
            "how much is 10000 yen in euros today?",
            "convert 10,000 JPY to EUR using today's rate",
            "what is 10000 yen worth in euro right now?",
            "yen to euros today, 10000 yen",
        ],
        "query": "10000 JPY to EUR today exchange rate",
        "rows": [
            ("JPY to EUR currency converter", "1 JPY = 0.005385 EUR on Aug 21, 2026; 10,000 JPY equals about 53.85 EUR before fees."),
            ("Japanese Yen to Euro exchange rate", "The current exchange rate is roughly 0.00538 euro per Japanese yen."),
            ("Currency conversion note", "Live exchange rates move through the day, so conversions should be treated as approximate."),
        ],
        "final": "At a rate around 1 JPY = 0.005385 EUR, 10,000 yen is about 53.85 euros before fees. The exact amount can move a little with the live exchange rate.",
        "answer_any": ["eur", "euro", "€"],
        "answer_any_2": ["10000", "10,000", "yen", "JPY"],
    },
    {
        "case_id": "web_fact_vaccine_mechanism_01",
        "family": "evergreen_synthesis",
        "topic": "how vaccines work",
        "users": [
            "how do vaccines work? quick explanation",
            "explain how vaccines train your immune system",
            "look up how vaccines work and summarize simply",
            "what do vaccines do in the body?",
        ],
        "query": "vaccines work immune system antigen antibodies memory cells protection",
        "rows": [
            ("How vaccines work", "Vaccines expose the immune system to a harmless antigen or instructions to make one."),
            ("Immune memory", "Vaccination helps form antibodies and memory cells so the body recognizes the pathogen faster later."),
            ("Vaccine protection", "Vaccines build protection without requiring the person to get the disease first."),
        ],
        "final": "Vaccines show your immune system a harmless antigen, or instructions for one. That helps the body make antibodies and memory cells, so it can recognize the real germ faster later.",
        "answer_any": ["immune system", "antigen", "antibodies"],
        "answer_any_2": ["memory", "protection", "recognize"],
    },
    {
        "case_id": "web_fact_onion_tears_chemistry_01",
        "family": "evergreen_synthesis",
        "topic": "onion tears chemical",
        "users": [
            "what chemical makes your eyes water when chopping onions?",
            "why do onions make you cry and what chemical is it?",
            "look up the onion tear chemical",
            "what makes onion fumes irritate your eyes?",
        ],
        "query": "onions chopping eyes water syn-propanethial-S-oxide lachrymatory sulfur irritates eyes tears",
        "rows": [
            ("Onion lachrymatory factor", "Cut onions form syn-Propanethial-S-oxide, a sulfur-containing lachrymatory factor."),
            ("Why onions cause tears", "The volatile chemical reaches the eyes, irritates nerve endings, and triggers tears."),
            ("Onion chemistry", "Enzymes and sulfur compounds mix when onion cells are cut, creating the eye-irritating gas."),
        ],
        "final": "Chopped onions make syn-propanethial-S-oxide, a sulfur-based lachrymatory chemical. It irritates your eyes, so your tear glands water to dilute and flush it away.",
        "answer_any": ["syn-propanethial", "sulfur", "lachrymatory"],
        "answer_any_2": ["eyes", "tears", "irritates"],
    },
    {
        "case_id": "web_fact_tallest_mountain_01",
        "family": "evergreen_synthesis",
        "topic": "tallest mountain above sea level",
        "users": [
            "what is the tallest mountain above sea level?",
            "which mountain is highest measured from sea level?",
            "look up the tallest mountain and its height",
            "how tall is the highest mountain on earth?",
        ],
        "query": "tallest mountain above sea level Mount Everest 8848.86 meters",
        "rows": [
            ("Mount Everest height", "Mount Everest is the tallest mountain above sea level at 8,848.86 meters."),
            ("Highest mountain on Earth", "Everest's summit is 29,031.7 feet above mean sea level."),
            ("Above sea level ranking", "Mount Everest is highest by elevation above sea level, while other mountains can be taller by base-to-peak measures."),
        ],
        "final": "Mount Everest is the tallest mountain above sea level, at about 8,848.86 meters, or 29,031.7 feet.",
        "answer_any": ["Everest"],
        "answer_any_2": ["8,848", "8848", "8,849", "8849"],
    },
    {
        "case_id": "web_obscure_product_lookup_01",
        "family": "obscure_lookup_uncertain",
        "topic": "Citizen watch dating uncertainty",
        "users": [
            "what year is a Citizen 4-820789Y watch likely from?",
            "can you date a Citizen 4-820789Y watch?",
            "look up Citizen 4-820789Y and tell me the likely year",
            "what does Citizen watch code 4-820789Y say about the year?",
        ],
        "query": "Citizen 4-820789Y watch case model serial date year",
        "rows": [
            ("Citizen watch serial dating", "Citizen watches are usually dated from the caseback serial number plus the movement or case code."),
            ("Citizen case code", "A code like 4-820789Y is a movement/case reference, not enough by itself to identify one exact year."),
            ("How to date Citizen watches", "The serial number's first digit often gives the year within a decade; the full caseback is needed to resolve the decade."),
        ],
        "final": "I cannot verify one exact year from 4-820789Y alone. That looks like a Citizen movement/case code; to date it, use the full caseback serial number plus the case/model code to resolve the decade.",
        "answer_any": ["serial", "case", "model", "year", "cannot", "likely"],
        "answer_any_2": ["Citizen", "watch", "code"],
    },
    {
        "case_id": "web_synthesis_no_source_dump_01",
        "family": "practical_synthesis",
        "topic": "sourdough starter acetone smell",
        "users": [
            "look up why sourdough starter smells like acetone and tell me what to do",
            "my sourdough starter smells like nail polish remover, what should I do?",
            "why does sourdough starter smell like acetone?",
            "search sourdough acetone smell fix",
        ],
        "query": "sourdough starter acetone nail polish smell hungry feed discard fix",
        "rows": [
            ("Sourdough acetone smell", "An acetone or nail polish remover smell usually means the starter is hungry and producing alcohol or other stress byproducts."),
            ("Fix sourdough starter smell", "Discard some starter, feed it fresh flour and water, and repeat regular feedings until the smell becomes pleasantly yeasty or tangy."),
            ("When to throw starter away", "Discard the starter if it shows fuzzy mold, pink/orange discoloration, or a rotten odor."),
        ],
        "final": "An acetone smell usually means the starter is hungry or stressed, not automatically ruined. Discard part of it, feed with fresh flour and water, and feed more often; throw it out only if you see mold, pink/orange discoloration, or rotten smells.",
        "answer_any": ["hungry", "feed", "starter", "discard"],
        "answer_any_2": ["acetone", "nail polish", "mold"],
    },
    {
        "case_id": "web_context_no_stale_query_01",
        "family": "context_followup_prevention",
        "topic": "prevent onion tears contextual follow-up",
        "users": [
            "is there a way to stop it?",
            "how do I prevent it?",
            "can I stop that from happening?",
            "what helps with that?",
        ],
        "prior_user": "what causes onions to make you cry",
        "query": "onions prevent crying stop tears chill sharp knife ventilation goggles water",
        "rows": [
            ("How to stop onion tears", "Chilling onions, using a sharp knife, and improving ventilation can reduce the gas that reaches your eyes."),
            ("Prevent onion crying", "Cut near running water or under a vent, avoid crushing the onion, and consider goggles for sensitive eyes."),
            ("Onion tear prevention", "The goal is to reduce release of syn-propanethial-S-oxide or keep it away from your eyes."),
        ],
        "final": "Yes. Chill the onion first, use a sharp knife so you crush fewer cells, keep air moving with a vent or fan, and use goggles if you are sensitive. Those steps reduce or block the tear-causing onion vapor.",
        "answer_any": ["chill", "knife", "ventilation", "water", "goggles", "cut"],
        "answer_any_2": ["onion", "tears", "cry"],
    },
]


BAD_QUERY_ROWS = [
    ("why", "WHY | English meaning", "Dictionary entry for the word why, unrelated to the user's topic."),
    ("official weather", "OFFICIAL | English meaning", "Dictionary entry for official, not a weather forecast."),
    ("safe touch", "SafeTouch Security Systems", "A home security company page, unrelated to handling animals or food."),
    ("browser does without looking", "Browser download page", "A browser marketing page, even though the user asked not to look it up."),
]


NEGATIVE_NO_TOOL_ROWS = [
    ("explain what a web browser does without looking it up", "A web browser is an app that fetches web pages, renders HTML/CSS, runs JavaScript, and lets you navigate the web. No lookup needed for that basic explanation."),
    ("without searching, what does a browser do?", "A browser requests pages from websites, displays them, runs page scripts, and manages things like tabs, history, cookies, and downloads."),
    ("answer from memory: what is a web search engine?", "A web search engine crawls and indexes pages, then ranks matching results when you type a query."),
]


def deepseek_endpoint() -> dict[str, str] | None:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if api_key:
        return {
            "name": "env-deepseek",
            "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            "api_key": api_key,
            "cached_models": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        }
    db_path = REPO_ROOT / "data/app.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT name, base_url, api_key, cached_models
            FROM model_endpoints
            WHERE lower(name) LIKE '%deepseek%'
              AND COALESCE(is_enabled, 0) = 1
              AND COALESCE(api_key, '') != ''
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        return {
            "name": row["name"],
            "base_url": row["base_url"],
            "api_key": row["api_key"],
            "cached_models": row["cached_models"] or "deepseek-chat",
        }
    finally:
        conn.close()


def call_deepseek(endpoint: dict[str, str], prompt: dict[str, Any]) -> list[dict[str, str]]:
    model = "deepseek-chat"
    with contextlib.suppress(Exception):
        cached = json.loads(endpoint.get("cached_models") or "[]")
        if isinstance(cached, list) and cached:
            model = cached[0]
        elif isinstance(cached, str) and cached:
            model = cached
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Return strict JSON only. No markdown. Do not reveal secrets."},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        "temperature": 0.55,
        "max_tokens": 4500,
    }
    req = request.Request(
        endpoint["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {endpoint['api_key']}"},
        method="POST",
    )
    with request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    content = body["choices"][0]["message"]["content"]
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", clean(content), flags=re.I | re.S)
    if not cleaned.startswith("{"):
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if match:
            cleaned = match.group(0)
    parsed = json.loads(cleaned)
    rows = parsed.get("rows", [])
    return [row for row in rows if isinstance(row, dict)]


def teacher_variants(anchor: dict[str, Any], count: int, endpoint: dict[str, str] | None) -> list[dict[str, str]]:
    fallback = [{"user": user, "final": anchor["final"]} for user in anchor["users"]]
    while len(fallback) < count:
        fallback.append({
            "user": anchor["users"][len(fallback) % len(anchor["users"])],
            "final": anchor["final"],
        })
    if endpoint is None:
        return fallback[:count]
    prompt = {
        "task": "Generate varied SFT phrasings for an Odysseus web tool-use model.",
        "count": count,
        "topic": anchor["topic"],
        "source_failure": "Current model often searched correctly but returned empty text, clipped snippets, stale query terms, or failed to synthesize the actual answer.",
        "requirements": [
            "Return JSON object with rows list.",
            "Each row has user and final only.",
            "User should be casual and varied; include some short phrasing and mild typos.",
            "Final must be concise, direct, and answer from evidence.",
            "Final must not mention snippets, links, sources, or WEB SEARCH RESULTS.",
            "Do not include private names, emails, secrets, or API keys.",
        ],
        "ideal_query": anchor["query"],
        "prior_user": anchor.get("prior_user", ""),
        "evidence": [snippet for _title, snippet in anchor["rows"]],
        "must_include_one_of": anchor["answer_any"],
        "must_include_one_of_second_group": anchor["answer_any_2"],
        "example_final_style": anchor["final"],
    }
    with contextlib.suppress(Exception):
        rows = call_deepseek(endpoint, prompt)
        valid: list[dict[str, str]] = []
        for row in rows:
            user = clean(row.get("user"))
            final = clean(row.get("final"))
            if len(user.split()) >= 3 and final and not re.search(r"WEB SEARCH RESULTS|```sources|links?|snippet", final, re.I):
                valid.append({"user": user, "final": final})
        if len(valid) >= max(3, count // 2):
            return (valid + fallback)[:count]
    return fallback[:count]


def sft_row(category: str, messages: list[dict[str, Any]], expected_calls: int, metadata: dict[str, Any]) -> dict[str, Any]:
    item = {
        "messages": messages,
        "tools": [WEB_SEARCH_TOOL] if expected_calls else [],
        "generator": "deepseek_teacher_v56_broad_web",
        "metadata": {
            "category": category,
            "split": "train_or_val",
            "expected_tool_calls": expected_calls,
            **metadata,
        },
    }
    item["uuid"] = stable_id("ody_v56_broad_web", item)
    return item


def build_rows(endpoint: dict[str, str] | None, per_anchor: int, retry_per_anchor: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    raw: dict[str, Any] = {"provider": endpoint["name"] if endpoint else "deterministic_fallback", "anchors": []}
    for anchor_idx, anchor in enumerate(ANCHORS):
        variants = teacher_variants(anchor, per_anchor, endpoint)
        raw["anchors"].append({"case_id": anchor["case_id"], "topic": anchor["topic"], "rows": variants})
        for idx, variant in enumerate(variants):
            call = tool_call("web_search", {"query": anchor["query"]}, f"synth_{anchor_idx}_{idx}")
            messages: list[dict[str, Any]] = []
            if anchor.get("prior_user"):
                messages.extend([
                    {"role": "user", "content": anchor["prior_user"]},
                    {"role": "assistant", "content": anchor.get("prior_answer", "I can look that up or explain it briefly.")},
                ])
            messages.extend([
                {"role": "user", "content": variant["user"]},
                {"role": "assistant", "content": "", "tool_calls": [call]},
                {"role": "tool", "tool_call_id": call["id"], "content": source_block(anchor["query"], anchor["rows"])},
                {"role": "assistant", "content": variant["final"]},
            ])
            rows.append(sft_row(anchor["family"], messages, 1, {
                "source_case_ids": [anchor["case_id"]],
                "query_must_include": anchor["query"].split()[:6],
                "answer_must_include": anchor["answer_any"] + anchor["answer_any_2"],
            }))
        for idx in range(retry_per_anchor):
            bad_query, title, snippet = BAD_QUERY_ROWS[(anchor_idx + idx) % len(BAD_QUERY_ROWS)]
            first = tool_call("web_search", {"query": bad_query}, f"retry_{anchor_idx}_{idx}_bad")
            second = tool_call("web_search", {"query": anchor["query"]}, f"retry_{anchor_idx}_{idx}_good")
            messages = [
                {"role": "user", "content": anchor["users"][idx % len(anchor["users"])]},
                {"role": "assistant", "content": "", "tool_calls": [first]},
                {"role": "tool", "tool_call_id": first["id"], "content": source_block(bad_query, [(title, snippet)])},
                {"role": "assistant", "content": "", "tool_calls": [second]},
                {"role": "tool", "tool_call_id": second["id"], "content": source_block(anchor["query"], anchor["rows"])},
                {"role": "assistant", "content": anchor["final"]},
            ]
            rows.append(sft_row("web_retry_bad_or_stale_query_then_synthesize", messages, 2, {
                "source_case_ids": [anchor["case_id"]],
                "bad_query": bad_query,
                "query_must_include": anchor["query"].split()[:6],
                "answer_must_include": anchor["answer_any"] + anchor["answer_any_2"],
            }))
    for idx, (user, final) in enumerate(NEGATIVE_NO_TOOL_ROWS):
        rows.append(sft_row("negative_explicit_no_web", [
            {"role": "user", "content": user},
            {"role": "assistant", "content": final},
        ], 0, {
            "source_case_ids": ["web_no_tool_memory_answer_01"],
            "forbidden_tools": ["web_search", "web_fetch"],
        }))
    return rows, raw


def build_eval_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for idx, anchor in enumerate(ANCHORS):
        case: dict[str, Any] = {
            "id": f"v56_broad_web_anchor_{idx:02d}_{anchor['family']}",
            "kind": "web",
            "user": anchor["users"][0],
            "expect_first_tool": "web_search",
            "must_query_any": anchor["query"].split()[:3],
            "must_answer_any": anchor["answer_any"],
            "must_answer_any_2": anchor["answer_any_2"],
            "forbidden_final": ["WEB SEARCH RESULTS", "```sources", "Here are links", "SEARCH RESULTS SUMMARY"],
            "max_web_searches": 2,
        }
        if anchor.get("prior_user"):
            case["prior_turns"] = [anchor["prior_user"]]
        cases.append(case)
    cases.append({
        "id": "v56_broad_web_negative_no_lookup",
        "kind": "chat",
        "user": NEGATIVE_NO_TOOL_ROWS[0][0],
        "expect_no_tool": True,
        "forbidden_tools": ["web_search", "web_fetch"],
        "must_answer_any": ["browser", "web", "pages"],
    })
    return cases


def split_rows(rows: list[dict[str, Any]], val_every: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    train: list[dict[str, Any]] = []
    val: list[dict[str, Any]] = []
    for idx, item in enumerate(rows):
        (val if idx % val_every == val_every - 1 else train).append(item)
    return train, val


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(item, ensure_ascii=True) + "\n" for item in rows), encoding="utf-8")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--eval-out", type=Path, default=DEFAULT_EVAL_OUT)
    parser.add_argument("--failure-targets", type=Path, default=DEFAULT_FAILURES)
    parser.add_argument("--per-anchor", type=int, default=18)
    parser.add_argument("--retry-per-anchor", type=int, default=4)
    parser.add_argument("--val-every", type=int, default=6)
    parser.add_argument("--seed", type=int, default=56)
    args = parser.parse_args()

    started = time.time()
    rng = random.Random(args.seed)
    endpoint = deepseek_endpoint()
    rows, raw = build_rows(endpoint, args.per_anchor, args.retry_per_anchor)
    rng.shuffle(rows)
    train, val = split_rows(rows, args.val_every)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.out_dir / "train.jsonl", train)
    write_jsonl(args.out_dir / "val.jsonl", val)
    write_jsonl(args.out_dir / "all.jsonl", rows)
    (args.out_dir / "raw_teacher.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    failure_target_payload: dict[str, Any] = {}
    if args.failure_targets.exists():
        failure_target_payload = json.loads(args.failure_targets.read_text(encoding="utf-8"))

    eval_cases = build_eval_cases()
    args.eval_out.parent.mkdir(parents=True, exist_ok=True)
    args.eval_out.write_text(
        json.dumps({
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "generator": Path(__file__).name,
            "source": "V55 broad web live-search gate failures.",
            "source_failure_targets": str(args.failure_targets),
            "cases": eval_cases,
        }, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )

    categories = sorted({item["metadata"]["category"] for item in rows})
    manifest = {
        "name": args.out_dir.name,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "provider": raw["provider"],
        "elapsed_seconds": round(time.time() - started, 3),
        "total_sft_rows": len(rows),
        "train_rows": len(train),
        "val_rows": len(val),
        "heldout_cases": len(eval_cases),
        "categories": {category: sum(1 for item in rows if item["metadata"]["category"] == category) for category in categories},
        "source_eval": failure_target_payload.get("generated_from", str(args.failure_targets)),
        "source_case_ids": [anchor["case_id"] for anchor in ANCHORS] + ["web_no_tool_memory_answer_01"],
        "acceptance_target": (
            "V56 must improve broad web live-search gate first; focused live regressions and old CRUD are regression checks."
        ),
        "files": {
            "train": str(args.out_dir / "train.jsonl"),
            "val": str(args.out_dir / "val.jsonl"),
            "all": str(args.out_dir / "all.jsonl"),
            "raw_teacher": str(args.out_dir / "raw_teacher.json"),
            "heldout_eval": str(args.eval_out),
            "failure_targets": str(args.failure_targets),
        },
    }
    for key, value in list(manifest["files"].items()):
        path = Path(value)
        if path.exists():
            manifest[f"{key}_sha256"] = file_sha256(path)
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "provider": manifest["provider"],
        "total_sft_rows": manifest["total_sft_rows"],
        "train_rows": manifest["train_rows"],
        "val_rows": manifest["val_rows"],
        "heldout_cases": manifest["heldout_cases"],
        "categories": manifest["categories"],
        "source_case_ids": manifest["source_case_ids"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
