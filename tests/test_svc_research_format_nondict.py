from services.research.research_handler import ResearchHandler


def test_format_research_report_skips_non_dict_findings():
    # Both the sources loop and the raw-findings loop call f.get on each entry;
    # a malformed finding (None / a bare string) made the report builder crash.
    rh = ResearchHandler.__new__(ResearchHandler)
    findings = [
        {"url": "https://a.com", "title": "A", "summary": "genuine detail about the topic"},
        "junk-row",
        None,
        {"url": "https://b.com", "summary": "more real detail"},
    ]
    out = rh._format_research_report("q", "the full report body", {}, 1.0, findings=findings)
    assert "https://a.com" in out
    assert "https://b.com" in out
    assert "junk-row" not in out
