"""Live check of Anthropic prompt caching and context windows in Odysseus.

Reads the newest Claude chat from Odysseus's database (read-only) and reports,
per assistant turn: input tokens, cache reads, cache writes, cost and the
context window the ring measured against. Prints no secrets.

How to run the live check:
  1. Add an Anthropic endpoint in Settings > Add Models > Add API Models.
     Odysseus stores the key encrypted in its own database; it does not
     read ANTHROPIC_API_KEY from the environment.
  2. Start Odysseus and hold a chat of at least 3 turns in Agent mode with
     claude-sonnet-4-6 (or Opus 4.6+) through the real Anthropic API.
  3. From the repository root:  python scripts/check_claude_cache_metrics.py [path/to/app.db]

The three result lines:
  1. Cache reads on round 2+        -> later rounds reused cached input
  2. Cost below the uncached price  -> caching lowered the bill
  3. Context window about 1M        -> the ring uses the 1M window, not 200k
"""
import glob
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.getcwd())
try:
    from src.model_pricing import estimate_cost_usd
except Exception:  # pricing is optional for the report
    estimate_cost_usd = None

ANTHROPIC = "https://api.anthropic.com/v1"


def find_db():
    if len(sys.argv) > 1:
        return sys.argv[1]
    for pattern in ("data/app.db", "**/app.db"):
        hits = [p for p in glob.glob(pattern, recursive=True) if "node_modules" not in p]
        if hits:
            return max(hits, key=os.path.getmtime)
    sys.exit("app.db not found. Run this from the Odysseus folder, or pass the path to app.db.")


def metrics_of(raw):
    try:
        meta = json.loads(raw or "{}")
    except ValueError:
        return {}
    if isinstance(meta, dict) and isinstance(meta.get("metrics"), dict):
        meta = meta["metrics"]
    return meta if isinstance(meta, dict) else {}


def num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def main():
    db = find_db()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT session_id, timestamp, metadata FROM chat_messages "
        "WHERE role = 'assistant' ORDER BY timestamp DESC LIMIT 200"
    ).fetchall()
    claude = [(s, t, metrics_of(m)) for s, t, m in rows if "claude" in str(metrics_of(m).get("model", "")).lower()]
    if not claude:
        sys.exit(f"No Claude answers found in {db}. Run a 3+ turn Claude chat first.")

    session = claude[0][0]
    turns = sorted([(t, m) for s, t, m in claude if s == session])
    print(f"Database: {db}\nLatest Claude chat: {len(turns)} answer(s)\n")
    print(f"{'#':>2}  {'model':<22} {'input':>8} {'cache_read':>10} {'cache_write':>11} {'cost_usd':>9} {'ctx%':>6} {'window':>9}")
    saw_read = cheaper = window_1m = False
    for i, (_, m) in enumerate(turns, 1):
        model = str(m.get("model", "?"))[:22]
        inp, out = num(m.get("input_tokens")), num(m.get("output_tokens"))
        read, write = num(m.get("cache_read_input_tokens")), num(m.get("cache_creation_input_tokens"))
        # usage_buckets repeat the top-level totals; fall back to them only
        # when the turn saved no top-level cache fields at all.
        if m.get("cache_read_input_tokens") is None and m.get("cache_creation_input_tokens") is None:
            for bucket in m.get("usage_buckets") or []:
                if isinstance(bucket, dict):
                    read += num(bucket.get("cache_read_input_tokens"))
                    write += num(bucket.get("cache_creation_input_tokens"))
        cost = m.get("cost_usd")
        pct = num(m.get("context_percent"))
        used = num(m.get("request_context_tokens")) or inp
        # context_length is the exact window; context_percent is rounded to
        # 0.1, so estimating from it is only a fallback for older rows.
        window = num(m.get("context_length")) or (round(used / (pct / 100)) if pct else 0)
        print(f"{i:>2}  {model:<22} {inp:>8} {read:>10} {write:>11} {cost if cost is not None else '-':>9} {pct:>6} {window:>9}")
        if i >= 2 and read > 0:
            saw_read = True
        if window >= 900_000:
            window_1m = True
        if estimate_cost_usd and read and inp:
            full = estimate_cost_usd(m.get("model", ""), inp, out, ANTHROPIC)
            cached = estimate_cost_usd(m.get("model", ""), inp, out, ANTHROPIC,
                                       cache_read_tokens=read, cache_write_tokens=write)
            if full is not None and cached is not None and cached < full:
                cheaper = True

    print()
    print(f"1. Cache reads on round 2+:          {'PASS' if saw_read else 'FAIL'}")
    print(f"2. Cost below the uncached price:   {'PASS' if cheaper else ('FAIL' if estimate_cost_usd else 'n/a (pricing module not found)')}")
    print(f"3. Context window about 1M:         {'PASS' if window_1m else 'not shown (use Sonnet 4.6 / Opus 4.6+ for this check)'}")


if __name__ == "__main__":
    main()
