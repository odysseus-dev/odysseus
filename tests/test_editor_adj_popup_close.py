from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADJ = (ROOT / "static/js/editor/fx/adj-popup.js").read_text(encoding="utf-8")


def test_adjustment_popup_head_uses_a_close_button():
    # The head control is an explicit close (x), not a minimise affordance that
    # did not behave like a minimise.
    assert '<button class="ge-adj-close" type="button" title="Close" aria-label="Close">&times;</button>' in ADJ


def test_head_no_longer_renders_a_minimise_button():
    assert 'class="ge-adj-min"' not in ADJ
    assert 'title="Minimise"' not in ADJ
    assert "&minus;" not in ADJ


def test_close_button_is_wired_to_close_adj_popup():
    assert "pop.querySelector('.ge-adj-close')?.addEventListener('click', closeAdjPopup);" in ADJ
    assert "pop.querySelector('.ge-adj-min')" not in ADJ
