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
DEFAULT_OUT = Path(str(Path(__file__).resolve().parents[1] / "data" / "teacher_live_gaps" / "odysseus_v55_web_synthesis_teacher_20260821"))
DEFAULT_EVAL_OUT = REPO_ROOT / "data/evals/ody_v55_web_synthesis_teacher_heldout_20260821/cases.json"


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


def stable_id(prefix: str, obj: dict[str, Any]) -> str:
    payload = json.dumps(obj, sort_keys=True, ensure_ascii=True)
    return prefix + "_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


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
        "family": "animal_foam_synthesis",
        "topic": "sea cucumber defensive foam/sticky secretions",
        "users": [
            "why do sea creatures like sea cucumbers produce foam?",
            "why do sea cucumbers shoot out sticky foamy stuff?",
            "what is the foam/stringy stuff sea cucumbers produce for?",
        ],
        "query": "sea cucumber sticky foam mucus defense cuvierian tubules predators",
        "rows": [
            ("Sea cucumber defense", "Sea cucumbers use chemical defenses and can eject sticky Cuvierian tubules to tangle or deter predators."),
            ("Cuvierian tubules", "Some sea cucumbers expel sticky mucus-like threads from the Cuvierian organ as a defensive response."),
            ("Marine animal mucus", "Foam or froth around marine animals is often mucus or secretions mixed with water and air during stress or defense."),
        ],
        "final": "Sea cucumbers do it mainly as a defense response. What looks like foam is usually sticky mucus-like secretions or Cuvierian tubules mixed with water and air, used to distract, tangle, or deter predators.",
        "answer_any": ["mucus", "foam", "sticky", "tubules"],
        "answer_any_2": ["defense", "predator", "protect", "stress"],
    },
    {
        "family": "animal_foam_synthesis",
        "topic": "slug bubbles when touched",
        "users": [
            "search and tell me why some slugs bubble up when touched",
            "why do slugs foam when you touch them?",
            "look up why slugs bubble when disturbed",
        ],
        "query": "slug bubble foam mucus when touched stress defense predator",
        "rows": [
            ("Slug mucus", "Slugs produce mucus over their bodies; when disturbed, air can mix with the mucus and make bubbles or foam."),
            ("Slug defense", "Many slugs secrete thicker or stickier mucus when threatened, which can deter predators and protect the body."),
            ("Gastropod stress response", "Handling, salt, dryness, or irritation can increase mucus secretion in slugs and snails."),
        ],
        "final": "Slugs bubble when touched because disturbance can trigger extra mucus, and air gets mixed into it as foam. It is mostly a stress or defense response that helps protect the slug and discourage predators.",
        "answer_any": ["mucus", "foam", "bubble", "slime"],
        "answer_any_2": ["stress", "defense", "predator", "protect"],
    },
    {
        "family": "animal_foam_synthesis",
        "topic": "snail froth around body",
        "users": [
            "why do snails make froth around themselves?",
            "what is the froth that comes out of snails?",
            "why does my garden snail have white foam around it?",
        ],
        "query": "snail froth foam mucus stress irritation defense moisture",
        "rows": [
            ("Why snails bubble", "Land snails can release bubbly mucus when stressed, irritated, handled, exposed to salt, or drying out."),
            ("Snail slime", "Snail slime is mucus; it helps movement, moisture retention, and protection from irritation."),
            ("Foam in snails", "The froth is mucus mixed with air, usually a defensive or stress response rather than a separate substance."),
        ],
        "final": "The froth is snail mucus mixed with air. Snails can make it when stressed, irritated, handled, too dry, or exposed to salt or chemicals; it helps protect them and retain moisture.",
        "answer_any": ["mucus", "foam", "froth", "bubble"],
        "answer_any_2": ["stress", "defense", "irritat", "moisture", "salt"],
    },
    {
        "family": "public_fact_synthesis",
        "topic": "vaccines immune system",
        "users": [
            "how do vaccines work? give me a quick explanation",
            "look up how vaccines work and summarize it simply",
            "quickly explain how vaccines train your immune system",
        ],
        "query": "vaccines work immune system antigen antibodies memory cells",
        "rows": [
            ("How vaccines work", "Vaccines expose the immune system to a harmless antigen or instructions to make one, so the body can practice recognizing it."),
            ("Immune memory", "After vaccination, immune cells and antibodies can respond faster if the real pathogen appears later."),
            ("CDC vaccine basics", "Vaccines help the body build protection without having to get the disease first."),
        ],
        "final": "Vaccines show your immune system a harmless antigen, or instructions to make one, so it can build antibodies and memory cells. Later, if the real germ shows up, your immune system recognizes it faster.",
        "answer_any": ["immune system", "antigen", "antibodies"],
        "answer_any_2": ["memory", "recognizes", "protection"],
    },
    {
        "family": "public_fact_synthesis",
        "topic": "biggest animal ever",
        "users": [
            "whats the biggest animal ever?",
            "look up the biggest animal ever and answer short",
            "what is the largest animal that ever lived?",
        ],
        "query": "largest biggest animal ever blue whale weight 200 tons",
        "rows": [
            ("Largest animals", "The blue whale is generally considered the largest animal ever known to have lived."),
            ("Blue whale size", "Blue whales can reach about 30 meters long and weigh roughly 180 to 200 tonnes."),
            ("Biggest animal ever", "No dinosaur currently beats the blue whale for overall body mass."),
        ],
        "final": "The blue whale is the biggest animal ever known. The largest individuals can reach about 30 meters long and roughly 180-200 tonnes.",
        "answer_any": ["blue whale"],
        "answer_any_2": ["200", "ton", "30 meters"],
    },
    {
        "family": "public_fact_synthesis",
        "topic": "onions crying chemistry",
        "users": [
            "why do onions make you cry?",
            "look up why chopping onions makes eyes water",
            "what chemical in onions makes people cry?",
        ],
        "query": "onions make you cry syn-propanethial-S-oxide irritates eyes lachrymatory factor",
        "rows": [
            ("Onion lachrymatory factor", "Cutting onions releases enzymes that form syn-Propanethial-S-oxide, a volatile lachrymatory factor."),
            ("Why onions cause tears", "The gas reaches the eyes, irritates nerve endings, and the eyes produce tears to flush it away."),
            ("Onion chemistry", "Sulfur compounds in onion cells mix after cutting and create the eye-irritating vapor."),
        ],
        "final": "Cut onions produce syn-propanethial-S-oxide, a volatile sulfur compound. It irritates your eyes, so your tear glands make tears to dilute and wash it away.",
        "answer_any": ["syn-propanethial-S-oxide", "sulfur"],
        "answer_any_2": ["irritates", "eyes", "tears"],
    },
    {
        "family": "public_fact_synthesis",
        "topic": "wifi simple explanation",
        "users": [
            "how does wifi work? short explanation",
            "look up how wifi works and explain simply",
            "what does a wifi router actually do?",
        ],
        "query": "how wifi works radio waves router 2.4 GHz 5 GHz devices",
        "rows": [
            ("How Wi-Fi works", "Wi-Fi uses radio waves to send data between a router/access point and devices."),
            ("Wi-Fi frequencies", "Common Wi-Fi bands include 2.4 GHz and 5 GHz, with newer systems also using 6 GHz."),
            ("Wireless networking", "The router connects local wireless devices to the internet and manages packets between them."),
        ],
        "final": "Wi-Fi sends data by radio waves between your device and a router. The router talks to devices on bands like 2.4 GHz or 5 GHz, then forwards that traffic to and from the internet.",
        "answer_any": ["radio waves"],
        "answer_any_2": ["router", "2.4 GHz", "5 GHz"],
    },
    {
        "family": "public_fact_synthesis",
        "topic": "speed of light",
        "users": [
            "what is the speed of light? quick",
            "look up the exact speed of light",
            "speed of light in vacuum please",
        ],
        "query": "speed of light vacuum 299792458 meters per second",
        "rows": [
            ("Speed of light", "The speed of light in vacuum is exactly 299,792,458 meters per second."),
            ("Physical constant c", "The defined value of c is 299,792,458 m/s."),
            ("Light speed", "In everyday terms, light travels about 300,000 kilometers per second in vacuum."),
        ],
        "final": "In vacuum, the speed of light is exactly 299,792,458 meters per second, about 300,000 km/s.",
        "answer_any": ["299", "792", "458"],
        "answer_any_2": ["meters per second", "km/s", "vacuum"],
    },
]


BAD_QUERY_ROWS = [
    ("official links", "Official link directory", "A URL shortener and link directory; it does not answer the user's question."),
    ("scientific links", "Scientific link collection", "Generic source list with no answer details."),
    ("why", "WHY | English meaning", "Dictionary entry for the word why, unrelated to the user's topic."),
    ("biggest", "BIGGEST | English meaning", "Dictionary entry for the word biggest, not an answer."),
    ("Wikipedia Python packaging packaging.python.org PyPI pip setuptools build", "Python Packaging User Guide", "Python package publishing docs; unrelated to the user's question."),
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
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Return strict JSON only. No markdown."},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        "temperature": 0.55,
        "max_tokens": 5000,
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
    fallback: list[dict[str, str]] = []
    prefixes = ["", "quick: ", "can you search this: ", "look this up and summarize: "]
    for idx in range(count):
        user = prefixes[idx % len(prefixes)] + anchor["users"][idx % len(anchor["users"])]
        fallback.append({"user": user, "final": anchor["final"]})
    if endpoint is None:
        return fallback
    prompt = {
        "task": "Generate varied SFT phrasings for a web-search tool-use model.",
        "count": count,
        "topic": anchor["topic"],
        "source_failure": "Current model searches, then dumps snippets instead of synthesizing a concise answer.",
        "requirements": [
            "Return JSON object with rows list.",
            "Each row has user and final only.",
            "User should be casual and varied; some can include typos.",
            "Final must be concise, direct, and answer from evidence.",
            "Final must not mention snippets, sources, WEB SEARCH RESULTS, or links.",
            "Do not include private names, emails, secrets, or exact API keys.",
        ],
        "ideal_query": anchor["query"],
        "evidence": [snippet for _title, snippet in anchor["rows"]],
        "must_include_one_of": anchor["answer_any"],
        "must_include_one_of_second_group": anchor["answer_any_2"],
        "example_final_style": anchor["final"],
    }
    with contextlib.suppress(Exception):
        rows = call_deepseek(endpoint, prompt)
        valid = []
        for row in rows:
            user = clean(row.get("user"))
            final = clean(row.get("final"))
            if len(user.split()) >= 3 and final and not re.search(r"WEB SEARCH RESULTS|```sources|links?", final, re.I):
                valid.append({"user": user, "final": final})
        if len(valid) >= max(3, count // 2):
            return (valid + fallback)[:count]
    return fallback


def row(category: str, messages: list[dict[str, Any]], expected_calls: int, anchor: dict[str, Any], source_ids: list[str]) -> dict[str, Any]:
    item = {
        "messages": messages,
        "tools": [WEB_SEARCH_TOOL] if expected_calls else [],
        "generator": "deepseek_teacher_v55_web_synthesis",
        "metadata": {
            "category": category,
            "split": "train_or_val",
            "expected_tool_calls": expected_calls,
            "query_must_include": anchor["query"].split()[:5],
            "answer_must_include": anchor["answer_any"] + anchor["answer_any_2"],
            "source_case_ids": source_ids,
        },
    }
    item["uuid"] = stable_id("ody_v55_web_synth", item)
    return item


def build_rows(endpoint: dict[str, str] | None, per_anchor: int, retry_per_anchor: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    raw: dict[str, Any] = {"provider": endpoint["name"] if endpoint else "deterministic_fallback", "anchors": []}
    source_ids = [
        "v54_live_gap_web_synthesis_animal_foam_01",
        "v54_live_gap_web_synthesis_animal_foam_04",
        "v54_live_gap_web_synthesis_animal_foam_05",
        "v54_live_gap_web_synthesis_animal_foam_10",
        "v54_live_gap_web_retry_after_weak_results_00",
        "v54_live_gap_web_retry_after_weak_results_01",
        "v54_live_gap_web_retry_after_weak_results_05",
    ]
    for anchor_idx, anchor in enumerate(ANCHORS):
        variants = teacher_variants(anchor, per_anchor, endpoint)
        raw["anchors"].append({"topic": anchor["topic"], "rows": variants})
        for idx, variant in enumerate(variants):
            call = tool_call("web_search", {"query": anchor["query"]}, f"synth_{anchor_idx}_{idx}")
            messages = [
                {"role": "user", "content": variant["user"]},
                {"role": "assistant", "content": "", "tool_calls": [call]},
                {"role": "tool", "tool_call_id": call["id"], "content": source_block(anchor["query"], anchor["rows"])},
                {"role": "assistant", "content": variant["final"]},
            ]
            rows.append(row("web_compress_noisy_results", messages, 1, anchor, source_ids))
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
            rows.append(row("web_retry_bad_query_then_synthesize", messages, 2, anchor, source_ids))
    return rows, raw


def build_eval_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for idx, anchor in enumerate(ANCHORS):
        cases.append({
            "id": f"v55_web_synthesis_anchor_{idx:02d}",
            "kind": "web",
            "user": anchor["users"][0],
            "forbidden_final": ["WEB SEARCH RESULTS", "```sources", "Here are links", "not enough clear evidence"],
            "must_answer_any": anchor["answer_any"],
            "must_answer_any_2": anchor["answer_any_2"],
            "max_web_searches": 2,
        })
    for idx, anchor in enumerate(ANCHORS[:5]):
        cases.append({
            "id": f"v55_web_retry_anchor_{idx:02d}",
            "kind": "web",
            "user": "search properly and answer: " + anchor["users"][1],
            "expect_first_tool": "web_search",
            "forbidden_query_any": ["official links", "scientific links", "python packaging", "dictionary"],
            "must_answer_any": anchor["answer_any"],
            "must_answer_any_2": anchor["answer_any_2"],
            "forbidden_final": ["WEB SEARCH RESULTS", "```sources", "Here are links", "not enough clear evidence"],
            "max_web_searches": 2,
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
    parser.add_argument("--per-anchor", type=int, default=14)
    parser.add_argument("--retry-per-anchor", type=int, default=4)
    parser.add_argument("--val-every", type=int, default=6)
    parser.add_argument("--seed", type=int, default=55)
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

    eval_cases = build_eval_cases()
    args.eval_out.parent.mkdir(parents=True, exist_ok=True)
    args.eval_out.write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "generator": Path(__file__).name,
                "source": "V54 live heldout failures where search ran but final synthesis missed answer terms.",
                "cases": eval_cases,
            },
            ensure_ascii=True,
            indent=2,
        )
        + "\n",
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
        "source_eval": "data/evals/ody_v54_live_gap_topup_gate_20260821_1555_queryguard2/live_gap_heldout/actual_results.json",
        "source_case_ids": rows[0]["metadata"]["source_case_ids"] if rows else [],
        "acceptance_target": (
            "V55 must pass user-reported web 3/3, V54 live-gap heldout, V55 synthesis heldout, "
            "and old CRUD regression before replacing V53/V54."
        ),
        "files": {
            "train": str(args.out_dir / "train.jsonl"),
            "val": str(args.out_dir / "val.jsonl"),
            "all": str(args.out_dir / "all.jsonl"),
            "raw_teacher": str(args.out_dir / "raw_teacher.json"),
            "heldout_eval": str(args.eval_out),
        },
    }
    for key, value in list(manifest["files"].items()):
        manifest[f"{key}_sha256"] = file_sha256(Path(value))
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("provider", "total_sft_rows", "train_rows", "val_rows", "heldout_cases", "categories", "source_case_ids")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
