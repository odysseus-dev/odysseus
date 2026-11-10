"""Static checks for the beginner `/setup` page and its first-run redirect."""

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parent.parent
_HTML = (_REPO / "static" / "setup.html").read_text(encoding="utf-8")
_JS = (_REPO / "static" / "js" / "setup.js").read_text(encoding="utf-8")
_CSS = (_REPO / "static" / "css" / "setup.css").read_text(encoding="utf-8")
_INIT = (_REPO / "static" / "js" / "init.js").read_text(encoding="utf-8")

_INLINE_HANDLER = re.compile(r"""\son[a-z]+\s*=""", re.IGNORECASE)


def _section(html: str, view_id: str) -> str:
    start = html.index(f'id="{view_id}"')
    end = html.index("</section>", start)
    return html[start:end]


def test_setup_files_exist():
    assert (_REPO / "static" / "setup.html").is_file()
    assert (_REPO / "static" / "js" / "setup.js").is_file()
    assert (_REPO / "static" / "css" / "setup.css").is_file()


def test_welcome_view_has_exactly_one_primary_button():
    welcome = _section(_HTML, "view-welcome")

    assert welcome.count('class="btn btn-primary"') == 1
    assert "Install and Set Up" in welcome
    assert "Set up manually" in welcome


def test_live_region_and_focus_targets_are_present():
    assert 'aria-live="polite"' in _HTML
    assert 'aria-labelledby="progress-label"' in _HTML
    assert _HTML.count('tabindex="-1"') == _HTML.count("<h1")
    assert "setAttribute('aria-current', 'step')" in _JS


def test_page_has_no_inline_handlers_or_inline_scripts():
    assert not _INLINE_HANDLER.search(_HTML)
    assert "<script>" not in _HTML
    assert all('src="' in tag for tag in re.findall(r"<script\b[^>]*>", _HTML))


def test_page_loads_its_own_stylesheet_and_script():
    assert 'href="/static/css/setup.css"' in _HTML
    assert 'src="/static/js/setup.js"' in _HTML


def test_setup_js_uses_text_content_not_inner_html():
    assert "innerHTML" not in _JS


def test_setup_js_polls_only_while_running_and_leaves_on_401():
    assert "setTimeout(poll, 1000)" in _JS
    assert "location.assign('/login')" in _JS


def test_problem_view_heading_can_be_replaced():
    assert "title || 'Something went wrong'" in _JS and "err.title" in _JS


def test_page_keeps_checking_after_a_request_got_no_answer():
    assert _JS.count("if (!err.status) schedulePoll();") == 2


def test_ready_view_shows_the_setup_note():
    assert 'id="ready-note"' in _section(_HTML, "view-ready")
    assert "ready-note" in _JS and "textContent" in _JS


def test_copy_question_has_its_own_wording():
    assert "'add_model_copy'" in _JS and "'Yes, add the copy'" in _JS and "'No download'" in _JS


def test_reduced_motion_is_respected():
    assert "prefers-reduced-motion: reduce" in _CSS


def test_init_redirects_to_setup_only_when_needed_and_not_skipped():
    redirect = _INIT[_INIT.index("First-run redirect"):]

    assert "/api/setup/status" in redirect
    assert "data.needed === true" in redirect
    assert "location.replace('/setup')" in redirect
    assert "sessionStorage.getItem('setupSkipped') === '1'" in redirect
