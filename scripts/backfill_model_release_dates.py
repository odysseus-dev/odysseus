#!/usr/bin/env python3
"""Backfill release_date on entries in services/hwfit/data/hf_models.json.

Why: the `newest` sort in the cookbook ranks rows by release_date. Anything
missing a date sorts to the bottom. This script pulls `created_at` from the
HuggingFace API for each catalog entry without one (or all entries when
--refresh is passed) and writes the catalog back.

Usage:
    python scripts/backfill_model_release_dates.py            # missing only
    python scripts/backfill_model_release_dates.py --refresh  # all entries
    python scripts/backfill_model_release_dates.py --limit 50 # cap requests
    python scripts/backfill_model_release_dates.py --dry-run  # show, don't write

Auth: set HF_TOKEN env var (or huggingface-cli login) to access gated repos.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from huggingface_hub import HfApi
    from huggingface_hub.utils import HfHubHTTPError
except ImportError:
    print("Install huggingface_hub: pip install huggingface_hub", file=sys.stderr)
    sys.exit(1)


CATALOG_PATH = Path(__file__).resolve().parent.parent / "services" / "hwfit" / "data" / "hf_models.json"


def fetch_release_date(api: HfApi, repo_id: str) -> str | None:
    """Return YYYY-MM-DD release date, or None on miss / error."""
    try:
        info = api.model_info(repo_id, files_metadata=False)
    except HfHubHTTPError as e:
        # 401 = gated/private, 404 = renamed/deleted. Either way, no date.
        status = getattr(getattr(e, "response", None), "status_code", None)
        print(f"  {repo_id}: HTTP {status or '?'}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  {repo_id}: {type(e).__name__}: {e}", file=sys.stderr)
        return None
    created = getattr(info, "created_at", None)
    if not created:
        return None
    return created.strftime("%Y-%m-%d")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--refresh", action="store_true", help="Overwrite existing release_date too (default: only fill missing).")
    p.add_argument("--limit", type=int, default=0, help="Stop after N API calls (0 = no limit).")
    p.add_argument("--dry-run", action="store_true", help="Don't write back; just report.")
    p.add_argument("--sleep", type=float, default=0.05, help="Seconds to sleep between requests (default 0.05).")
    args = p.parse_args()

    if not CATALOG_PATH.exists():
        print(f"Catalog not found: {CATALOG_PATH}", file=sys.stderr)
        sys.exit(2)

    with CATALOG_PATH.open(encoding="utf-8") as f:
        catalog = json.load(f)

    candidates = []
    for i, m in enumerate(catalog):
        name = m.get("name")
        if not name:
            continue
        existing = (m.get("release_date") or "").strip()
        if existing and not args.refresh:
            continue
        candidates.append(i)

    if args.limit:
        candidates = candidates[: args.limit]

    print(f"Catalog: {CATALOG_PATH}")
    print(f"Total entries: {len(catalog)}")
    print(f"Targets ({'refresh all' if args.refresh else 'missing only'}{'' if not args.limit else f', capped at {args.limit}'}): {len(candidates)}")
    if not candidates:
        print("Nothing to do.")
        return

    api = HfApi(token=os.environ.get("HF_TOKEN") or None)
    updated = 0
    skipped = 0
    started = time.time()
    for n, idx in enumerate(candidates, start=1):
        entry = catalog[idx]
        name = entry["name"]
        old = (entry.get("release_date") or "").strip()
        new = fetch_release_date(api, name)
        if new is None:
            skipped += 1
            tag = "skip"
        elif new == old:
            tag = "unchanged"
        else:
            entry["release_date"] = new
            updated += 1
            tag = f"set {new}" + (f" (was {old})" if old else "")
        print(f"[{n}/{len(candidates)}] {name} — {tag}")
        if args.sleep:
            time.sleep(args.sleep)

    elapsed = time.time() - started
    print()
    print(f"Done in {elapsed:.1f}s — {updated} updated, {skipped} skipped (HF unavailable / gated / missing date).")

    if args.dry_run:
        print("Dry run — no write.")
        return

    if updated:
        # Atomic write: tmp file in the same dir, then rename. Keeps the
        # catalog usable even if the process dies mid-write.
        tmp = CATALOG_PATH.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(catalog, f, indent=1, ensure_ascii=False)
            f.write("\n")
        tmp.replace(CATALOG_PATH)
        print(f"Wrote {CATALOG_PATH}")
    else:
        print("No changes to write.")


if __name__ == "__main__":
    main()
