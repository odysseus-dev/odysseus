"""Research fan-out — parallel multi-provider web search with merged results.

Backs the `operator_research` agent tool. Queries TinyFish, Perplexity, and
Firecrawl concurrently (skipping providers without a configured key), merges
and deduplicates by normalized URL, and ranks the merged list. Failure-
isolated: one provider's error or timeout never sinks the call, and an overall
deadline returns partial results.
"""

from __future__ import annotations

import concurrent.futures as cf
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

from services.operator.core import CAP_RESEARCH, envelope, require_capability

logger = logging.getLogger(__name__)

FANOUT_PROVIDERS = ("tinyfish", "perplexity", "firecrawl")
DEFAULT_DEADLINE = 20.0


def _provider_fns() -> Dict[str, Callable]:
    from services.search.providers import (
        firecrawl_search,
        perplexity_search,
        tinyfish_search,
    )
    return {
        "tinyfish": tinyfish_search,
        "perplexity": perplexity_search,
        "firecrawl": firecrawl_search,
    }


def _configured_providers() -> List[str]:
    from services.search.providers import _get_provider_key
    configured = []
    for name in FANOUT_PROVIDERS:
        try:
            if _get_provider_key(name):
                configured.append(name)
        except Exception:
            continue
    return configured


def _normalize_url(url: str) -> str:
    """Dedupe key: drop scheme, leading www, fragment, and trailing slash."""
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip().lower()
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parts.path.rstrip("/")
    return urlunsplit(("", netloc, path, parts.query, "")).lstrip("/")


def _merge(per_provider: Dict[str, List[dict]]) -> List[dict]:
    """Interleave providers by rank, deduping by normalized URL.

    Round-robin across providers so a diverse top-N surfaces before any single
    provider's long tail; first provider to contribute a URL owns it and
    records which others also returned it.
    """
    merged: List[dict] = []
    seen: Dict[str, dict] = {}
    max_len = max((len(v) for v in per_provider.values()), default=0)
    for rank in range(max_len):
        for provider, hits in per_provider.items():
            if rank >= len(hits):
                continue
            hit = hits[rank]
            key = _normalize_url(hit.get("url", ""))
            if not key:
                continue
            if key in seen:
                seen[key].setdefault("also_from", [])
                if provider not in seen[key]["also_from"]:
                    seen[key]["also_from"].append(provider)
                continue
            entry = {
                "title": hit.get("title", ""),
                "url": hit.get("url", ""),
                "snippet": hit.get("snippet", ""),
                "age": hit.get("age", ""),
                "source": provider,
            }
            seen[key] = entry
            merged.append(entry)
    return merged


def operator_research(
    query: str,
    count: Optional[int] = None,
    deadline: float = DEFAULT_DEADLINE,
) -> Dict[str, Any]:
    """Fan out a query across configured providers and merge the results."""
    query = (query or "").strip()
    if not query:
        return envelope(CAP_RESEARCH, False, reason="empty_query")

    gate = require_capability(CAP_RESEARCH)
    if gate:
        return gate

    providers = _configured_providers()
    if not providers:
        return envelope(
            CAP_RESEARCH, False, reason="no_providers",
            hint="Configure a TinyFish, Perplexity, or Firecrawl API key in Admin settings.",
        )

    fns = _provider_fns()
    per_provider: Dict[str, List[dict]] = {}
    errors: Dict[str, str] = {}
    skipped = [p for p in FANOUT_PROVIDERS if p not in providers]

    def _run(provider: str) -> Tuple[str, List[dict]]:
        return provider, fns[provider](query, count)

    with ThreadPoolExecutor(max_workers=len(providers)) as pool:
        futures = {pool.submit(_run, p): p for p in providers}
        done, not_done = cf.wait(futures, timeout=deadline)
        for fut in done:
            provider = futures[fut]
            try:
                name, hits = fut.result()
                per_provider[name] = hits or []
            except Exception as exc:  # provider blew up — isolate it
                errors[provider] = str(exc)
                logger.warning("operator_research provider %s failed: %s", provider, exc)
        for fut in not_done:
            provider = futures[fut]
            errors[provider] = "timeout"
            fut.cancel()

    merged = _merge(per_provider)
    data = {
        "query": query,
        "results": merged,
        "result_count": len(merged),
        "providers_used": sorted(per_provider.keys()),
        "providers_skipped": skipped,
    }
    if errors:
        data["provider_errors"] = errors
    # Success as long as at least one provider answered, even partially.
    ok = bool(per_provider)
    return envelope(CAP_RESEARCH, ok, data=data, degraded=bool(errors) and ok,
                    reason=None if ok else "all_providers_failed")
