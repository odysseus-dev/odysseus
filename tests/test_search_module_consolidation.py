"""Search consolidation regression tests.

``src.search`` is still a public import path for agent/deep-research code, but
core/provider behavior should come from the services.search implementation.
"""

import importlib


def test_src_search_core_aliases_services_core():
    src_core = importlib.import_module("src.search.core")
    service_core = importlib.import_module("services.search.core")

    assert src_core is service_core
    assert src_core.comprehensive_web_search is service_core.comprehensive_web_search
    assert src_core.invalidate_search_cache is service_core.invalidate_search_cache


def test_src_search_providers_aliases_services_providers():
    src_providers = importlib.import_module("src.search.providers")
    service_providers = importlib.import_module("services.search.providers")

    assert src_providers is service_providers
    assert src_providers._resolve_ddg_redirect is service_providers._resolve_ddg_redirect
    assert src_providers._safesearch_for is service_providers._safesearch_for


def test_src_search_package_exports_still_resolve():
    import src.search as search
    import services.search as service_search

    assert search.comprehensive_web_search is service_search.comprehensive_web_search
    assert search.searxng_search_results is service_search.searxng_search_results
    assert search.searxng_search_api is service_search.searxng_search_api
    assert search.PROVIDER_INFO is service_search.PROVIDER_INFO
