#!/usr/bin/env python3
"""deep_research.py — free, no-billing scholarly research.

The DEFAULT research path. Uses only free, no-key academic APIs (OpenAlex,
Semantic Scholar, PubMed) plus polite rate-limited arXiv, with local caching
so repeated queries never re-hit the wire. Baloney detection (research_lens)
filters results before they're returned, so the persona never builds on
unvetted claims.

Backends (all free, all no-key):
  OpenAlex          — largest open scholarly index; best coverage, reliable
  Semantic Scholar  — strongest on recent (2025-26) agent/LLM papers
  PubMed/Entrez     — biomedical + many NLP/psych articles
  arXiv             — polite (rate-limited + backoff); preprints

Pipeline:
  expand (research_lens) -> query each backend (cached) -> dedupe by DOI/title
  -> baloney-detect each hit (research_lens.assess_source) -> score+sort
  -> fetch abstracts for the top hits -> emit a brief (sources + verdicts)

Everything is cached under <memory>/research/ so research compounds: a query
run once is free forever after. No key, no billing, no rate-limit guilt.

Usage:
  deep_research.py search "<query>" [--n 10] [--abstracts] [--json]
  deep_research.py get <doi-or-title>    # fetch + assess one work
  deep_research.py cache                  # show cached query sizes
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

import sys, os
_SD = os.path.dirname(os.path.abspath(__file__))
if _SD not in sys.path: sys.path.insert(0, _SD)
import memory_env

CACHE_DIR = os.path.join(memory_env.memory_dir(), "research")
UA = "agent-memory-platform/1.0 (free research; no billing)"


def _get_json(url, timeout=30, retries=2):
    """GET a JSON API with retry + backoff (rate-limit politeness)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    last = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            last = e
            time.sleep(1.5 * (attempt + 1))  # backoff for 429s
    return {}


def _cache_key(backend, query):
    return hashlib.sha256(f"{backend}:{query}".encode()).hexdigest()[:16]


def _cache_get(backend, query):
    try:
        p = os.path.join(CACHE_DIR, f"{_cache_key(backend, query)}.json")
        if os.path.exists(p):
            return json.load(open(p))
    except Exception:
        pass
    return None


def _cache_put(backend, query, data):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(os.path.join(CACHE_DIR, f"{_cache_key(backend, query)}.json"),
                  "w") as f:
            json.dump(data, f)
    except Exception:
        pass


# ------------------------------------------------------------------ backends --

def _openalex(query, per_page=8):
    """OpenAlex: no key, best coverage. URL-encode the query."""
    url = ("https://api.openalex.org/works?search=" +
           urllib.parse.quote(query) +
           f"&per-page={per_page}&select=id,doi,title,publication_date,"
           "publication_year,abstract_inverted_index,cited_by_count,"
           "primary_location")
    return _get_json(url)


def _invert_abstract(inv):
    """OpenAlex stores abstracts as inverted indexes; rebuild plain text."""
    if not inv:
        return ""
    positions = {}
    for word, idxs in inv.items():
        for i in idxs:
            positions[i] = word
    return " ".join(positions[i] for i in sorted(positions))


def _semantic_scholar(query, limit=8):
    url = ("https://api.semanticscholar.org/graph/v1/paper/search?query=" +
           urllib.parse.quote(query) +
           f"&limit={limit}&fields=title,abstract,year,venue,externalIds,"
           "citationCount,openAccessPdf")
    return _get_json(url)


def _pubmed(query, retmax=6):
    """PubMed/Entrez: eutils esearch -> esummary (titles + abstracts)."""
    es = _get_json("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
                   f"?db=pubmed&term={urllib.parse.quote(query)}&retmode=json"
                   f"&retmax={retmax}")
    ids = (es.get("esearchresult") or {}).get("idlist") or []
    if not ids:
        return {"results": []}
    eu = _get_json("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
                   f"?db=pubmed&id={','.join(ids)}&retmode=json")
    docs = (eu.get("result") or {})
    out = []
    for pid in ids:
        d = docs.get(pid) or {}
        out.append({"title": d.get("title", ""), "year": d.get("pubdate", "")[:4],
                    "venue": d.get("fulljournalname", ""),
                    "doi": (d.get("elocationid") or "").replace("doi: ", ""),
                    "abstract": d.get("abstract", "")})
    return {"results": out}


def _arxiv(query, max_results=5):
    """Polite arXiv: rate-limited with backoff (arXiv enforces ~3s/call)."""
    url = ("https://export.arxiv.org/api/query?search_query=" +
           urllib.parse.quote("all:" + query) + f"&max_results={max_results}")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            xml = r.read().decode()
        titles = re.findall(r"<title>([^<]+)</title>", xml)[1:]  # skip feed title
        sums = re.findall(r"<summary>([^<]+)</summary>", xml)
        out = []
        for i, t in enumerate(titles):
            out.append({"title": t.strip(),
                        "abstract": sums[i].strip() if i < len(sums) else "",
                        "venue": "arXiv preprint"})
        return {"results": out}
    except Exception as e:
        time.sleep(4)  # arXiv 429 -> respect and retry once
        return {"results": []}


def _hal(query, rows=5):
    """HAL — the French national open archive (free, worldwide research)."""
    url = ("https://api.archives-ouvertes.fr/search/?q=" +
           urllib.parse.quote(query) +
           f"&fl=title_s,producedDate_s,abstract_s&rows={rows}&wt=json")
    raw = _get_json(url)
    docs = (raw.get("response") or {}).get("docs") or []
    out = []
    for d in docs:
        t = d.get("title_s") or []
        out.append({"title": t[0] if t else "",
                    "abstract": (d.get("abstract_s") or [""])[0] or "",
                    "year": (d.get("producedDate_s") or "")[:4],
                    "venue": "HAL (French national archive)"})
    return {"results": out}


def _openaire(query, size=5):
    """OpenAIRE — the EU research aggregator (free)."""
    url = ("https://api.openaire.eu/search/publications?keywords=" +
           urllib.parse.quote(query) + f"&size={size}&format=json")
    raw = _get_json(url)
    resp = raw.get("response") or {}
    res = (resp.get("results") or {}).get("result") or []
    out = []
    for r in res:
        m = (r.get("metadata") or {}).get("oaf:entity") or {}
        title = ""
        yr = ""
        for t in (m.get("title") or []):
            title = t.get("$", "")
            break
        try:
            date = ((m.get("dateofacceptance") or [{}])[0].get("$") or "")
            yr = date[:4]
        except Exception:
            yr = ""
        out.append({"title": title, "abstract": "", "year": yr,
                    "venue": "OpenAIRE (EU)"})
    return {"results": out}


def _doaj(query, page_size=5):
    """DOAJ — Directory of Open Access Journals (global, free)."""
    url = ("https://doaj.org/api/v2/search/articles/" +
           urllib.parse.quote(query) + f"?pageSize={page_size}")
    raw = _get_json(url)
    out = []
    for r in (raw.get("results") or []):
        bib = r.get("bibjson") or {}
        out.append({"title": bib.get("title") or "",
                    "abstract": (bib.get("abstract") or "")[:300],
                    "year": "",
                    "venue": "DOAJ (open access)"})
    return {"results": out}


# ------------------------------------------------------------------ pipeline --

# Global backend map. OpenAlex is the widest (indexes CNKI/Wanfang, French,
# EU, worldwide) — the others add regional depth. 'all' = every free source.
BACKENDS = {
    "all": ("openalex", "s2", "pubmed", "arxiv", "hal", "openaire", "doaj"),
    "openalex": ("openalex",),       # worldwide (incl. CNKI, French, EU)
    "s2": ("s2",),                   # Semantic Scholar — recent papers
    "pubmed": ("pubmed",),           # biomedical
    "arxiv": ("arxiv",),             # preprints (polite)
    "eu": ("openaire", "openalex"),  # European + worldwide
    "fr": ("hal", "openalex"),       # French + worldwide
    "cn": ("openalex",),             # Chinese coverage via OpenAlex's CNKI index
    "oa": ("doaj",),                 # global open-access journals only
}


def _normalise(hit):
    """Normalise a backend hit into {title, year, venue, doi, abstract, score}."""
    return {
        "title": hit.get("title") or "",
        "year": hit.get("year") or hit.get("publication_year") or "",
        "venue": hit.get("venue") or "",
        "doi": hit.get("doi") or (hit.get("externalIds") or {}).get("DOI") or "",
        "abstract": (hit.get("abstract") or ""),
        "citations": hit.get("cited_by_count") or hit.get("citationCount") or 0,
    }


def search(query, n=10, with_abstracts=False, sources="all", since=None):
    """Search the free backends (cached), baloney-detect, dedupe, return a brief.

    `sources` selects the region/scope: all | openalex | s2 | pubmed | arxiv |
    eu | fr | cn | oa. Default 'all' = worldwide free indexes.
    `since` sets a hard recency floor in years (e.g. since=2 = only papers from
    this year or last). Stale research is dropped, never padded.
    """
    backends = BACKENDS.get(sources, BACKENDS["all"])
    hits = []
    seen = set()
    for backend in backends:
        cached = _cache_get(backend, query)
        if cached is None:
            if backend == "openalex":
                raw = _openalex(query, per_page=n)
                items = [dict(r) for r in (raw.get("results") or [])]
                for it in items:
                    it["abstract"] = _invert_abstract(it.get("abstract_inverted_index"))
            elif backend == "s2":
                raw = _semantic_scholar(query, limit=n)
                items = raw.get("data") or []
            elif backend == "pubmed":
                raw = _pubmed(query, retmax=n // 2)
                items = raw.get("results") or []
            elif backend == "arxiv":
                raw = _arxiv(query, max_results=n // 2)
                items = raw.get("results") or []
            elif backend == "hal":
                raw = _hal(query, rows=n // 2)
                items = raw.get("results") or []
            elif backend == "openaire":
                raw = _openaire(query, size=n // 2)
                items = raw.get("results") or []
            elif backend == "doaj":
                raw = _doaj(query, page_size=n // 2)
                items = raw.get("results") or []
            else:
                items = []
            _cache_put(backend, query, items)
        else:
            items = cached
        for it in items:
            norm = _normalise(it)
            key = (norm["doi"] or norm["title"]).lower()
            if not norm["title"] or key in seen:
                continue
            seen.add(key)
            norm["backend"] = backend
            hits.append(norm)

    # Baloney-detect each hit (research lens: falsifiable, no-overclaim, etc.)
    # IMPORTANT: the lens assesses CLAIMS, not metadata. A hit with no abstract
    # carries nothing to assess — mark it UNASSESSED (included, unvetted) rather
    # than REJECT (would wrongly filter relevant papers that lack abstracts).
    try:
        import research_lens as rl
        for h in hits:
            if not (h.get("abstract") or "").strip():
                h["verdict"] = "UNASSESSED"
                continue
            v = rl.assess_source(h["title"], h["abstract"][:400])
            h["verdict"] = v[0] if isinstance(v, tuple) else (v or {}).get("verdict", "WEAK")
    except Exception:
        for h in hits:
            h["verdict"] = "UNASSESSED"

    # RECENCY: today's date is 2026-08 — "latest research" means this year and
    # last, not a year-behind default. Rank by recency as a first-class axis:
    #   current year (2026) +4, last year (2025) +3, 2024 +2, 2023 +1, older 0.
    # The verdict still gates (REJECT stays last), but a fresh paper is never
    # buried under an old classic. This curves the stale-research default that
    # LLM search tools fall into when they rank by citations alone.
    import datetime as _dt
    _cur = _dt.date.today().year
    def _recency_score(year):
        try:
            y = int(year)
        except Exception:
            return 0
        age = _cur - y
        if age <= 0:
            return 4
        if age == 1:
            return 3
        if age == 2:
            return 2
        if age == 3:
            return 1
        return 0

    for h in hits:
        h["recency"] = _recency_score(h.get("year"))

    # HARD RECENCY FLOOR: --since drops everything older than N years, so a
    # "latest research" query never returns a year-behind default. The tool
    # refuses stale research rather than silently padding the results.
    if since is not None:
        floor = _cur - since
        hits = [h for h in hits
                if (str(h.get("year") or "").isdigit() and int(h["year"]) >= floor)]

    # Sort: verdict tier first (REJECT always last), then RECENCY desc (latest
    # first), then citations as the tiebreak. Fresh + vetted beats old + famous.
    order = {"STRONG": 0, "UNASSESSED": 1, "WEAK": 2, "REJECT": 3}
    hits.sort(key=lambda h: (order.get(h.get("verdict"), 3),
                             -h.get("recency", 0),
                             -(h.get("citations") or 0)))

    out = hits[:n]
    if with_abstracts:
        return {"query": query, "count": len(out), "results": out}
    slim = []
    for h in out:
        slim.append({k: h.get(k) for k in
                     ("title", "year", "venue", "doi", "verdict", "citations", "backend")})
    return {"query": query, "count": len(slim), "results": slim}


def get_one(identifier):
    """Fetch + assess a single work by DOI or title."""
    if identifier.startswith("10."):
        url = ("https://api.openalex.org/works/https://doi.org/" +
               urllib.parse.quote(identifier))
        raw = _get_json(url)
        if raw.get("title"):
            item = dict(raw)
            item["abstract"] = _invert_abstract(item.get("abstract_inverted_index"))
            return _normalise(item)
    return search(identifier, n=3, with_abstracts=True)


def cache_report():
    try:
        files = os.listdir(CACHE_DIR)
        total = sum(os.path.getsize(os.path.join(CACHE_DIR, f)) for f in files)
        return {"queries_cached": len(files), "cache_bytes": total,
                "cache_dir": CACHE_DIR}
    except Exception:
        return {"queries_cached": 0, "cache_bytes": 0, "cache_dir": CACHE_DIR}


def main():
    ap = argparse.ArgumentParser(description="Free, no-billing scholarly research")
    ap.add_argument("cmd", choices=["search", "get", "cache"])
    ap.add_argument("arg", nargs="*", default=[])
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--abstracts", action="store_true")
    ap.add_argument("--sources", default="all",
                    help="region/scope: all|openalex|s2|pubmed|arxiv|eu|fr|cn|oa")
    ap.add_argument("--since", type=int, default=None,
                    help="hard recency floor: only papers within N years (e.g. --since 2)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.cmd == "cache":
        print(json.dumps(cache_report(), indent=2) if args.json else
              f"cache: {cache_report()['queries_cached']} queries, "
              f"{cache_report()['cache_bytes']//1024} KB in "
              f"{cache_report()['cache_dir']}")
        return

    if args.cmd == "get":
        res = get_one(" ".join(args.arg))
        print(json.dumps(res, indent=2) if args.json else
              f"title: {res.get('title','')}\nyear: {res.get('year','')}\n"
              f"venue: {res.get('venue','')}\ndoi: {res.get('doi','')}\n"
              f"abstract: {res.get('abstract','')[:400]}")
        return

    q = " ".join(args.arg)
    if not q:
        print("usage: deep_research.py search '<query>' [--sources all|eu|fr|cn|oa]")
        return
    res = search(q, n=args.n, with_abstracts=args.abstracts,
                 sources=args.sources, since=args.since)
    if args.json:
        print(json.dumps(res, indent=2))
        return
    print(f"# Research ({args.sources} sources): {q}\n")
    for h in res["results"]:
        line = f"[{h.get('verdict','?'):6}] {h.get('title','')[:80]}"
        if h.get("year"):
            line += f" ({h['year']})"
        print(line)
        if args.abstracts and h.get("abstract"):
            print(f"        {h['abstract'][:180]}...")
        print(f"        {h.get('backend','')} | {h.get('doi','') or h.get('venue','')}")


if __name__ == "__main__":
    main()
