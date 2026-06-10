from services.research.research_handler import ResearchHandler


def _format_report(findings):
    handler = object.__new__(ResearchHandler)
    return handler._format_research_report(
        "test query",
        "# Report\n\nBody",
        {"Rounds": 1, "Queries": 1, "URLs": len(findings)},
        1.0,
        findings=findings,
    )


def _format_report_with_analyzed_urls(findings, analyzed_urls):
    handler = object.__new__(ResearchHandler)
    return handler._format_research_report(
        "test query",
        "# Report\n\nBody",
        {"Rounds": 1, "Queries": 1, "URLs": len(analyzed_urls)},
        1.0,
        findings=findings,
        analyzed_urls=analyzed_urls,
    )


def test_research_report_lists_every_analyzed_url_once():
    findings = [
        {
            "url": "https://example.com/good",
            "title": "Good Source",
            "summary": "Detailed useful evidence about the query.",
        },
        {
            "url": "https://example.com/low-quality",
            "title": "Low Quality Page",
            "summary": "",
            "evidence": "",
        },
        {
            "url": "https://example.com/good",
            "title": "Good Source Duplicate",
            "summary": "Repeated extraction from the same URL.",
        },
    ]

    report = _format_report(findings)

    assert "### Analyzed URLs" in report
    analyzed_section = report.split("### Analyzed URLs", 1)[1].split("<details>", 1)[0]
    assert "1. [Good Source](https://example.com/good)" in analyzed_section
    assert "2. [Low Quality Page](https://example.com/low-quality)" in analyzed_section
    assert analyzed_section.count("https://example.com/good") == 1


def test_research_report_keeps_sources_section_curated():
    findings = [
        {
            "url": "https://example.com/good",
            "title": "Good Source",
            "summary": "Detailed useful evidence about the query.",
        },
        {
            "url": "https://example.com/low-quality",
            "title": "Low Quality Page",
            "summary": "",
            "evidence": "",
        },
    ]

    report = _format_report(findings)

    sources_section = report.split("### Sources", 1)[1].split("### Analyzed URLs", 1)[0]
    assert "[Good Source](https://example.com/good)" in sources_section
    assert "https://example.com/low-quality" not in sources_section


def test_research_report_uses_full_analyzed_url_set_not_just_findings():
    findings = [
        {
            "url": "https://example.com/finding",
            "title": "Finding Source",
            "summary": "Detailed useful evidence about the query.",
        },
    ]
    analyzed_urls = [
        {"url": "https://example.com/finding", "title": "Finding Source"},
        {"url": "https://example.com/fetched-no-finding", "title": "Fetched No Finding"},
        {"url": "https://example.com/finding", "title": "Duplicate"},
    ]

    report = _format_report_with_analyzed_urls(findings, analyzed_urls)

    sources_section = report.split("### Sources", 1)[1].split("### Analyzed URLs", 1)[0]
    analyzed_section = report.split("### Analyzed URLs", 1)[1].split("<details>", 1)[0]
    assert "https://example.com/fetched-no-finding" not in sources_section
    assert "1. [Finding Source](https://example.com/finding)" in analyzed_section
    assert "2. [Fetched No Finding](https://example.com/fetched-no-finding)" in analyzed_section
    assert analyzed_section.count("https://example.com/finding") == 1
