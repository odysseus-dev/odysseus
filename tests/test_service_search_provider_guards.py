"""Regression tests for the canonical services.search provider implementation.

The old src.search provider path aliases this module; these tests pin the
behavior at the single implementation point.
"""

import ipaddress
import sys
import pytest

from services.search import core
from services.search import providers


@pytest.fixture(autouse=True)
def _deterministic_outbound_dns(monkeypatch):
    """Keep the outbound-URL policy running, but take it off live DNS.

    ``_scholarly_api_get`` validates each destination through
    ``check_outbound_url`` before it reaches the mocked ``httpx`` transport, and
    that check resolves the hostname. Without this stub these tests depend on
    real DNS: on a DNS64/NAT64 network an IPv4-only host such as
    export.arxiv.org resolves to ``64:ff9b::<v4>``, so the lookup is judged on a
    synthesised address that has nothing to do with the title-resolution,
    fallback, request-parameter and ordering behaviour asserted here.

    Only name resolution is replaced. The policy itself still runs in full, and
    a literal-IP host still resolves to itself, so a test that points at a
    prohibited address is still genuinely rejected. This is deliberately not a
    stand-in for SSRF/NAT64 coverage, which lives in tests/test_url_safety.py
    and tests/test_review_20260923_fixes.py.
    """

    def _resolve(host: str):
        try:
            return [str(ipaddress.ip_address(host))]
        except ValueError:
            return ["93.184.216.34"]  # public, policy-clean

    monkeypatch.setattr("src.url_safety._default_resolver", _resolve)


def test_html_transport_fallback_preserves_query_constraints(monkeypatch):
    seen = []
    class Response:
        is_success = True
        text = '<article class="result"><h3><a href="https://example.org/report">Report</a></h3><p class="content">Evidence</p></article>'
    def get(*args, **kwargs):
        seen.append(dict(kwargs['params']))
        if len(seen) == 1:
            raise ValueError('JSON transport unavailable')
        return Response()
    monkeypatch.setattr(providers, '_get_search_instance', lambda: 'http://searx.test')
    monkeypatch.setattr(providers, '_get_search_settings', lambda: {'search_safesearch': 'strict'})
    monkeypatch.setattr(providers, '_get_provider_key', lambda name: '')
    monkeypatch.setattr(providers.httpx, 'get', get)
    results = providers.searxng_search_api('AI news site:example.org', time_filter='week', engines='test-engine')
    assert len(results) == 1
    assert len(seen) == 2
    assert seen[1] == {k: v for k, v in seen[0].items() if k != 'format'}
    assert seen[1]['time_range'] == 'week'


@pytest.mark.parametrize('query,category', [
    ('AI developments this week', 'news'),
    ('recent developments in battery manufacturing', 'news'),
    ('latest developments in quantum computing', 'news'),
    ('web development tutorial this week', 'general'),
    ('current Firefox privacy documentation', 'general'),
    ('historical developments in mathematics', 'general'),
    ('latest Python version developments', 'general'),
])
def test_temporal_developments_select_news_not_reference_search(monkeypatch, query, category):
    seen = []
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {'results': [{'title': 'Result', 'url': 'https://example.org/report'}]}
    def get(*args, **kwargs):
        seen.append(kwargs['params'])
        return Response()
    monkeypatch.setattr(providers, '_get_search_instance', lambda: 'http://searx.test')
    monkeypatch.setattr(providers, '_get_search_settings', lambda: {})
    monkeypatch.setattr(providers, '_get_provider_key', lambda name: '')
    monkeypatch.setattr(providers.httpx, 'get', get)
    providers.searxng_search_api(query, time_filter='week')
    assert seen[0]['categories'] == category
    assert seen[0]['time_range'] == 'week'


def test_dead_credentialed_fallback_is_skipped_for_same_instance_engine(monkeypatch):
    monkeypatch.setattr(core, "_get_search_settings", lambda: {"search_fallback_chain": ["google_pse"]})
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {})
    monkeypatch.setattr(providers, "_get_provider_key", lambda name: "")
    assert core._build_provider_chain("searxng") == ["searxng", "searxng_yep"]


def test_valid_configured_fallback_is_preserved(monkeypatch):
    monkeypatch.setattr(core, "_get_search_settings", lambda: {"search_fallback_chain": ["google_pse"]})
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"google_pse_cx": "test-cx"})
    monkeypatch.setattr(providers, "_get_provider_key", lambda name: "test-key")
    assert core._build_provider_chain("searxng") == [
        "searxng", "searxng_yep", "google_pse",
    ]


def test_service_safesearch_values_match_provider_contract(monkeypatch):
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"search_safesearch": "strict"})
    assert providers._safesearch_for("searxng") == "2"
    assert providers._safesearch_for("brave") == "strict"
    assert providers._safesearch_for("duckduckgo_lib") == "on"
    assert providers._safesearch_for("duckduckgo_html") == "1"
    assert providers._safesearch_for("google_pse") == "active"
    assert providers._safesearch_for("serper") == "active"

    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"search_safesearch": "off"})
    assert providers._safesearch_for("searxng") == "0"
    assert providers._safesearch_for("brave") == "off"
    assert providers._safesearch_for("duckduckgo_lib") == "off"
    assert providers._safesearch_for("duckduckgo_html") == "-2"
    assert providers._safesearch_for("google_pse") is None
    assert providers._safesearch_for("serper") is None


def test_service_searxng_json_sends_safesearch(monkeypatch):
    seen = {}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {"title": "Result", "url": "https://example.com", "content": "Snippet"}
                ]
            }

    def fake_get(url, **kwargs):
        seen["url"] = url
        seen["params"] = kwargs["params"]
        return _Response()

    monkeypatch.setattr(providers, "_get_search_instance", lambda: "http://searx.test")
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"search_safesearch": "moderate"})
    monkeypatch.setattr(providers.httpx, "get", fake_get)

    results = providers.searxng_search_api("odysseus", count=1)

    assert results
    assert seen["url"] == "http://searx.test/search"
    assert seen["params"]["safesearch"] == "1"


@pytest.mark.parametrize('query,expected_time', [
    ('latest ollama release version github', 'day'),
    ('current Firefox Chrome privacy features comparison', 'day'),
    ('Sony headphone manual', 'day'),
])
def test_service_searxng_latest_release_uses_general_search(monkeypatch, query, expected_time):
    seen = {}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {
                        "title": "ollama/ollama releases",
                        "url": "https://github.com/ollama/ollama/releases",
                        "content": "Latest release v0.33.0",
                    }
                ]
            }

    def fake_get(url, **kwargs):
        seen["params"] = kwargs["params"]
        return _Response()

    monkeypatch.setattr(providers, "_get_search_instance", lambda: "http://searx.test")
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"search_safesearch": "strict"})
    monkeypatch.setattr(providers.httpx, "get", fake_get)

    results = providers.searxng_search_api(
        query,
        count=1,
        time_filter="day",
    )

    assert results
    assert seen["params"]["categories"] == "general"
    assert seen["params"]["engines"] == providers._GENERAL_ENGINES
    assert seen['params'].get('time_range') == expected_time


def test_service_searxng_specific_current_event_uses_news_search(monkeypatch):
    seen = {}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {
                        "title": "British widow faces deportation from Sweden",
                        "url": "https://example.com/news/story",
                        "content": "The 78-year-old has lived in Sweden for 22 years.",
                    }
                ]
            }

    def fake_get(url, **kwargs):
        seen["params"] = kwargs["params"]
        return _Response()

    monkeypatch.setattr(providers, "_get_search_instance", lambda: "http://sear.test")
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"search_safesearch": "moderate"})
    monkeypatch.setattr(providers.httpx, "get", fake_get)

    results = providers.searxng_search_api(
        "Sweden 78 year old British woman deportation Brexit residence application",
        count=5,
    )

    assert results
    assert seen["params"]["categories"] == "news"
    assert "engines" not in seen["params"]


def test_low_relevance_filter_ignores_generic_freshness_terms():
    results = core._filter_low_relevance_results(
        "latest ollama release version github",
        [
            {
                "title": "Fox News - Breaking News Updates",
                "url": "https://www.foxnews.com/",
                "snippet": "Latest Current News: U.S., World, Entertainment.",
            },
            {
                "title": "Releases · ollama/ollama - GitHub",
                "url": "https://github.com/ollama/ollama/releases",
                "snippet": "Get up and running with Kimi, DeepSeek, Qwen and other models.",
            },
        ],
    )

    assert [r["url"] for r in results] == ["https://github.com/ollama/ollama/releases"]


def test_low_relevance_filter_rejects_one_broad_match_for_specific_query():
    results = core._filter_low_relevance_results(
        "Sweden 78 year old British woman deportation Brexit residence application",
        [
            {
                "title": "Geography of Sweden",
                "url": "https://en.wikipedia.org/wiki/Geography_of_Sweden",
                "snippet": "Sweden is a country in Northern Europe.",
            },
            {
                "title": "British widow faces deportation from Sweden after 22 years",
                "url": "https://example.com/news/british-widow-sweden",
                "snippet": "A 78-year-old woman missed a Brexit residence application.",
            },
        ],
    )

    assert [r["url"] for r in results] == [
        "https://example.com/news/british-widow-sweden"
    ]


def test_low_relevance_filter_treats_ai_as_subject_not_news_as_subject():
    results = core._filter_low_relevance_results(
        "Latest news in AI",
        [
            {
                "title": "Anthropic researcher quits over AI risks",
                "url": "https://example.com/technology/anthropic-ai",
                "snippet": "The departure highlights concern inside AI labs.",
            },
            {
                "title": "Latest world news and headlines",
                "url": "https://example.com/world",
                "snippet": "Breaking updates from around the world.",
            },
        ],
    )

    assert [r["url"] for r in results] == [
        "https://example.com/technology/anthropic-ai"
    ]


def test_provider_query_removes_generic_interrogative_shell():
    assert core._provider_friendly_query(
        "What year did Ethiopia become independent"
    ) == "Ethiopia become independent year"


def test_provider_query_separates_conversational_freshness_shell_from_subject():
    assert core._provider_friendly_query(
        "Any latest info on quantum physics"
    ) == "quantum physics"
    assert core._meaningful_query_terms(
        "Any latest info on quantum physics"
    ) == ["quantum", "physics"]


def test_relevance_matching_accepts_conservative_word_family_variants():
    results = core._filter_low_relevance_results(
        "Ethiopia become independent year",
        [
            {
                "title": "History of Ethiopia",
                "url": "https://example.com/ethiopia-history",
                "snippet": "The country's independence and periods of occupation.",
            },
            {
                "title": "Calendar year",
                "url": "https://example.com/calendar-year",
                "snippet": "A year has twelve months.",
            },
        ],
    )

    assert [r["url"] for r in results] == ["https://example.com/ethiopia-history"]


def test_low_relevance_filter_weather_queries_require_location_terms():
    results = core._filter_low_relevance_results(
        "Kyoto weather forecast tomorrow August 27 2026",
        [
            {
                "title": "Weather Tomorrow for Kyoto-shi, Kyoto, Japan",
                "url": "https://www.accuweather.com/en/jp/kyoto-shi/224436/weather-tomorrow/224436",
                "snippet": "Detailed forecast including temperature and rain.",
            },
            {
                "title": "Seattle, WA Weather Forecast",
                "url": "https://www.accuweather.com/en/us/seattle/98104/weather-forecast/351409",
                "snippet": "Seattle weather forecast with current conditions.",
            },
            {
                "title": "Kyoto - Wikipedia",
                "url": "https://en.wikipedia.org/wiki/Kyoto",
                "snippet": "Kyoto is a city in Japan.",
            },
            {
                "title": "Kyoto Travel | Kyoto City Official Guide",
                "url": "https://kyoto.travel/en/",
                "snippet": "Kyoto tourism tips, itineraries, and things to do.",
            },
        ],
    )

    assert [r["url"] for r in results] == [
        "https://www.accuweather.com/en/jp/kyoto-shi/224436/weather-tomorrow/224436"
    ]


def test_weather_query_rewrite_moves_location_first():
    assert (
        core._subject_first_weather_query("What is the weather in Kyoto tomorrow?")
        == "Kyoto weather forecast tomorrow"
    )


def test_scholarly_title_extraction_prefers_probable_paper_title():
    assert core._scholarly_title_from_query(
        '"Attention Is All You Need" "Table 2" "Training Cost" FLOPS'
    ) == "Attention Is All You Need"
    assert core._scholarly_title_from_query(
        'Find the paper "LLaVA-OneVision: Easy Visual Task Transfer" Table 5'
    ) == "LLaVA-OneVision: Easy Visual Task Transfer"
    assert core._scholarly_title_from_query('search for "ordinary quoted phrase"') == ""


def test_scholarly_arxiv_fallback_prepends_only_strong_title_match(monkeypatch):
    seen = {}
    atom = """\
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/1706.03762v7</id>
    <title>Attention Is All You Need</title>
    <summary>We propose the Transformer architecture.</summary>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/0000.00000v1</id>
    <title>Attention Mechanisms in an Unrelated Setting</title>
    <summary>An unrelated result.</summary>
  </entry>
</feed>
"""

    class _Response:
        text = atom

        def raise_for_status(self):
            return None

    def _fake_get(url, **kwargs):
        seen["url"] = url
        seen["params"] = kwargs["params"]
        return _Response()

    monkeypatch.setattr(core, "searxng_search_api", lambda *args, **kwargs: [])
    monkeypatch.setattr(core, "_openalex_title_results", lambda *args, **kwargs: [])
    monkeypatch.setattr(core.httpx, "get", _fake_get)
    generic = [{
        "title": "ATTENTION | English meaning",
        "url": "https://dictionary.example/attention",
        "snippet": "A definition of attention.",
    }]

    results = core._augment_scholarly_results(
        '"Attention Is All You Need" "Table 2" "Training Cost" FLOPS',
        generic,
        5,
    )

    assert seen["url"] == "https://export.arxiv.org/api/query"
    assert seen["params"]["search_query"] == 'ti:"Attention Is All You Need"'
    assert results[0]["title"] == "Attention Is All You Need"
    assert results[0]["url"] == "https://arxiv.org/abs/1706.03762v7"
    assert results[1:] == generic


def test_explicit_arxiv_identifier_bypasses_noisy_or_empty_serp():
    noisy = [{
        "title": "arXiv - Wikipedia",
        "url": "https://en.wikipedia.org/wiki/ArXiv",
        "snippet": "Generic repository article.",
    }]

    results = core._augment_scholarly_results(
        "arXiv:2404.14219 Phi-3 publication venue",
        noisy,
        5,
    )

    assert results[0] == {
        "title": "arXiv:2404.14219 — exact identifier match",
        "url": "https://arxiv.org/abs/2404.14219",
        "snippet": (
            "Official arXiv landing page resolved directly from the exact "
            "identifier in the query."
        ),
        "source": "arxiv",
    }
    assert results[1:] == noisy


def test_explicit_arxiv_identifier_deduplicates_versions_and_urls():
    results = core._augment_scholarly_results(
        "Compare arxiv.org/pdf/2408.03326v2 with arXiv:2408.03326",
        [{
            "title": "existing",
            "url": "https://arxiv.org/abs/2408.03326",
            "snippet": "duplicate",
        }],
        5,
    )

    assert [item["url"] for item in results] == [
        "https://arxiv.org/abs/2408.03326"
    ]


def test_explicit_arxiv_publication_query_prepends_formal_openalex_match(monkeypatch):
    seen = []

    def _fake_openalex(title, count):
        seen.append((title, count))
        return [
            {
                "title": (
                    "Molmo and PixMo: Open Weights and Open Data for "
                    "State-of-the-Art Vision-Language Models"
                ),
                "url": "https://doi.org/10.1109/cvpr52734.2025.00018",
                "snippet": (
                    "Exact scholarly-title match from OpenAlex metadata. "
                    "Formal publication: 2025 IEEE/CVF Conference on Computer "
                    "Vision and Pattern Recognition (CVPR), 2025."
                ),
                "source": "openalex",
            },
            {
                "title": (
                    "Molmo and PixMo: Open Weights and Open Data for "
                    "State-of-the-Art Vision-Language Models"
                ),
                "url": "https://arxiv.org/abs/2409.17146",
                "snippet": "Preprint record.",
                "source": "openalex",
            },
        ]

    monkeypatch.setattr(core, "_openalex_title_results", _fake_openalex)

    results = core._augment_scholarly_results(
        "Molmo and PixMo arXiv abs/2409.17146 published venue conference 2025",
        [],
        5,
    )

    assert seen == [("Molmo and PixMo", 3)]
    assert results[0]["url"] == "https://doi.org/10.1109/cvpr52734.2025.00018"
    assert results[1]["url"] == "https://arxiv.org/abs/2409.17146"


def test_scholarly_fallback_prefers_exact_bare_title_search(monkeypatch):
    seen = []

    def _fake_search(query, count, **kwargs):
        seen.append((query, count))
        return [{
            "title": "LLaVA-OneVision: Easy Visual Task Transfer",
            "url": "https://arxiv.org/abs/2408.03326",
            "snippet": "The matching paper.",
        }]

    monkeypatch.setattr(core, "searxng_search_api", _fake_search)
    monkeypatch.setattr(
        core,
        "_arxiv_title_results",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("arXiv API should not run after a strong bare-title match")
        ),
    )

    results = core._augment_scholarly_results(
        'Find the paper "LLaVA-OneVision: Easy Visual Task Transfer" Table 5',
        [{
            "title": "LLaVA: Large Language and Vision Assistant",
            "url": "https://example.test/older-llava",
            "snippet": "An older project.",
        }],
        5,
    )

    assert seen == [("LLaVA-OneVision: Easy Visual Task Transfer", 3)]
    assert results[0]["url"] == "https://arxiv.org/abs/2408.03326"


def test_openalex_title_fallback_returns_exact_arxiv_landing_page(monkeypatch):
    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {
                        "display_name": "LLaVA-OneVision: Easy Visual Task Transfer",
                        "doi": "https://doi.org/10.48550/arxiv.2408.03326",
                        "primary_location": {
                            "landing_page_url": "http://arxiv.org/abs/2408.03326",
                        },
                    },
                    {
                        "display_name": "LLaVA: Large Language and Vision Assistant",
                        "primary_location": {
                            "landing_page_url": "https://arxiv.org/abs/2304.08485",
                        },
                    },
                ],
            }

    monkeypatch.setattr(core.httpx, "get", lambda *args, **kwargs: _Response())

    assert core._openalex_title_results(
        "LLaVA-OneVision: Easy Visual Task Transfer",
        3,
    ) == [{
        "title": "LLaVA-OneVision: Easy Visual Task Transfer",
        "url": "https://arxiv.org/abs/2408.03326",
        "snippet": "Exact scholarly-title match from OpenAlex metadata.",
        "source": "openalex",
    }]


def test_openalex_title_result_exposes_formal_publication_metadata(monkeypatch):
    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [{
                    "display_name": (
                        "Molmo and PixMo: Open Weights and Open Data for "
                        "State-of-the-Art Vision-Language Models"
                    ),
                    "doi": "https://doi.org/10.1109/cvpr52734.2025.00018",
                    "publication_year": 2025,
                    "type": "conference-paper",
                    "primary_location": {
                        "landing_page_url": (
                            "https://doi.org/10.1109/cvpr52734.2025.00018"
                        ),
                        "raw_source_name": (
                            "2025 IEEE/CVF Conference on Computer Vision and "
                            "Pattern Recognition (CVPR)"
                        ),
                        "version": "publishedVersion",
                        "is_published": True,
                    },
                }],
            }

    monkeypatch.setattr(core.httpx, "get", lambda *args, **kwargs: _Response())

    result = core._openalex_title_results("Molmo and PixMo", 3)[0]

    assert result["url"] == "https://doi.org/10.1109/cvpr52734.2025.00018"
    assert result["snippet"] == (
        "Exact scholarly-title match from OpenAlex metadata. Formal publication: "
        "2025 IEEE/CVF Conference on Computer Vision and Pattern Recognition "
        "(CVPR), 2025; type: conference-paper; version: publishedVersion."
    )


def test_named_paper_search_returns_exact_metadata_before_generic_providers(
    monkeypatch,
    tmp_path,
):
    """Paper-source discovery must not burn its budget on generic providers."""

    provider_calls = []

    monkeypatch.setattr(core, "SEARCH_CACHE_DIR", tmp_path)
    monkeypatch.setattr(core, "search_cache_index", {})
    monkeypatch.setattr(core, "_record_query", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        core,
        "_get_search_settings",
        lambda: {"search_provider": "searxng", "search_fallback_chain": []},
    )
    monkeypatch.setattr(core, "_build_provider_chain", lambda provider: [provider])
    monkeypatch.setattr(
        core,
        "_call_provider",
        lambda *args, **kwargs: provider_calls.append((args, kwargs)) or [],
    )
    monkeypatch.setattr(
        core,
        "_arxiv_title_results",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("OpenAlex should resolve the title before arXiv fallback")
        ),
    )
    monkeypatch.setattr(
        core,
        "_openalex_title_results",
        lambda title, count: [{
            "title": "LLaVA-OneVision: Easy Visual Task Transfer",
            "url": "https://arxiv.org/abs/2408.03326",
            "snippet": "Exact scholarly-title match from OpenAlex metadata.",
            "source": "openalex",
        }] if title == "LLaVA-OneVision: Easy Visual Task Transfer" else [],
    )

    results = core.searxng_search_results(
        'LLaVA-OneVision paper "LLaVA-OneVision: Easy Visual Task Transfer" '
        "Table 3 Table 5",
        count=5,
    )

    assert provider_calls == []
    assert results[0]["url"] == "https://arxiv.org/abs/2408.03326"


def test_short_distinctive_paper_name_uses_exact_metadata_before_providers(
    monkeypatch,
    tmp_path,
):
    provider_calls = []
    monkeypatch.setattr(core, "SEARCH_CACHE_DIR", tmp_path)
    monkeypatch.setattr(core, "search_cache_index", {})
    monkeypatch.setattr(core, "_record_query", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        core,
        "_get_search_settings",
        lambda: {"search_provider": "searxng", "search_fallback_chain": []},
    )
    monkeypatch.setattr(core, "_build_provider_chain", lambda provider: [provider])
    monkeypatch.setattr(
        core,
        "_call_provider",
        lambda *args, **kwargs: provider_calls.append((args, kwargs)) or [],
    )
    monkeypatch.setattr(
        core,
        "_direct_scholarly_title_results",
        lambda title, count: [{
            "title": "Qwen2-VL: Enhancing Vision-Language Model Perception",
            "url": "https://arxiv.org/abs/2409.12191",
            "snippet": "Official paper.",
            "source": "openalex",
        }] if title == "Qwen2-VL" else [],
    )

    results = core.searxng_search_results(
        "Qwen2-VL paper multimodal benchmarks Table 2 Table 4",
        count=5,
    )

    assert provider_calls == []
    assert results[0]["url"] == "https://arxiv.org/abs/2409.12191"


def test_scholarly_fallback_skips_network_for_existing_strong_match(monkeypatch):
    def _unexpected_get(*args, **kwargs):
        raise AssertionError("arXiv should not be queried for an existing title match")

    monkeypatch.setattr(core, "searxng_search_api", _unexpected_get)
    monkeypatch.setattr(core.httpx, "get", _unexpected_get)
    exact = {
        "title": "LLaVA-OneVision: Easy Visual Task Transfer",
        "url": "https://arxiv.org/abs/2408.03326",
        "snippet": "Paper abstract.",
    }
    generic = {
        "title": "LLaVA: Large Language and Vision Assistant",
        "url": "https://example.test/older-llava",
        "snippet": "An older project.",
    }

    assert core._augment_scholarly_results(
        'Find the paper "LLaVA-OneVision: Easy Visual Task Transfer" Table 5',
        [generic, exact],
        5,
    ) == [exact, generic]


def test_service_ddg_redirect_ignores_lookalike_hosts():
    for host in ("duckduckgo.com.evil.com", "notduckduckgo.com"):
        url = f"https://{host}/l/?uddg=https%3A%2F%2Fexample.com"
        assert providers._resolve_ddg_redirect(url) == url

    assert providers._resolve_ddg_redirect(
        "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com"
    ) == "https://example.com"


def test_service_ddg_html_fallback_sends_safesearch(monkeypatch):
    seen = {}
    html = """
    <html><body>
      <div class="result">
        <a class="result__a" href="https://notduckduckgo.com/l/?uddg=https%3A%2F%2Fevil.example">
          Lookalike
        </a>
        <a class="result__snippet">Snippet</a>
      </div>
    </body></html>
    """

    class _Response:
        text = html

        def raise_for_status(self):
            return None

    def fake_get(url, **kwargs):
        seen["params"] = kwargs["params"]
        seen["timeout"] = kwargs["timeout"]
        return _Response()

    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"search_safesearch": "off"})
    monkeypatch.setitem(sys.modules, "ddgs", None)
    monkeypatch.setattr(providers.httpx, "get", fake_get)

    results = providers.duckduckgo_search("odysseus", count=1)

    assert seen["params"]["kp"] == "-2"
    assert seen["timeout"] <= 5
    assert results[0]["url"].startswith("https://notduckduckgo.com/")
def test_relevance_filter_falls_back_when_heuristic_rejects_every_result():
    rows = [{
        "title": "Bicycle buying guide",
        "snippet": "Road bikes and city bikes",
        "url": "https://example.test/bicycle-guide",
    }]

    assert core._filter_low_relevance_results("bicycles", rows) == rows


def test_relevance_filter_does_not_bypass_explicit_site_scope():
    rows = [{
        "title": "Bicycle buying guide",
        "snippet": "Road bikes and city bikes",
        "url": "https://example.test/bicycle-guide",
    }]

    assert core._filter_low_relevance_results("site:official.test bicycles", rows) == []
