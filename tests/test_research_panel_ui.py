from pathlib import Path
from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parents[1]


def test_research_settings_use_custom_pickers_and_shared_provider_icons():
    panel = (ROOT / "static/js/research/panel.js").read_text(encoding="utf-8")
    style = app_css()
    settings = (ROOT / "static/js/settings.js").read_text(encoding="utf-8")
    icons = (ROOT / "static/js/searchProviderIcons.js").read_text(encoding="utf-8")

    for select_id in (
        "research-rounds",
        "research-category",
        "research-search-provider",
        "research-endpoint",
        "research-model",
    ):
        assert f"'{select_id}'" in panel

    assert "_setupResearchPickers(pane)" in panel
    assert "searchProviderLogo(value)" in panel
    assert "SEARCH_PROVIDER_LOGOS as _SEARCH_PROVIDER_LOGOS" in settings
    assert "provider === 'google' ? 'google_pse'" in icons
    first_picker_offset = style.split(".research-setting:has(#research-rounds),", 1)[1].split("}", 1)[0]
    assert ".research-setting:has(#research-category)" in first_picker_offset
    assert "top: 0;" in first_picker_offset


def test_completed_research_keeps_primary_and_utility_actions_visible_without_format_icon():
    panel = (ROOT / "static/js/research/panel.js").read_text(encoding="utf-8")

    assert "research-job-format-icon" not in panel
    assert 'data-action="report" title="Open visual report"' in panel
    assert 'data-action="chat" title="Open follow-up chat with this research as context"' in panel
    assert "{ action: 'copy', icon: _copyIcon, label: 'Copy report' }" in panel
    assert "{ action: 'dismiss', icon: _cancelIcon, label: 'Hide from list' }" in panel
    assert "{ action: 'delete', icon: _trashIcon, label: 'Delete from disk', danger: true }" in panel


def test_research_format_survives_live_and_reconnected_jobs():
    jobs = (ROOT / "static/js/research/jobs.js").read_text(encoding="utf-8")
    routes = (ROOT / "routes/research/research_routes.py").read_text(encoding="utf-8")
    handler = (ROOT / "src/research_handler.py").read_text(encoding="utf-8")

    assert "category: task.category || ''" in jobs
    assert "if (d.category) job.category = d.category" in jobs
    assert '"category": research_handler.get_category(session_id)' in routes
    assert "'category': research_handler.get_category(session_id)" in routes
    assert '_task_entry["category"] = researcher.category or category' in handler


def test_research_panel_has_no_model_only_visual_mode():
    panel = (ROOT / "static/js/research/panel.js").read_text(encoding="utf-8")
    jobs = (ROOT / "static/js/research/jobs.js").read_text(encoding="utf-8")
    routes = (ROOT / "routes/research/research_routes.py").read_text(encoding="utf-8")
    handler = (ROOT / "src/research_handler.py").read_text(encoding="utf-8")

    assert '<option value="-1">' not in panel
    assert '<option value="visual">' not in panel
    assert "categorySelect.value = 'visual'" not in panel
    assert 'Field(default=0, ge=0, le=20)' in routes
    assert 'explain_only = effective_max_rounds == -1' not in routes
    assert 'if max_rounds == -1:' not in handler
    assert "mode: 'research'" in jobs
