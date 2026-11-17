from services.search import core
import pytest


def test_documentation_freshness_year_is_not_a_product_model_number():
    result = {'title': 'Enhanced Tracking Protection in Firefox',
              'url': 'https://support.mozilla.org/en-US/kb/enhanced-tracking-protection-firefox-desktop',
              'snippet': 'Firefox privacy settings and tracking protection documentation.'}
    assert core._result_has_query_overlap('Mozilla Firefox privacy documentation latest 2026', result)


def test_documentation_filter_keeps_real_product_number_requirements():
    result = {'title': 'WIKING Miro 3 manual', 'url': 'https://example.org/wiking-miro-3', 'snippet': 'WIKING Miro installation guide'}
    assert core._result_has_query_overlap('WIKING Miro 3 manual latest 2026', result)
    assert not core._result_has_query_overlap('WIKING Miro 4 manual latest 2026', result)
    assert not core._result_has_query_overlap('WIKING Miro 2026 manual', result)


@pytest.mark.parametrize('comprehensive', [False, True])
@pytest.mark.parametrize('transient_error', [False, True])
def test_empty_results_advance_provider_but_transport_errors_get_one_retry(monkeypatch, tmp_path, comprehensive, transient_error):
    calls = []
    def provider(name, query, count, time_filter=None):
        calls.append(name)
        if transient_error and name == 'primary' and calls.count(name) == 1:
            raise ConnectionError('temporary failure')
        return []
    monkeypatch.setattr(core, 'SEARCH_CACHE_DIR', tmp_path)
    monkeypatch.setattr(core, 'search_cache_index', {})
    monkeypatch.setattr(core, '_get_search_settings', lambda: {'search_provider': 'primary'})
    monkeypatch.setattr(core, '_build_provider_chain', lambda primary: ['primary', 'fallback'])
    monkeypatch.setattr(core, '_call_provider', provider)
    monkeypatch.setattr(core, '_record_query', lambda *a, **k: None)
    monkeypatch.setattr(core, 'cleanup_cache', lambda *a, **k: None)
    query = 'reference site:example.org'
    if comprehensive:
        output, sources = core.comprehensive_web_search(query, return_sources=True)
        assert sources == []
        if transient_error:
            assert 'primary:empty' in output
    else:
        assert core.searxng_search_results(query) == []
    assert calls == (['primary', 'primary', 'fallback'] if transient_error else ['primary', 'fallback'])


@pytest.mark.parametrize('url,accepted', [
    ('https://python.org/downloads/', True),
    ('https://docs.python.org/3/', True),
    ('https://python.org.evil.example/downloads/', False),
    ('https://evil.example/python.org', False),
    ('https://evil.example/?site=python.org', False),
    ('https://python.org@evil.example/', False),
    ('https://en.wikipedia.org/wiki/Microsoft_campus', False),
])
def test_provider_results_must_obey_site_scope(url, accepted):
    result = {'url': url, 'title': 'Python official release source python.org'}
    assert bool(core._filter_low_relevance_results(
        'latest Python release site:python.org', [result],
    )) is accepted


def test_site_scope_exclusions_and_unrestricted_queries():
    result = {'url': 'https://docs.python.org/3/'}
    assert core._result_matches_site_scope('Python documentation', result)
    assert not core._result_matches_site_scope('Python -site:python.org', result)
    assert core._result_matches_site_scope('site:example.org OR site:python.org', result)
    assert not core._result_matches_site_scope('site:python.org/downloads/', result)


@pytest.mark.parametrize('query', [
    'latest Python official site:python.org',
    'Python manual -site:example.org',
    'official manual filetype:pdf',
    'official "exact phrase" manual',
])
def test_relaxation_never_drops_explicit_query_constraints(query):
    assert core._empty_result_query_relaxations(query) == []


@pytest.mark.parametrize('comprehensive', [False, True])
def test_out_of_scope_provider_results_never_become_evidence(monkeypatch, tmp_path, comprehensive):
    queries = []
    def provider(name, query, count, time_filter=None):
        queries.append(query)
        return [{'title': 'Python release official python.org',
                 'url': 'https://evil.example/python.org', 'snippet': 'Python release'}]
    monkeypatch.setattr(core, 'SEARCH_CACHE_DIR', tmp_path)
    monkeypatch.setattr(core, 'search_cache_index', {})
    monkeypatch.setattr(core, '_get_search_settings', lambda: {'search_provider': 'searxng'})
    monkeypatch.setattr(core, '_build_provider_chain', lambda primary: ['searxng'])
    monkeypatch.setattr(core, '_call_provider', provider)
    monkeypatch.setattr(core, '_record_query', lambda *a, **k: None)
    monkeypatch.setattr(core, 'cleanup_cache', lambda *a, **k: None)
    def forbidden_fetch(*a, **k):
        pytest.fail('Out-of-scope results must not be fetched')
    monkeypatch.setattr(core, 'fetch_webpage_content', forbidden_fetch)
    query = 'latest Python release site:python.org'
    if comprehensive:
        _, sources = core.comprehensive_web_search(query, return_sources=True)
    else:
        sources = core.searxng_search_results(query)
    assert sources == []
    assert queries and set(queries) == {query}


def test_searxng_chain_always_keeps_distinct_private_engine_fallback(monkeypatch):
    import services.search.providers as providers

    monkeypatch.setattr(providers, 'provider_configured', lambda name: True)
    monkeypatch.setattr(core, '_get_search_settings', lambda: {
        'search_fallback_chain': ['duckduckgo'],
    })

    assert core._build_provider_chain('searxng') == [
        'searxng', 'searxng_yep', 'duckduckgo',
    ]


def test_empty_document_search_relaxes_scaffolding_then_entity():
    assert core._empty_result_query_relaxations(
        'Find WIKING Miro 3 English manual official source online'
    ) == [
        'WIKING Miro 3 manual',
        'WIKING Miro 3',
    ]


def test_manual_relevance_rejects_homonym_without_brand_and_model():
    query = 'WIKING Miro 3 English manual official source'
    assert not core._result_has_query_overlap(query, {
        'title': 'Miro Appliance User Manuals',
        'url': 'https://shop.mirohome.com/pages/miro-appliance-user-manuals',
        'snippet': 'Manuals for Miro humidifiers and air purifiers.',
    })
    assert core._result_has_query_overlap(query, {
        'title': 'WIKING Miro 3 Installation and User Manual',
        'url': 'https://www.hwam.com/manuals/wiking-miro-3.pdf',
        'snippet': 'Official English installation and user manual.',
    })


def test_search_uses_entity_relaxation_only_after_exact_queries_are_empty(
    monkeypatch, tmp_path,
):
    calls = []

    def provider(name, query, count, time_filter=None):
        calls.append((name, query))
        if name == 'searxng_yep' and query == 'WIKING Miro 3':
            return [{
                'title': 'WIKING Miro 3+ black with lower door - HWAM',
                'url': 'https://www.hwam.com/miro3-side-glass-lower-door',
                'snippet': 'Official WIKING Miro 3 product page.',
            }]
        return []

    monkeypatch.setattr(core, 'SEARCH_CACHE_DIR', tmp_path)
    monkeypatch.setattr(core, 'search_cache_index', {})
    monkeypatch.setattr(core, '_get_search_settings', lambda: {
        'search_provider': 'searxng',
        'search_fallback_chain': ['duckduckgo'],
    })
    monkeypatch.setattr(core, '_build_provider_chain', lambda primary: [
        'searxng', 'searxng_yep', 'duckduckgo',
    ])
    monkeypatch.setattr(core, '_call_provider', provider)
    monkeypatch.setattr(core, '_record_query', lambda *args, **kwargs: None)
    monkeypatch.setattr(core, 'cleanup_cache', lambda *args, **kwargs: None)

    results = core.searxng_search_results(
        'WIKING Miro 3 English manual official source', count=5,
    )

    assert results[0]['url'] == 'https://www.hwam.com/miro3-side-glass-lower-door'
    assert ('searxng_yep', 'WIKING Miro 3') in calls
    assert calls.index(('searxng_yep', 'WIKING Miro 3')) > calls.index(
        ('searxng_yep', 'WIKING Miro 3 manual')
    )
