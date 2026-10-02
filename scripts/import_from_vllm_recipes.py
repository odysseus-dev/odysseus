#!/usr/bin/env python3
"""Import models from the upstream vllm-project/recipes catalog into our
local hf_models.json. Two modes:

  --update-existing  Stamp min_vllm_version + vllm_recipe=True on rows we
                     already carry. Cheap, no HF API calls.
  --add-missing      Create new catalog rows for every recipe model we
                     don't carry. Hits the HF API for created_at + downloads
                     (~1 req per missing model, paced).

Both modes write atomically (tmp + rename) so a crashed run leaves the
catalog intact. Default with no mode flags runs both, prefer to pass them
explicitly.

Usage:
    python scripts/import_from_vllm_recipes.py --update-existing
    python scripts/import_from_vllm_recipes.py --add-missing
    python scripts/import_from_vllm_recipes.py --dry-run
    python scripts/import_from_vllm_recipes.py --limit 10

Auth: set HF_TOKEN to access gated repos when --add-missing.
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import httpx
    import yaml
except ImportError:
    print("pip install httpx PyYAML", file=sys.stderr)
    sys.exit(1)

try:
    from huggingface_hub import HfApi
    from huggingface_hub.utils import HfHubHTTPError
except ImportError:
    HfApi = None
    HfHubHTTPError = Exception


CATALOG_PATH = Path(__file__).resolve().parent.parent / "services" / "hwfit" / "data" / "hf_models.json"
RECIPES_TREE_URL = (
    "https://api.github.com/repos/vllm-project/recipes/git/trees/main?recursive=1"
)
RECIPE_RAW_URL = (
    "https://raw.githubusercontent.com/vllm-project/recipes/main/models/{repo}.yaml"
)


# Map recipe `precision` to the closest catalog `quantization` label that
# fit.py / models.py already understand.
_PRECISION_TO_QUANT = {
    "fp8": "FP8",
    "nvfp4": "NVFP4",
    "mxfp4": "MXFP4",
    "bf16": "BF16",
    "fp16": "F16",
    "f16": "F16",
    "fp4": "FP4",
    "int8": "INT8",
    "int4": "INT4",
    "awq-4bit": "AWQ-4bit",
    "awq-8bit": "AWQ-8bit",
}

# Architecture name → use_case fallback. fit.py weights use_case for filtering;
# missing field defaults to a generic bucket.
_ARCH_USE_CASE = {
    "moe": "General-purpose reasoning, long-context",
    "llama": "General-purpose chat",
    "qwen2": "General-purpose chat",
    "qwen3": "General-purpose reasoning",
    "deepseek_v3_moe": "General-purpose reasoning, long-context",
    "deepseek_v4_moe": "General-purpose reasoning, long-context",
}


def _parse_param_count(s) -> int:
    """'230B' / '8.6B' / '4.2T' → integer parameter count."""
    if s is None:
        return 0
    s = str(s).strip().replace(",", "")
    m = re.match(r"^([\d.]+)\s*([KMBT]?)$", s, re.I)
    if not m:
        return 0
    num = float(m.group(1))
    unit = (m.group(2) or "").upper()
    mult = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12, "": 1.0}[unit]
    return int(num * mult)


def _capabilities_for(arch: str, hardware: dict, ctx_len: int, has_reasoning: bool) -> list[str]:
    caps = []
    if "moe" in (arch or "").lower():
        caps.append("moe")
    if has_reasoning:
        caps.append("reasoning")
    if ctx_len and ctx_len >= 100_000:
        caps.append("long_context")
    if any(hw in (hardware or {}) for hw in ("mi300x", "mi325x", "mi350x", "mi355x")):
        caps.append("amd_supported")
    return caps


def _fetch_manifest(client: httpx.Client) -> set[str]:
    r = client.get(RECIPES_TREE_URL, headers={"Accept": "application/vnd.github+json"}, timeout=15)
    r.raise_for_status()
    tree = (r.json() or {}).get("tree") or []
    out: set[str] = set()
    for e in tree:
        path = (e or {}).get("path") or ""
        if path.startswith("models/") and path.endswith(".yaml"):
            body = path[len("models/"):-len(".yaml")]
            if "/" in body:
                out.add(body)
    return out


def _fetch_recipe(client: httpx.Client, repo: str) -> dict | None:
    url = RECIPE_RAW_URL.format(repo=repo)
    try:
        r = client.get(url, timeout=10)
        if r.status_code != 200:
            return None
        return yaml.safe_load(r.text) or {}
    except Exception:
        return None


def _stamp_from_recipe(entry: dict, recipe: dict) -> bool:
    """Mutate entry with recipe-derived fields. Returns True if anything changed."""
    model = recipe.get("model") or {}
    meta = recipe.get("meta") or {}
    features = recipe.get("features") or {}

    changed = False
    new_min = (model.get("min_vllm_version") or "").strip()
    if new_min and entry.get("min_vllm_version") != new_min:
        entry["min_vllm_version"] = new_min
        changed = True
    if not entry.get("vllm_recipe"):
        entry["vllm_recipe"] = True
        changed = True
    # Hardware support map — useful for filtering "which models run on my AMD box".
    hw = meta.get("hardware") or {}
    if hw and entry.get("recipe_hardware") != hw:
        entry["recipe_hardware"] = {k: str(v) for k, v in hw.items()}
        changed = True
    # Tool/reasoning parser hints — purely informational at catalog level;
    # the live launch command builder still reads them from the recipe API.
    if features.get("reasoning") and not entry.get("has_reasoning_parser"):
        entry["has_reasoning_parser"] = True
        changed = True
    if features.get("tool_calling") and not entry.get("has_tool_call_parser"):
        entry["has_tool_call_parser"] = True
        changed = True
    return changed


def _build_new_entry(repo: str, recipe: dict, hf_info=None) -> dict | None:
    """Build a fresh catalog entry from a recipe + (optional) HF model info."""
    model = recipe.get("model") or {}
    meta = recipe.get("meta") or {}
    features = recipe.get("features") or {}
    variants = recipe.get("variants") or {}

    org, name = repo.split("/", 1)
    raw_params = _parse_param_count(model.get("parameter_count"))
    active_raw = _parse_param_count(model.get("active_parameters"))
    ctx = model.get("context_length") or 0

    # Pick the smallest-VRAM variant as the catalog quant — that's what most
    # users land on first. NVFP4/MXFP4 typically win this on Blackwell;
    # FP8 elsewhere; BF16 baseline only.
    pick_quant = None
    pick_vram = None
    for vk, vv in variants.items():
        if not isinstance(vv, dict):
            continue
        prec = (vv.get("precision") or "").lower()
        vram = vv.get("vram_minimum_gb") or 0
        quant = _PRECISION_TO_QUANT.get(prec)
        if quant and (pick_vram is None or (vram and vram < pick_vram)):
            pick_quant = quant
            pick_vram = vram or pick_vram
    if not pick_quant:
        pick_quant = "BF16"

    arch = (model.get("architecture") or "").lower()
    use_case = _ARCH_USE_CASE.get(arch, "General-purpose chat")
    caps = _capabilities_for(arch, meta.get("hardware") or {}, ctx, bool(features.get("reasoning")))

    rel_date = ""
    downloads = 0
    likes = 0
    if hf_info is not None:
        created = getattr(hf_info, "created_at", None)
        if created:
            rel_date = created.strftime("%Y-%m-%d")
        downloads = int(getattr(hf_info, "downloads", 0) or 0)
        likes = int(getattr(hf_info, "likes", 0) or 0)
    if not rel_date:
        rel_date = str(meta.get("date_updated") or datetime.utcnow().strftime("%Y-%m-%d"))

    entry: dict = {
        "name": repo,
        "provider": org,
        "parameter_count": str(model.get("parameter_count") or "?"),
        "parameters_raw": raw_params,
        "is_moe": "moe" in arch,
        "quantization": pick_quant,
        "context_length": int(ctx or 0),
        "use_case": use_case,
        "capabilities": caps,
        "pipeline_tag": "text-generation",
        "architecture": arch or "unknown",
        "hf_downloads": downloads,
        "hf_likes": likes,
        "release_date": rel_date,
        # Recipe-derived bits.
        "vllm_recipe": True,
        "min_vllm_version": (model.get("min_vllm_version") or "").strip() or None,
        "recipe_hardware": {k: str(v) for k, v in (meta.get("hardware") or {}).items()},
        "has_reasoning_parser": bool(features.get("reasoning")),
        "has_tool_call_parser": bool(features.get("tool_calling")),
    }
    if active_raw:
        entry["active_parameters"] = active_raw
    if pick_vram:
        # min_vram_gb is what hwfit uses for "does this fit". Recipe states a
        # minimum for the chosen variant; round up slightly for KV-cache room.
        entry["min_vram_gb"] = float(pick_vram)
        entry["min_ram_gb"] = float(round(pick_vram * 0.6, 1))
        entry["recommended_ram_gb"] = float(round(pick_vram * 1.2, 1))
    # Drop empty / None fields to keep the JSON tidy.
    return {k: v for k, v in entry.items() if v not in (None, "", [], {})}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--update-existing", action="store_true", help="Stamp min_vllm_version + vllm_recipe on existing rows.")
    p.add_argument("--add-missing", action="store_true", help="Add new rows for recipe models not in the catalog.")
    p.add_argument("--limit", type=int, default=0, help="Stop after N recipe fetches.")
    p.add_argument("--dry-run", action="store_true", help="Don't write back; just report.")
    p.add_argument("--sleep", type=float, default=0.05, help="Seconds between HTTP requests.")
    args = p.parse_args()
    if not args.update_existing and not args.add_missing:
        args.update_existing = args.add_missing = True

    with CATALOG_PATH.open(encoding="utf-8") as f:
        catalog = json.load(f)
    by_name = {m.get("name"): m for m in catalog if m.get("name")}

    client = httpx.Client(follow_redirects=True)
    print(f"Catalog: {CATALOG_PATH} ({len(catalog)} entries)")
    print("Fetching upstream manifest…")
    try:
        manifest = _fetch_manifest(client)
    except Exception as e:
        print(f"FATAL: manifest fetch failed: {e}", file=sys.stderr)
        sys.exit(2)
    print(f"Manifest: {len(manifest)} recipes")

    existing = sorted(by_name.keys() & manifest)
    missing = sorted(manifest - by_name.keys())
    print(f"Match catalog ↔ manifest: existing={len(existing)} missing={len(missing)}")

    targets: list[tuple[str, str]] = []  # (repo, action)
    if args.update_existing:
        targets.extend((r, "update") for r in existing)
    if args.add_missing:
        targets.extend((r, "add") for r in missing)
    if args.limit:
        targets = targets[: args.limit]
    print(f"Targets: {len(targets)}")

    hf_api = HfApi(token=os.environ.get("HF_TOKEN") or None) if HfApi else None
    updated = added = skipped = 0
    started = time.time()

    for n, (repo, action) in enumerate(targets, 1):
        recipe = _fetch_recipe(client, repo)
        if not recipe:
            print(f"[{n}/{len(targets)}] {repo:55} skip (no recipe fetched)")
            skipped += 1
            time.sleep(args.sleep)
            continue
        if action == "update":
            entry = by_name[repo]
            if _stamp_from_recipe(entry, recipe):
                updated += 1
                print(f"[{n}/{len(targets)}] {repo:55} updated")
            else:
                print(f"[{n}/{len(targets)}] {repo:55} unchanged")
        else:  # add
            hf_info = None
            if hf_api:
                try:
                    hf_info = hf_api.model_info(repo, files_metadata=False)
                except HfHubHTTPError as e:
                    code = getattr(getattr(e, "response", None), "status_code", "?")
                    print(f"  HF {code} for {repo} — building from recipe only", file=sys.stderr)
                except Exception as e:
                    print(f"  HF error for {repo}: {e}", file=sys.stderr)
            new_entry = _build_new_entry(repo, recipe, hf_info)
            if new_entry:
                catalog.append(new_entry)
                by_name[repo] = new_entry
                added += 1
                print(f"[{n}/{len(targets)}] {repo:55} added ({new_entry.get('parameter_count','?')}, {new_entry.get('quantization','?')})")
            else:
                skipped += 1
                print(f"[{n}/{len(targets)}] {repo:55} skip (couldn't build entry)")
        time.sleep(args.sleep)

    elapsed = time.time() - started
    print()
    print(f"Done in {elapsed:.1f}s — added={added}, updated={updated}, skipped={skipped}")

    if args.dry_run:
        print("Dry run — no write.")
        return
    if added or updated:
        tmp = CATALOG_PATH.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(catalog, f, indent=1, ensure_ascii=False)
            f.write("\n")
        tmp.replace(CATALOG_PATH)
        print(f"Wrote {CATALOG_PATH} ({len(catalog)} entries)")
    else:
        print("No changes — catalog untouched.")


if __name__ == "__main__":
    main()
