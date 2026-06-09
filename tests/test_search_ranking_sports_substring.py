"""Regression: the sports-hint match must be word-boundary, not substring.

`_SPORTS_HINTS` contains "sport", which is a substring of "transport",
"passport", "sportswear", and of domains like "transport.gov". The old code
used `hint in text` / `hint in netloc`, so for any non-sports news query a
legitimate result mentioning "transport"/"passport" took the -1.5 sports
penalty and was pushed down the ranking. The query classifier had the same
flaw (a "passport" query was treated as a sports query). Both now use the
word-boundary `_SPORTS_HINT_RE`.

The same ranking module exists in two live copies: `services/search/ranking.py`
(the /api/search HTTP path) and `src/search/ranking.py` (the agent's
`web_search` tool path via `src/search/core.py`). Both are fixed and both are
covered here.
"""
import pytest

import services.search.ranking as services_ranking
import src.search.ranking as src_ranking

MODULES = [services_ranking, src_ranking]
MODULE_IDS = ["services", "src"]


@pytest.mark.parametrize("ranking", MODULES, ids=MODULE_IDS)
def test_sports_regex_ignores_substring_false_positives(ranking):
    for word in ("transport", "passport", "sportswear", "transportation"):
        assert ranking._SPORTS_HINT_RE.search(word) is None, word


@pytest.mark.parametrize("ranking", MODULES, ids=MODULE_IDS)
def test_sports_regex_still_matches_real_terms(ranking):
    for word in ("sport", "sports", "world cup", "the nba finals", "soccer match"):
        assert ranking._SPORTS_HINT_RE.search(word) is not None, word


@pytest.mark.parametrize("ranking", MODULES, ids=MODULE_IDS)
def test_transport_news_result_outranks_one_with_standalone_sport(ranking):
    # Non-sports news query (contains "latest"/"news"); subject term "transport".
    query = "latest transport news"
    results = [
        # B first in input; identical except B carries a standalone "sport" word.
        {"title": "City transport plan", "snippet": "the transport plan details and sport",
         "url": "https://example.org/b", "age": "1 day"},
        {"title": "City transport plan", "snippet": "the transport plan details",
         "url": "https://example.org/a", "age": "1 day"},
    ]
    ranked = ranking.rank_search_results(query, results)
    # With word-boundary matching only B (standalone "sport") is penalized, so the
    # plain transport result rises to the top. Pre-fix both were penalized equally
    # (via "transport") and input order was preserved, leaving B on top.
    assert ranked[0]["url"] == "https://example.org/a"
