from pathlib import Path
from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parents[1]


def test_agent_thread_chevron_uses_css_shape_in_live_and_history_renderers():
    live = (ROOT / "static/js/chat.js").read_text()
    history = (ROOT / "static/js/chatRenderer.js").read_text()
    css = app_css()

    for src in (live, history):
        assert 'class="agent-thread-chevron" aria-hidden="true"></span>' in src
        assert 'class="agent-thread-chevron">\\u25B6</span>' not in src

    assert ".agent-thread-chevron::before" in css
    assert "border-top: 4px solid transparent;" in css
    assert "border-bottom: 4px solid transparent;" in css
    assert "border-left: 6px solid currentColor;" in css
    assert ".agent-thread-node.open .agent-thread-chevron::before" in css
