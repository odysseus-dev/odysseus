"""Regression tests for research handler import-path compatibility."""


def test_services_research_handler_is_canonical_src_class():
    from services.research import ResearchHandler as package_handler
    from services.research.research_handler import ResearchHandler as module_handler
    from src.research_handler import ResearchHandler as canonical_handler

    assert module_handler is canonical_handler
    assert package_handler is canonical_handler
    assert hasattr(package_handler, "get_report_html")
    assert hasattr(package_handler, "get_raw_findings")


def test_services_research_handler_exports_storage_helpers():
    from services.research import research_handler as service_module
    from src import research_handler as canonical_module

    assert service_module.RESEARCH_DATA_DIR is canonical_module.RESEARCH_DATA_DIR
    assert service_module._research_json_path is canonical_module._research_json_path
    assert service_module._format_probe_failure is canonical_module._format_probe_failure
