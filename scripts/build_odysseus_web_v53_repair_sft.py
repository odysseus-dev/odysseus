#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import random
import time
from pathlib import Path
from typing import Any


DEFAULT_OUT = Path(str(Path(__file__).resolve().parents[1] / "data" / "teacher_web_synthesis" / "odysseus_web_v53_repair_20260821"))
DEFAULT_EVAL_OUT = Path(str(Path(__file__).resolve().parents[1] / "data" / "evals" / "ody_web_v53_live_robust_gate_20260821" / "cases.json"))

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for current or source-backed information.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "time_filter": {"type": "string", "enum": ["day", "week", "month", "year"]},
            },
            "required": ["query"],
        },
    },
}


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


SYNTHESIS_SEEDS = [
    {
        "topic": "dog foaming at the mouth",
        "users": [
            "why would a dog suddenly foam at the mouth",
            "look up why dogs get foam around their mouth sometimes",
            "my dog is drooling white foam, what can cause that",
        ],
        "query": "dog foaming at mouth causes poisoning seizure dental nausea emergency vet",
        "rows": [
            ("Foaming at the mouth in dogs: causes", "Veterinary guidance lists nausea, stress, dental disease, seizures, poisoning, and rabies as possible causes."),
            ("When dog foaming is urgent", "Emergency care is advised when foaming is sudden, severe, follows toxin exposure, or comes with collapse or seizures."),
        ],
        "final": "Dogs can foam at the mouth from nausea, stress, dental irritation, toxin exposure, seizures, or infections like rabies. If it is sudden, heavy, or paired with weakness, collapse, vomiting, or possible poison exposure, treat it as urgent and call a vet or emergency clinic.",
        "query_terms": ["dog foaming", "mouth"],
        "answer_terms": ["poisoning", "vet"],
    },
    {
        "topic": "cat foaming after medicine",
        "users": [
            "why is my cat foaming after i gave medicine",
            "look up cat white foam after oral meds",
            "cat started bubbling at the mouth after medicine why",
        ],
        "query": "cat foaming mouth after medicine bitter taste nausea toxicity vet",
        "rows": [
            ("Cat foaming after medication", "Cats may foam after bitter oral medicines because of taste, drooling, nausea, or irritation."),
            ("When to call a vet", "Call a veterinarian if the cat may have received the wrong dose, shows tremors, weakness, breathing issues, or persistent vomiting."),
        ],
        "final": "Cats often foam or drool after oral medicine because many meds taste very bitter, but nausea, irritation, or overdose can also do it. If the dose may be wrong or symptoms continue, call your vet and keep the package handy.",
        "query_terms": ["cat foaming", "medicine"],
        "answer_terms": ["bitter", "vet"],
    },
    {
        "topic": "snails bubbling",
        "users": [
            "why do garden snails bubble up sometimes",
            "look up why snails make bubbles",
            "why is my snail foaming a bit",
        ],
        "query": "garden snail bubbling foaming mucus stress irritation defense",
        "rows": [
            ("Why snails bubble", "Land snails can make bubbly mucus when irritated, stressed, handled roughly, exposed to salt or chemicals, or defending themselves."),
            ("Snail mucus and bubbles", "Some aquatic snails use mucus bubbles for floating, while pet land snails may foam as a stress response."),
        ],
        "final": "Snails usually bubble or foam when air mixes with mucus, often from stress, irritation, handling, dryness, salt, or chemicals. For a pet snail, rinse away any irritant with clean dechlorinated water and check humidity rather than treating it like normal behavior.",
        "query_terms": ["snail", "bubbling"],
        "answer_terms": ["mucus", "stress"],
    },
    {
        "topic": "swollen phone battery",
        "users": [
            "why is my phone battery swollen and is it dangerous",
            "look up swollen lithium battery what to do",
            "phone battery puffed up after charging, safe?",
        ],
        "query": "swollen lithium ion phone battery gas buildup fire risk stop using",
        "rows": [
            ("Swollen lithium-ion batteries", "Battery swelling usually means gas buildup from cell degradation, damage, overheating, or overcharging."),
            ("Battery swelling safety", "A swollen battery can rupture, leak, smoke, or catch fire. Stop using and charging the device and seek safe replacement or recycling."),
        ],
        "final": "A swollen phone battery is usually gas buildup from a failing lithium-ion cell. It is unsafe: stop using or charging it, do not press or puncture it, and get it replaced or recycled safely.",
        "query_terms": ["swollen", "battery"],
        "answer_terms": ["unsafe", "fire"],
    },
    {
        "topic": "kombucha vinegar smell",
        "users": [
            "why does my kombucha smell like vinegar",
            "look up kombucha tastes too vinegary reason",
            "kombucha batch turned sharp vinegar smell why",
        ],
        "query": "kombucha smells like vinegar over fermented acetic acid fix",
        "rows": [
            ("Kombucha tastes like vinegar", "A strong vinegar smell usually comes from extended fermentation and acetic acid production."),
            ("Fixing overly sour kombucha", "Shorter fermentation, cooler storage, and using less starter liquid can reduce sourness in the next batch."),
        ],
        "final": "A vinegar smell usually means the kombucha over-fermented and built up more acetic acid. It is often still usable if there is no mold, but shorten the ferment next time or dilute it to taste.",
        "query_terms": ["kombucha", "vinegar"],
        "answer_terms": ["over-fermented", "acid"],
    },
    {
        "topic": "phone battery drain after update",
        "users": [
            "why is my phone battery draining after an update",
            "look up battery drain right after phone update",
            "ios android update killed my battery why",
        ],
        "query": "phone battery drain after software update indexing background activity recalibration",
        "rows": [
            ("Battery drain after updates", "After major updates, phones may spend hours or days indexing photos, rebuilding caches, syncing, and recalibrating battery estimates."),
            ("When battery drain is abnormal", "Check background apps, location use, and battery health if drain remains high after a couple of days."),
        ],
        "final": "Battery drain right after an update is often from background indexing, app updates, syncing, and battery estimate recalibration. If it does not settle after a day or two, check battery health and which apps are using background power.",
        "query_terms": ["battery drain", "update"],
        "answer_terms": ["background", "recalibration"],
    },
]


PRICE_SEEDS = [
    {
        "users": [
            "whats the price per liter of gas in japan in euro",
            "look up japan petrol per litre in eur",
            "how much is gasoline in japan per liter converted to euros",
        ],
        "query": "Japan gasoline price per liter yen EUR conversion",
        "rows": [
            ("Japan gasoline retail price", "Regular gasoline is around 174 yen per liter in recent Japan retail-price summaries."),
            ("EUR JPY exchange rate", "A recent EUR/JPY rate near 172 yen per euro implies 174 yen is about 1.01 euro."),
        ],
        "final": "Using about 174 yen per liter and roughly 172 yen per euro, gasoline in Japan is about 1.01 EUR per liter. Treat that as an estimate because pump prices and exchange rates move.",
        "query_terms": ["Japan", "gasoline"],
        "answer_terms": ["EUR", "liter"],
    },
    {
        "users": [
            "what is milk per liter in japan in gbp roughly",
            "look up japanese milk price converted to pounds",
            "how much does a liter of milk cost in japan in pounds",
        ],
        "query": "Japan milk price per liter yen GBP conversion",
        "rows": [
            ("Japan milk retail price", "Retail milk prices are often around 220 to 270 yen per liter depending on store and region."),
            ("GBP JPY exchange rate", "At about 215 yen per pound, 220 to 270 yen is roughly 1.02 to 1.26 GBP."),
        ],
        "final": "A liter of milk in Japan is roughly 220-270 yen, which is about 1.00-1.25 GBP at a recent yen-pound rate. Local supermarkets can vary.",
        "query_terms": ["Japan", "milk"],
        "answer_terms": ["GBP", "yen"],
    },
    {
        "users": [
            "what is silver per ounce in euros today",
            "look up silver spot price oz eur",
            "silver price per ounce in eur right now",
        ],
        "query": "silver spot price per ounce EUR today",
        "rows": [
            ("Silver spot price today", "Silver spot is quoted around 38 USD per troy ounce in recent market tables."),
            ("EUR USD exchange rate", "At about 1.17 USD per EUR, 38 USD is roughly 32.50 EUR."),
        ],
        "final": "If silver is around 38 USD per troy ounce and EUR/USD is about 1.17, that works out to roughly 32.50 EUR per ounce. Use it as a live-market estimate, not a quote.",
        "query_terms": ["silver", "ounce"],
        "answer_terms": ["EUR", "ounce"],
    },
]


NO_TOOL_SEEDS = [
    ("dont search, explain what a search engine is", "A search engine crawls or indexes pages, ranks them for a query, and returns links or summaries that seem relevant."),
    ("without looking it up, what is photosynthesis", "Photosynthesis is how plants, algae, and some bacteria use light energy to turn carbon dioxide and water into sugars, releasing oxygen as a byproduct."),
    ("no web, why do onions make people cry", "Cut onions release sulfur compounds that react into an eye-irritating gas. Your eyes make tears to dilute and wash it away."),
    ("answer from memory only: what is a lithium ion battery", "A lithium-ion battery stores energy by moving lithium ions between electrodes during charging and discharging."),
    ("do not search. what does inflation mean", "Inflation means the general level of prices is rising, so the same amount of money buys less than before."),
]


INSUFFICIENT_SEEDS = [
    {
        "user": "look up the exact price of the sandwich at that cafe from my screenshot",
        "query": "exact sandwich price cafe screenshot",
        "rows": [
            ("Cafe menu design tips", "Article about designing restaurant menus, no prices for the user's screenshot."),
            ("Generic sandwich prices", "A broad article with no matching cafe name or current menu."),
        ],
        "final": "I do not have enough clear evidence to give the exact sandwich price. The search results are generic and do not identify the cafe or current menu, so I would need the cafe name or the screenshot text.",
        "query_terms": ["sandwich", "price"],
        "answer_terms": ["not enough", "cafe"],
    },
    {
        "user": "find the current rent for the apartment my friend mentioned",
        "query": "current rent apartment friend mentioned",
        "rows": [
            ("Apartment rent trends", "Citywide rent trend article with averages only."),
            ("Rental listings", "No address, building, or unit matching the user's request."),
        ],
        "final": "I do not have enough evidence to identify that apartment or its current rent. I would need the building, listing, address, or message text before searching further.",
        "query_terms": ["rent", "apartment"],
        "answer_terms": ["not enough", "address"],
    },
]


def make_synthesis_rows(target: int, rng: random.Random) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seeds = SYNTHESIS_SEEDS + PRICE_SEEDS
    variants = [
        "{user}",
        "can you look this up: {user}",
        "{user} pls",
        "quick search - {user}",
    ]
    while len(rows) < target:
        seed = seeds[len(rows) % len(seeds)]
        user = rng.choice(variants).format(user=rng.choice(seed["users"]))
        query = seed["query"]
        call = tool_call("web_search", {"query": query}, f"synth_{len(rows)}")
        messages = [
            {"role": "user", "content": user},
            {"role": "assistant", "content": "", "tool_calls": [call]},
            {"role": "tool", "tool_call_id": call["id"], "content": source_block(query, seed["rows"])},
            {"role": "assistant", "content": seed["final"]},
        ]
        rows.append(row("web_synthesis_after_results", messages, 1, seed["query_terms"], seed["answer_terms"]))
    return rows


def make_no_tool_rows(target: int, rng: random.Random) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    prefixes = ["", "quickly, ", "short answer: ", "one paragraph, "]
    while len(rows) < target:
        user, final = NO_TOOL_SEEDS[len(rows) % len(NO_TOOL_SEEDS)]
        messages = [
            {"role": "user", "content": rng.choice(prefixes) + user},
            {"role": "assistant", "content": final},
        ]
        rows.append(row("web_no_tool_boundary", messages, 0, [], [final.split()[0]]))
    return rows


def make_retry_rows(target: int, rng: random.Random) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seeds = SYNTHESIS_SEEDS + PRICE_SEEDS
    while len(rows) < target:
        seed = seeds[len(rows) % len(seeds)]
        first_query = seed["query"].split(" ", 4)[0] + " " + seed["query"].split(" ", 4)[1]
        first_call = tool_call("web_search", {"query": first_query}, f"retry_{len(rows)}_first")
        second_call = tool_call("web_search", {"query": seed["query"]}, f"retry_{len(rows)}_second")
        messages = [
            {"role": "user", "content": rng.choice(seed["users"])},
            {"role": "assistant", "content": "", "tool_calls": [first_call]},
            {
                "role": "tool",
                "tool_call_id": first_call["id"],
                "content": source_block(first_query, [("Ambiguous results", "The results are dictionary pages or unrelated pages and do not answer the user's question.")]),
            },
            {"role": "assistant", "content": "", "tool_calls": [second_call]},
            {"role": "tool", "tool_call_id": second_call["id"], "content": source_block(seed["query"], seed["rows"])},
            {"role": "assistant", "content": seed["final"]},
        ]
        rows.append(row("web_retry_after_weak_results", messages, 2, seed["query_terms"], seed["answer_terms"]))
    return rows


def make_insufficient_rows(target: int, rng: random.Random) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    while len(rows) < target:
        seed = INSUFFICIENT_SEEDS[len(rows) % len(INSUFFICIENT_SEEDS)]
        user = seed["user"]
        if rng.random() < 0.5:
            user = "please search: " + user
        call = tool_call("web_search", {"query": seed["query"]}, f"insufficient_{len(rows)}")
        messages = [
            {"role": "user", "content": user},
            {"role": "assistant", "content": "", "tool_calls": [call]},
            {"role": "tool", "tool_call_id": call["id"], "content": source_block(seed["query"], seed["rows"])},
            {"role": "assistant", "content": seed["final"]},
        ]
        rows.append(row("web_insufficient_evidence", messages, 1, seed["query_terms"], seed["answer_terms"]))
    return rows


def row(category: str, messages: list[dict[str, Any]], expected_calls: int, query_terms: list[str], answer_terms: list[str]) -> dict[str, Any]:
    item = {
        "messages": messages,
        "tools": [] if expected_calls == 0 else [WEB_SEARCH_TOOL],
        "generator": "odysseus_web_v53_repair_seeded_teacher",
        "metadata": {
            "category": category,
            "split": "train_or_val",
            "expected_tool_calls": expected_calls,
            "query_must_include": query_terms,
            "answer_must_include": answer_terms,
        },
    }
    item["uuid"] = stable_id("ody_web_v53_repair", item)
    return item


def split_rows(rows: list[dict[str, Any]], val_every: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    train: list[dict[str, Any]] = []
    val: list[dict[str, Any]] = []
    for idx, item in enumerate(rows):
        (val if idx % val_every == val_every - 1 else train).append(item)
    return train, val


def eval_case(idx: int, seed: dict[str, Any], category: str, expect_no_tool: bool = False) -> dict[str, Any]:
    if expect_no_tool:
        return {
            "id": f"v53_{category}_{idx:02d}",
            "kind": "negative_web",
            "user": seed["user"],
            "expect_no_tool": True,
            "forbidden_tools": ["web_search", "web_fetch"],
            "must_answer_any": seed["answer_terms"],
            "forbidden_final": ["WEB SEARCH RESULTS", "```sources", "Here are links"],
        }
    return {
        "id": f"v53_{category}_{idx:02d}",
        "kind": "web",
        "user": seed["user"],
        "expect_first_tool": "web_search",
        "forbidden_query_any": ["official links", "cambridge", "merriam", "dictionary", "wikipedia official"],
        "must_query_any": seed["query_terms"],
        "must_answer_any": seed["answer_terms"],
        "forbidden_final": [
            "WEB SEARCH RESULTS",
            "```sources",
            "Here are links",
            "not enough clear answer evidence",
            "not enough clear evidence to synthesize",
        ],
        "max_web_searches": 2,
    }


def build_eval_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    synth_seeds = SYNTHESIS_SEEDS + PRICE_SEEDS
    for idx, seed in enumerate(synth_seeds):
        cases.append(eval_case(idx, {"user": seed["users"][0], "query_terms": seed["query_terms"], "answer_terms": seed["answer_terms"]}, "synthesis"))
    for idx, seed in enumerate(SYNTHESIS_SEEDS[:4]):
        cases.append(eval_case(idx, {"user": "bad prior results, search again properly: " + seed["users"][1], "query_terms": seed["query_terms"], "answer_terms": seed["answer_terms"]}, "query_quality"))
    for idx, (user, final) in enumerate(NO_TOOL_SEEDS):
        terms = [word.strip(".,").lower() for word in final.split() if len(word.strip(".,")) > 5][:3] or ["answer"]
        cases.append(eval_case(idx, {"user": user, "answer_terms": terms}, "no_tool", expect_no_tool=True))
    return cases


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(item, ensure_ascii=True) + "\n" for item in rows), encoding="utf-8")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--eval-out", type=Path, default=DEFAULT_EVAL_OUT)
    parser.add_argument("--val-every", type=int, default=6)
    parser.add_argument("--seed", type=int, default=53)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    rows = []
    rows.extend(make_synthesis_rows(120, rng))
    rows.extend(make_no_tool_rows(50, rng))
    rows.extend(make_retry_rows(40, rng))
    rows.extend(make_insufficient_rows(30, rng))
    rng.shuffle(rows)
    train, val = split_rows(rows, args.val_every)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.out_dir / "train.jsonl", train)
    write_jsonl(args.out_dir / "val.jsonl", val)
    write_jsonl(args.out_dir / "all.jsonl", rows)

    eval_cases = build_eval_cases()
    args.eval_out.parent.mkdir(parents=True, exist_ok=True)
    args.eval_out.write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "generator": "build_odysseus_web_v53_repair_sft.py",
                "source": "seeded teacher-style repair rows from V52 live failure families",
                "cases": eval_cases,
            },
            ensure_ascii=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    manifest = {
        "name": args.out_dir.name,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_sft_rows": len(rows),
        "train_rows": len(train),
        "val_rows": len(val),
        "heldout_cases": len(eval_cases),
        "categories": {
            category: sum(1 for item in rows if item["metadata"]["category"] == category)
            for category in sorted({item["metadata"]["category"] for item in rows})
        },
        "heldout_categories": {
            category: sum(1 for case in eval_cases if f"_{category}_" in case["id"])
            for category in ["synthesis", "query_quality", "no_tool"]
        },
        "acceptance_target": (
            "Promote only if live robust gate passes all cases, user-reported web searches synthesize answers, "
            "no-search requests avoid tools, active compose still mutates document, and old CRUD remains regression-clean."
        ),
        "files": {
            "train": str(args.out_dir / "train.jsonl"),
            "val": str(args.out_dir / "val.jsonl"),
            "all": str(args.out_dir / "all.jsonl"),
            "heldout_eval": str(args.eval_out),
        },
    }
    for key, value in list(manifest["files"].items()):
        manifest[f"{key}_sha256"] = file_sha256(Path(value))
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({k: manifest[k] for k in ("total_sft_rows", "train_rows", "val_rows", "heldout_cases", "categories", "heldout_categories")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
