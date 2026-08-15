"""Tests for the platform's deep_research.py (free worldwide research tool).

NOTE: the other test_deep_research_*.py files in this directory test the
Odysseus app's `src/deep_research.py` — a different module. This file tests
`memory_platform/deep_research.py`, the free no-billing research tool wired
into the memory plugin.

Verifies the recency-first design (the fix from the stale-research lesson):
- `--since` is a HARD recency floor: older papers are dropped, never padded
- recency is a first-class ranking axis: a fresh paper outranks an old classic
- verdict tier gates sorting (REJECT always last) but fresh vetted beats old
- backends normalise to the same shape (dedupe + scoring work on it)
- the baloney lens runs in PRIMARY mode (a paper is the primary source about
  its own system) so hedged scientific abstracts are not wrongly REJECTed

All network backends are monkeypatched; no live calls in tests.
"""

import json
import os
import sys

import pytest

_HERE = os.path.dirname(__file__)
# Works from both layouts: source repo (memory_platform/) and the private
# repo / deployed copy (flat scripts/).
for _sub in ("memory_platform", "scripts"):
    _p = os.path.join(_HERE, "..", _sub)
    if os.path.isdir(_p):
        sys.path.insert(0, _p)
        break
import deep_research as dr  # noqa: E402


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(dr, "CACHE_DIR", str(tmp_path / "research"))
    return tmp_path


def _hit(title, year, citations=0, doi="", abstract=""):
    return {"title": title, "year": str(year), "venue": "test venue",
            "doi": doi, "abstract": abstract,
            "cited_by_count": citations}


class _NoCache:
    def __init__(self):
        self.writes = []

    def _cache_get(self, backend, query):
        return None

    def _cache_put(self, backend, query, data):
        self.writes.append((backend, query, data))


def _install_backend(monkeypatch, backend, items):
    """Point one backend at canned items, bypassing the disk cache."""
    calls = {}
    def _make(fn):
        def _wrapped(query, **kwargs):
            calls[query] = calls.get(query, 0) + 1
            return fn(query, **kwargs)
        return _wrapped

    if backend == "openalex":
        monkeypatch.setattr(dr, "_openalex",
                            _make(lambda q, per_page=8: {"results": items}))
    elif backend == "s2":
        monkeypatch.setattr(dr, "_semantic_scholar",
                            _make(lambda q, limit=8: {"data": items}))
    elif backend == "pubmed":
        monkeypatch.setattr(dr, "_pubmed",
                            _make(lambda q, retmax=6: {"results": items}))
    elif backend == "arxiv":
        monkeypatch.setattr(dr, "_arxiv",
                            _make(lambda q, max_results=5: {"results": items}))
    return calls


@pytest.fixture(autouse=True)
def no_disk_cache(monkeypatch):
    """Never touch the real research cache OR the real network in tests.
    Every backend defaults to empty; tests override the ones they exercise."""
    monkeypatch.setattr(dr, "_cache_get", lambda b, q: None)
    monkeypatch.setattr(dr, "_cache_put", lambda b, q, d: None)
    monkeypatch.setattr(dr, "_openalex", lambda q, per_page=8: {"results": []})
    monkeypatch.setattr(dr, "_semantic_scholar", lambda q, limit=8: {"data": []})
    monkeypatch.setattr(dr, "_pubmed", lambda q, retmax=6: {"results": []})
    monkeypatch.setattr(dr, "_arxiv", lambda q, max_results=5: {"results": []})
    monkeypatch.setattr(dr, "_hal", lambda q, rows=5: {"results": []})
    monkeypatch.setattr(dr, "_openaire", lambda q, size=5: {"results": []})
    monkeypatch.setattr(dr, "_doaj", lambda q, page_size=5: {"results": []})


def test_since_drops_stale_papers(cache_dir, monkeypatch):
    """--since 2 is a HARD floor: a 2023 classic is dropped, 2025-26 kept."""
    items = [
        _hit("Old classic cited everywhere", 2023, citations=5000),
        _hit("Fresh 2026 finding", 2026, citations=2),
        _hit("Last year's follow-up", 2025, citations=10),
        _hit("Unknown year paper", "", citations=0),
    ]
    _install_backend(monkeypatch, "openalex", items)
    res = dr.search("recency test", n=10, sources="openalex", since=2)
    years = {r["year"] for r in res["results"]}
    assert years == {"2026", "2025"}, f"stale paper leaked: {years}"
    assert all("Old classic" not in r["title"] for r in res["results"])


def test_no_since_keeps_old_but_ranks_recency_first(cache_dir, monkeypatch):
    """Without a floor, old papers remain but RECENCY outranks citations:
    a fresh paper with 2 citations beats a 2023 classic with 5000."""
    items = [
        _hit("Old classic cited everywhere", 2023, citations=5000),
        _hit("Fresh 2026 finding", 2026, citations=2),
        _hit("Last year's follow-up", 2025, citations=10),
    ]
    _install_backend(monkeypatch, "openalex", items)
    res = dr.search("ranking test", n=10, sources="openalex", since=None)
    assert res["results"][0]["title"] == "Fresh 2026 finding", \
        "a fresh paper must outrank an old classic"
    # 2025 next (recency + decent citations), then the old classic
    assert res["results"][1]["title"] == "Last year's follow-up"
    assert res["results"][2]["title"] == "Old classic cited everywhere"


def test_verdict_tier_gates_reject_last(cache_dir, monkeypatch):
    """REJECT stays last even when it is fresh — the lens verdict gates.
    Uses the s2 backend: it preserves abstracts (openalex rebuilds them from
    an inverted index, which a canned test hit doesn't carry)."""
    from deep_research import search
    items = [
        _hit("Rejected sensational paper", 2026, citations=100,
             abstract="shocking secret cure conspiracy the experts are hiding"),
        _hit("Solid recent work", 2025, citations=5,
             abstract="we conducted experiments and measured effect sizes "
                      "with independent replication"),
    ]
    _install_backend(monkeypatch, "s2", items)
    res = search("gating test", n=10, sources="s2", since=None,
                 with_abstracts=True)
    verdicts = [r["verdict"] for r in res["results"]]
    assert "REJECT" in verdicts
    assert res["results"][-1]["verdict"] == "REJECT", \
        "REJECT must sort last regardless of recency"


def test_primary_mode_does_not_reject_hedged_abstract(cache_dir, monkeypatch):
    """A scholarly paper is a PRIMARY source about its own system: hedged
    scientific prose ('cannot preserve coherence', 'a critical challenge')
    must NOT be REJECTed as overclaiming — that buried every valid paper."""
    items = [
        _hit("Hebbian Memory for Agents", 2026, citations=1,
             abstract="Long-term memory is a critical challenge for agents, "
                      "as fixed context windows cannot preserve coherence "
                      "across extended interactions. We propose a memory "
                      "system evaluated with measurements."),
    ]
    _install_backend(monkeypatch, "s2", items)
    res = dr.search("primary mode", n=10, sources="s2", since=None,
                    with_abstracts=True)
    assert res["results"][0]["verdict"] != "REJECT", \
        "a valid paper abstract must not be rejected in primary mode"


def test_dedupe_by_doi_and_title(cache_dir, monkeypatch):
    """The same paper from two backends is deduped, not doubled."""
    doi = "10.1234/test.2026"
    a = _hit("Duplicate paper", 2026, citations=3, doi=doi,
             abstract="a solid abstract with experiment and data")
    b = _hit("Duplicate paper", 2026, citations=3, doi=doi,
             abstract="a solid abstract with experiment and data")
    _install_backend(monkeypatch, "openalex", [a])
    monkeypatch.setattr(dr, "_semantic_scholar",
                        lambda q, limit=8: {"data": [b]})
    res = dr.search("dedupe test", n=10, sources="all", since=None)
    assert res["count"] == 1, f"duplicate not deduped: {res['results']}"


def test_backends_normalise_to_common_shape(cache_dir, monkeypatch):
    """Each backend normalises into {title, year, venue, doi, abstract,
    citations} so the scoring pipeline sees one shape."""
    _install_backend(monkeypatch, "openalex", [])
    monkeypatch.setattr(dr, "_semantic_scholar",
                        lambda q, limit=8: {"data": [
                            {"title": "S2 paper", "year": 2026,
                             "venue": "v", "externalIds": {"DOI": "10.x/y"},
                             "abstract": "measured data results",
                             "citationCount": 4}]})
    res = dr.search("s2 shape", n=10, sources="all", since=None,
                    with_abstracts=True)
    h = res["results"][0]
    for k in ("title", "year", "venue", "doi", "abstract", "citations"):
        assert k in h, f"missing normalised field {k}"
    assert h["doi"] == "10.x/y"


def test_search_cli_with_since_flag(cache_dir, monkeypatch):
    """The CLI surface (what the plugin invokes) accepts --since."""
    import subprocess
    items = [
        _hit("CLI old paper", 2023, citations=100),
        _hit("CLI fresh paper", 2026, citations=1),
    ]
    _install_backend(monkeypatch, "openalex", items)
    r = subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(dr.__file__),
                                      "deep_research.py"),
         "search", "cli since test", "--since", "2", "--sources", "openalex",
         "--n", "10", "--json"],
        capture_output=True, text=True, timeout=60, env=os.environ.copy())
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout or "{}")
    assert all(x["year"] in ("2026",) for x in out["results"]), \
        "CLI --since floor not enforced"
