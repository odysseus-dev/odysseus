from pathlib import Path

from routes import search_routes
from routes.research_routes import _research_failure_summary


ROOT = Path(__file__).resolve().parents[1]
PANEL_JS = (ROOT / "static" / "js" / "research" / "panel.js").read_text(encoding="utf-8")
JOBS_JS = (ROOT / "static" / "js" / "research" / "jobs.js").read_text(encoding="utf-8")
STYLE_CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")


def test_google_pse_provider_status_requires_key_and_cx(monkeypatch):
    monkeypatch.setattr(search_routes, "_get_provider_key", lambda provider: "")
    monkeypatch.setattr(search_routes, "_get_search_settings", lambda: {"google_pse_cx": ""})

    status = search_routes._provider_status("google_pse")

    assert status["available"] is False
    assert "API key missing" in status["reason"]
    assert "Programmable Search Engine CX ID missing" in status["reason"]


def test_google_pse_provider_status_requires_cx_even_with_key(monkeypatch):
    monkeypatch.setattr(search_routes, "_get_provider_key", lambda provider: "present")
    monkeypatch.setattr(search_routes, "_get_search_settings", lambda: {"google_pse_cx": ""})

    status = search_routes._provider_status("google_pse")

    assert status["available"] is False
    assert "API key missing" not in status["reason"]
    assert "Programmable Search Engine CX ID missing" in status["reason"]
    assert "present" not in str(status.values())


def test_research_failure_summary_preserves_search_error_reason():
    data = {
        "sources": [],
        "raw_report": (
            "**Search unavailable** — Web search failed after 2 rounds. "
            "Error: no results from search provider(s): google_pse, duckduckgo\n\n"
            "Please check your search provider settings."
        ),
    }

    assert _research_failure_summary(data) == (
        "Search unavailable — Web search failed after 2 rounds. "
        "Error: no results from search provider(s): google_pse, duckduckgo"
    )


def test_research_panel_marks_unavailable_search_providers_and_uses_saved_failure_reason():
    assert "/api/search/providers" in PANEL_JS
    assert "let _searchProviderStatus = new Map();" in PANEL_JS
    assert "function _isSearchProviderUnavailable(provider)" in PANEL_JS
    assert "function _syncSearchProviderWarning()" in PANEL_JS
    assert "Using Default search instead." in PANEL_JS
    assert "`${meta.label} (setup needed)`" in PANEL_JS
    assert "research-search-provider-warning" in PANEL_JS
    assert "search_provider: _isSearchProviderUnavailable(searchProvider) ? undefined : searchProvider" in PANEL_JS
    assert "const failureMessage = failed ? (jobs.failureMessage(job)" in PANEL_JS
    assert "research-provider-warning" in STYLE_CSS

    assert "export function failureMessage(job)" in JOBS_JS
    assert "item.error_summary || null" in JOBS_JS
    assert "d.error_summary || _researchFailureMessage(d.result)" in JOBS_JS
