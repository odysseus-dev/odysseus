from src.research_handler import ResearchHandler


def test_extract_sources_skips_non_dict_findings():
    # findings come from the DeepResearcher result list / cached JSON; a
    # malformed entry (None or a bare string) made the old loop call .get on a
    # non-dict and crash, dropping every real source in the set.
    findings = [
        {"url": "https://a.com", "title": "A", "summary": "real analysis of the topic"},
        "junk-row",
        None,
        {"url": "https://b.com", "summary": "more genuine detail here"},
    ]
    out = ResearchHandler._extract_sources(findings)
    assert [s["url"] for s in out] == ["https://a.com", "https://b.com"]
