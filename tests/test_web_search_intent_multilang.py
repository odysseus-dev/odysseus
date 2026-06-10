"""Web-search intent gating (multilingual).

Explicit "search the web" requests — in English AND French, with or without a
URL — must force-include ``web_search``/``web_fetch`` even when the embedding
retrieval is stubbed or its top-k budget is crowded (e.g. an open document is
injected into the agent context). Trivial prompts must NOT pull web tools, so we
do not reintroduce the #2684 regression (``test``/``yo`` triggering web search).

Reproduces the original report: a FRENCH request ("cherche sur le web les VLM")
made with a large document open lost ``web_search`` entirely, because the only
deterministic web gate (``_WEB_RE``) matched URLs only and the RAG top-k was
crowded out by the injected document.
"""
import pytest

from src.agent_loop import _classify_agent_request
from src.tool_index import ToolIndex


def _web_domain(message: str) -> bool:
    """True when the agent-loop request classifier tags a single-turn message as
    the 'web' domain, which seeds web_search/web_fetch on every selection path."""
    intent = _classify_agent_request([{"role": "user", "content": message}], message)
    return "web" in intent["domains"]


def _tools_for(query: str):
    """Run get_tools_for_query with the heavy __init__ skipped and retrieval
    stubbed to empty, so only the deterministic gates decide the result."""
    idx = ToolIndex.__new__(ToolIndex)
    idx.retrieve = lambda q, k=8: set()
    return idx.get_tools_for_query(query)


WEB_SEARCH_QUERIES = [
    # FR
    "cherche sur le web les VLM",
    "Cherche maintenant sur le web les VLM recents avec ton outil web_search",
    "fais une recherche internet sur les VLM",
    "recherche sur internet les derniers modeles",
    "recherche sur le web des infos sur les transformers",
    # EN
    "search the web for recent VLMs",
    "look up the latest VLMs online",
    "can you search online for the news",
    "google the latest AI news",
    "do a web search for python tutorials",
]

URL_QUERIES = [
    "open this link please",
    "visit https://example.com",
    "fetch this url for me",
]

NON_WEB_QUERIES = [
    "test",
    "yo",
    "what is 2+2",
    "edit the document",
    "resume ce document en 5 points",
    "remember that I like coffee",
    "write a python function",
    "open the file config.py",
    "explique moi comment fonctionne un transformer",
    "Google a publie un nouveau modele",  # talking ABOUT Google, not searching
]


@pytest.mark.parametrize("q", WEB_SEARCH_QUERIES)
def test_explicit_web_search_intent_includes_web_tools(q):
    tools = _tools_for(q)
    assert "web_search" in tools, f"web_search missing for: {q!r}"
    assert "web_fetch" in tools, f"web_fetch missing for: {q!r}"


@pytest.mark.parametrize("q", URL_QUERIES)
def test_url_intent_still_includes_web_tools(q):
    tools = _tools_for(q)
    assert {"web_search", "web_fetch"} <= tools, f"URL gate broken for: {q!r}"


@pytest.mark.parametrize("q", NON_WEB_QUERIES)
def test_trivial_prompts_do_not_pull_web_tools(q):
    tools = _tools_for(q)
    assert "web_search" not in tools, f"web_search wrongly added for: {q!r}"
    assert "web_fetch" not in tools, f"web_fetch wrongly added for: {q!r}"


def test_french_doc_open_repro():
    # The exact reproduction from the bug report.
    assert "web_search" in _tools_for("cherche sur le web les VLM")


# --- Agent-loop domain classifier (the all-paths gate) -----------------------
# get_tools_for_query only runs on the RAG path; the domain classifier feeds the
# deterministic "domain seeding" that runs on EVERY path (RAG, keyword fallback,
# caller-provided). It must recognise FR / non-URL web-search intent too, or a
# follow-up like "fais une recherche internet" silently loses web_search when the
# RAG retrieval falls back (e.g. a ChromaDB hiccup) or is crowded by an open doc.

WEB_DOMAIN_QUERIES = [
    "fais une recherche internet sur les VLM",
    "cherche sur le web les VLM",
    "recherche sur internet les derniers modeles",
    "recherche sur le web des infos sur les transformers",
    "cherche en ligne des exemples",
    "search the web for recent VLMs",
    "google the latest AI news",
    "look up the latest models online",
]

NON_WEB_DOMAIN_QUERIES = [
    "test",
    "yo",
    "edit the document",
    "resume ce document en 5 points",
    "lis la roadmap et resume-la",          # step 1 of the repro must NOT pull web
    "explique le protocole internet",        # 'internet' but not a search request
    "ecris une fonction python",
    "remember that I like coffee",
]


@pytest.mark.parametrize("q", WEB_DOMAIN_QUERIES)
def test_web_intent_classified_as_web_domain(q):
    assert _web_domain(q), f"'web' domain missing for: {q!r}"


@pytest.mark.parametrize("q", NON_WEB_DOMAIN_QUERIES)
def test_non_web_not_classified_as_web_domain(q):
    assert not _web_domain(q), f"'web' domain wrongly set for: {q!r}"
