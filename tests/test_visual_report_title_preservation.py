"""Selecting a report title must not erase a later section or its TOC entry."""
import pytest
from bs4 import BeautifulSoup

from src.visual_report import _extract_report_title, generate_visual_report


@pytest.mark.parametrize('report,title', [
    ('## Introduction\n\nOpening text.\n\n## The Real Findings\n\nEvidence.', 'The Real Findings'),
    ('Opening context.\n\n## Measured Outcomes\n\nEvidence.', 'Measured Outcomes'),
    ('## Summary\n\nOpening text.\n\n# Detailed Analysis\n\nEvidence.', 'Detailed Analysis'),
    ('## Introduction\n\n## Findings\n\n## Final Assessment\n\nEvidence.', 'Final Assessment'),
])
def test_mid_document_title_keeps_original_markdown(report, title):
    assert _extract_report_title(report, 'Fallback') == (title, report)


@pytest.mark.parametrize('heading', ['# A Real Report', '## A Real Report'])
def test_top_title_still_removed_to_avoid_duplicate_hero(heading):
    title, body = _extract_report_title('\n \n' + heading + '\n\n## Details\n\nEvidence.', 'Fallback')
    assert title == 'A Real Report'
    assert body == '## Details\n\nEvidence.'


@pytest.mark.parametrize('report', ['', 'A paragraph without headings.', '## Introduction\n\nOpening.\n\n## Summary\n\nClosing.'])
def test_no_specific_heading_preserves_fallback_and_body(report):
    assert _extract_report_title(report, 'Fallback') == ('Fallback', report)


def test_rendered_mid_document_section_remains_linked_in_toc():
    report = '## Introduction\n\nSynthetic introduction.\n\n## The Real Findings\n\nSynthetic evidence.\n\n## Discussion\n\nSynthetic discussion.'
    soup = BeautifulSoup(generate_visual_report('Synthetic query', report, sources=[], stats={}), 'html.parser')
    assert soup.h1.get_text(strip=True) == 'The Real Findings'
    headings = soup.select('h2')
    assert any(h.get_text(strip=True) == 'The Real Findings' for h in headings)
    links = soup.select('.toc-sidebar nav a')
    findings = [link for link in links if link.get_text(strip=True) == 'The Real Findings']
    assert len(findings) == 1
    target = soup.find(id=findings[0]['href'].removeprefix('#'))
    assert target is not None and target.name == 'h2'
    assert target.get_text(strip=True) == 'The Real Findings'
    assert 'Synthetic introduction.' in soup.get_text()
    assert 'Synthetic evidence.' in soup.get_text()
    assert 'Synthetic discussion.' in soup.get_text()
