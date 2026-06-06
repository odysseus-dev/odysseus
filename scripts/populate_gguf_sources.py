#!/usr/bin/env python3
"""Fast batch population of missing gguf_sources using HF list_models.

Searches each popular quantizer for models matching *-GGUF, then maps them
back to base-model entries in the catalog. One API call per quantizer
instead of one per candidate combination.

Usage:
    python3 scripts/populate_gguf_sources.py
"""
import json
import os
from collections import defaultdict

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "services", "hwfit", "data", "hf_models.json")
DATA_PATH = os.path.abspath(DATA_PATH)

QUANTIZERS = [
    ("unsloth", 0),
    ("bartowski", 1),
    ("TheBloke", 2),
    ("mradermacher", 3),
]


def _normalize_for_match(name):
    """Drop org prefix so 'meta-llama/Llama-3-8B' matches 'unsloth/Llama-3-8B-GGUF'."""
    return name.split("/")[-1].lower().replace("-instruct", "").replace("-chat", "").strip()


def main():
    try:
        from huggingface_hub import HfApi
    except ImportError:
        print("huggingface_hub not installed. Run: pip install huggingface_hub")
        return

    api = HfApi()

    with open(DATA_PATH, encoding="utf-8") as f:
        catalog = json.load(f)

    by_name = {m["name"]: m for m in catalog}
    # Also index by normalized base name for fuzzy matching
    by_base = defaultdict(list)
    for m in catalog:
        base = _normalize_for_match(m["name"])
        by_base[base].append(m)

    # Find models with GGUF quant but no sources
    to_fix = [m for m in catalog if (m.get("quantization", "").startswith(("Q", "IQ"))) and not m.get("gguf_sources")]
    to_fix_set = {m["name"] for m in to_fix}
    print(f"Found {len(to_fix)} models missing gguf_sources")

    added_total = 0

    for author, priority in QUANTIZERS:
        print(f"\nSearching {author} for -GGUF models...")
        try:
            # list_models with search is case-insensitive substring match on the repo id
            gguf_repos = list(api.list_models(author=author, search="-GGUF", full=False))
        except Exception as e:
            print(f"  Failed to list {author}: {e}")
            continue

        matched = 0
        for repo in gguf_repos:
            repo_id = repo.id
            # Strip author and -GGUF suffix to get base model name
            base = repo_id.split("/")[-1]
            base_clean = base.lower()
            for suffix in ("-gguf", "_gguf", "-GGUF", "_GGUF"):
                if base_clean.endswith(suffix):
                    base_clean = base_clean[: -len(suffix)]
                    break

            # Try exact match first
            targets = []
            for m in catalog:
                if m["name"] not in to_fix_set:
                    continue
                m_base = m["name"].split("/")[-1].lower()
                if base_clean == m_base or base_clean == m_base.replace("-instruct", "").replace("-chat", "").strip():
                    targets.append(m)
                # Also try: repo base exactly matches catalog base name
                elif m_base.endswith(base_clean) or base_clean.endswith(m_base):
                    targets.append(m)

            # Deduplicate and pick exact match if available
            seen = set()
            unique_targets = []
            for m in targets:
                if m["name"] in seen:
                    continue
                seen.add(m["name"])
                unique_targets.append(m)

            for m in unique_targets:
                if m.get("gguf_sources"):
                    continue
                m.setdefault("gguf_sources", []).append({"repo": repo_id, "provider": author})
                to_fix_set.discard(m["name"])
                matched += 1

        print(f"  Matched {matched} new sources from {author}")
        added_total += matched

    print(f"\nDone: added {added_total} gguf_sources. {len(to_fix_set)} models still missing.")

    if added_total > 0:
        with open(DATA_PATH + ".bak", "w", encoding="utf-8") as f:
            json.dump(catalog, f, indent=1)
        with open(DATA_PATH, "w", encoding="utf-8") as f:
            json.dump(catalog, f, indent=1)
        print(f"Wrote updated catalog to {DATA_PATH}")

        # Show a sample of what was added
        print("\nSample additions:")
        for m in catalog:
            if m.get("gguf_sources") and len(m["gguf_sources"]) == 1 and m.get("hf_downloads", 0) > 500000:
                src = m["gguf_sources"][0]
                print(f"  {m['name']} -> {src['repo']}")
                if added_total > 0:
                    added_total -= 1
                if added_total < 10:
                    break


if __name__ == "__main__":
    main()
