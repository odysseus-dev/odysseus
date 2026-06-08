#!/usr/bin/env python3
"""Build data/aa_model_index.json from Artificial Analysis Data API.

Usage:
    AA_API_KEY=... python scripts/sync_aa_model_index.py
        Fetch AA model list (required) + sitemap for valid_slugs.

    python scripts/sync_aa_model_index.py --sitemap-only
        Bundled fallback: sitemap slugs only (exact slug aliases, no HF guessing).
        Cookbook AA badges appear only when normalize(modelId) matches an AA slug.

Maps display names and optional huggingface_url (Pro tier) to verified AA slugs.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "data" / "aa_model_index.json"
AA_SITEMAP = "https://artificialanalysis.ai/sitemap.xml"
AA_API = "https://artificialanalysis.ai/api/v2/language/models/free"

_RESERVED_SLUGS = frozenset({
    "recommend", "capabilities", "comparisons", "multilingual",
    "multimodal", "caching", "providers", "leaderboards",
})


def normalize_key(raw: str) -> str:
    if not raw:
        return ""
    s = str(raw).strip()
    if "/" in s:
        s = s.rsplit("/", 1)[-1]
    s = re.sub(r"\.(gguf|bin|safetensors)$", "", s, flags=re.IGNORECASE)
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")


def fetch_sitemap_slugs() -> set[str]:
    req = Request(AA_SITEMAP, headers={"Accept": "application/xml"})
    with urlopen(req, timeout=120) as resp:
        xml = resp.read().decode("utf-8", errors="replace")
    slugs = set(re.findall(r"https://artificialanalysis\.ai/models/([a-z0-9-]+)", xml))
    return {s for s in slugs if s not in _RESERVED_SLUGS and "/" not in s}


def fetch_api_models(api_key: str) -> list[dict]:
    models: list[dict] = []
    page = 1
    while True:
        url = f"{AA_API}?page={page}&page_size=100"
        req = Request(url, headers={"x-api-key": api_key, "Accept": "application/json"})
        with urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        models.extend(payload.get("data") or [])
        if not (payload.get("pagination") or {}).get("has_more"):
            break
        page += 1
        if page > 200:
            break
    return models


def _add_alias(aliases: dict[str, str], key: str, slug: str, valid_slugs: set[str]) -> None:
    if slug not in valid_slugs:
        return
    norm = normalize_key(key)
    if not norm or norm in aliases:
        return
    aliases[norm] = slug


def build_aliases_from_api(models: list[dict], valid_slugs: set[str]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for m in models:
        slug = (m.get("slug") or "").strip()
        name = (m.get("name") or "").strip()
        if not slug or slug not in valid_slugs:
            continue
        _add_alias(aliases, slug, slug, valid_slugs)
        if name:
            _add_alias(aliases, name, slug, valid_slugs)
            base = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
            if base and base != name:
                _add_alias(aliases, base, slug, valid_slugs)
        hf_url = (m.get("huggingface_url") or "").strip()
        if hf_url:
            match = re.match(r"https?://huggingface\.co/([^/?#]+/[^/?#]+)", hf_url)
            if match:
                _add_alias(aliases, match.group(1), slug, valid_slugs)
    return aliases


def build_slug_only_aliases(valid_slugs: set[str]) -> dict[str, str]:
    """Exact AA slug aliases only — no HF catalog inference."""
    aliases: dict[str, str] = {}
    for slug in valid_slugs:
        _add_alias(aliases, slug, slug, valid_slugs)
    return aliases


def write_index(aliases: dict[str, str], valid_slugs: set[str], source: str) -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 4,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source,
        "valid_slug_count": len(valid_slugs),
        "alias_count": len(aliases),
        "valid_slugs": sorted(valid_slugs),
        "aliases": dict(sorted(aliases.items())),
    }
    OUT_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_PATH} ({len(aliases)} aliases, {len(valid_slugs)} AA slugs, source={source})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync Artificial Analysis model slug index")
    parser.add_argument(
        "--sitemap-only",
        action="store_true",
        help="Bundled fallback: sitemap slugs with exact slug aliases only (no API key)",
    )
    args = parser.parse_args()

    print("Fetching AA sitemap slugs...", file=sys.stderr)
    try:
        valid_slugs = fetch_sitemap_slugs()
    except (HTTPError, URLError, TimeoutError) as exc:
        print(f"Sitemap fetch failed: {exc}", file=sys.stderr)
        return 1
    print(f"  {len(valid_slugs)} model slugs", file=sys.stderr)

    if args.sitemap_only:
        aliases = build_slug_only_aliases(valid_slugs)
        write_index(aliases, valid_slugs, "sitemap-slugs")
        return 0

    api_key = os.environ.get("AA_API_KEY", "").strip()
    if not api_key:
        print(
            "AA_API_KEY is required to build the model list from Artificial Analysis.\n"
            "Set AA_API_KEY in the environment, or use --sitemap-only for a bundled "
            "fallback with exact slug matches only.",
            file=sys.stderr,
        )
        return 1

    try:
        api_models = fetch_api_models(api_key)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"AA API fetch failed: {exc}", file=sys.stderr)
        return 1
    print(f"  {len(api_models)} models from AA API", file=sys.stderr)

    aliases = build_aliases_from_api(api_models, valid_slugs)
    print(f"  {len(aliases)} aliases from AA API", file=sys.stderr)

    if not aliases:
        print("No aliases generated from AA API", file=sys.stderr)
        return 1

    write_index(aliases, valid_slugs, "sitemap+api")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
