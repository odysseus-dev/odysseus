from pathlib import Path


CSS = Path("static/style.css").read_text(encoding="utf-8")
INIT_JS = Path("static/js/init.js").read_text(encoding="utf-8")


def test_both_minimized_window_docks_clear_the_composer():
    assert "#minimized-dock {" in CSS
    assert "bottom: var(--composer-clearance, 12px);" in CSS
    assert "#modal-dock {" in CSS
    assert "bottom:var(--composer-clearance, 0px);" in CSS


def test_composer_clearance_tracks_input_and_attachment_height():
    assert "const chatBar = document.querySelector('.chat-input-bar');" in INIT_JS
    assert "const attachStrip = document.getElementById('attach-strip');" in INIT_JS
    assert "root.style.setProperty('--composer-clearance', clearance + 'px');" in INIT_JS
