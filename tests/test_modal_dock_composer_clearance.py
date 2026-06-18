from pathlib import Path

from tests.helpers.css_loader import load_runtime_css_text

ROOT = Path(__file__).resolve().parents[1]
CSS = load_runtime_css_text()
INIT_JS = (ROOT / "static/js/init.js").read_text(encoding="utf-8")


def test_both_minimized_window_docks_clear_the_composer():
    assert "#minimized-dock {" in CSS
    assert "bottom: var(--composer-clearance, 12px);" in CSS
    assert "#modal-dock {" in CSS
    assert "bottom:var(--composer-clearance, 0px);" in CSS


def test_composer_clearance_tracks_input_and_attachment_height():
    assert "const chatBar = document.querySelector('.chat-input-bar');" in INIT_JS
    assert "const attachStrip = document.getElementById('attach-strip');" in INIT_JS
    assert "root.style.setProperty('--composer-clearance', clearance + 'px');" in INIT_JS
