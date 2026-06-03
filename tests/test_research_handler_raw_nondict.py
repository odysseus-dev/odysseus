from src.research_handler import ResearchHandler


def test_extract_raw_findings_skips_non_dict_without_losing_all():
    # The body is wrapped in a try/except that returns [] on any error, so a
    # single non-dict finding made the AttributeError from f.get swallow EVERY
    # good finding (silent total data loss), not just the bad row.
    findings = [
        {"url": "https://a.com", "summary": "a real and useful finding here"},
        "junk-row",
        {"url": "https://b.com", "summary": "another genuine finding with detail"},
    ]
    out = ResearchHandler._extract_raw_findings(findings)
    assert [i["url"] for i in out] == ["https://a.com", "https://b.com"]
