"""Regression: snippet and subject-term matching must be word-boundary.

#1473 converted the title and sports-hint matches in ranking.py to word
boundaries, but left two raw substring tests behind:

  - snippet_score: ``term in snippet.lower()`` — query term "port" hits
    "transport"/"support", inflating a result's relevance.
  - news_quality_adjustment: ``t in text or t in netloc`` for the subject term —
    query "us" substring-matches "business"/"music", so an off-topic page
    wrongly escapes the off-topic penalty for a country/subject news query.

Both now go through ``_has_word`` (the same ``\\b...\\b`` pattern title_score
uses), so a short term no longer matches inside an unrelated word.

``rank_search_results`` is exercised on both the services module (the
/api/search path) and the src re-export shim (the agent web_search path).
"""
import pytest

import services.search.ranking as services_ranking
import src.search.ranking as src_ranking

RANK_MODULES = [services_ranking, src_ranking]
RANK_IDS = ["services", "src"]


# --- _has_word helper (defined in the services module) ---------------------

def test_has_word_rejects_substring_false_positives():
    assert services_ranking._has_word("business and music", "us") is False
    assert services_ranking._has_word("transport and support", "port") is False
    assert services_ranking._has_word("passport office", "sport") is False


def test_has_word_matches_standalone_terms():
    assert services_ranking._has_word("the us economy", "us") is True
    assert services_ranking._has_word("port forwarding guide", "port") is True


# --- snippet_score: substring term must not inflate relevance ---------------

@pytest.mark.parametrize("ranking", RANK_MODULES, ids=RANK_IDS)
def test_snippet_substring_does_not_outrank_a_true_nonmatch(ranking):
    # Non-news query so only snippet relevance differs (no news adjustment).
    query = "port forwarding"
    results = [
        # C first: a genuine non-match (no query word at all).
        {"title": "Networking notes", "snippet": "weather updates today",
         "url": "https://example.org/c", "age": "1 day"},
        # B: contains "port" only inside "transport"/"support" (substring).
        {"title": "Networking notes", "snippet": "transport and support",
         "url": "https://example.org/b", "age": "1 day"},
    ]
    ranked = ranking.rank_search_results(query, results)
    # Pre-fix B got a spurious term hit and outranked C; post-fix they have the
    # same (zero) snippet term match, so input order stands and C stays first.
    assert ranked[0]["url"] == "https://example.org/c"


# --- subject-term off-topic penalty: substring must not suppress it ---------

@pytest.mark.parametrize("ranking", RANK_MODULES, ids=RANK_IDS)
def test_offtopic_subject_substring_is_still_penalized(ranking):
    # News query with subject term "us". B mentions "us" only inside
    # "business"; A mentions "us" as a standalone word. The snippets are padded
    # past the 200-char length cap and are otherwise identical, so both sides
    # have equal base scores and the ONLY thing that can differ is the off-topic
    # penalty — isolating the bug from incidental length/term scoring.
    filler = (
        "regional market report covered many provincial topics and figures in "
        "detail over the period with extra commentary and analysis written for "
        "readers wanting more depth on the matter at hand and well into the "
        "following week ahead"
    )
    query = "us news"
    results = [
        # B first: off-topic, "us" only as a substring of "business".
        {"title": "Daily roundup", "snippet": "business economy and policy. " + filler,
         "url": "https://example.org/b", "age": "1 day"},
        # A: on-topic, standalone "us".
        {"title": "Daily roundup", "snippet": "us economy and policy. " + filler,
         "url": "https://example.org/a", "age": "1 day"},
    ]
    ranked = ranking.rank_search_results(query, results)
    # Pre-fix B escaped the off-topic penalty (substring "us") so the tie kept
    # input order (B on top); post-fix B takes the -1.0 penalty and A rises.
    assert ranked[0]["url"] == "https://example.org/a"
