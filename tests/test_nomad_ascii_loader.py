from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_boot_loader_renders_a_generated_ascii_orb():
    html = _read("static/index.html")
    assert 'class="nomad-loader-shell"' in html
    assert "function renderOrb(t)" in html
    assert "var ramp=' .:-=+*#%@'" in html
    assert "NMD / ORBITAL BOOT" in html


def test_shared_wave_spinner_uses_monochrome_ascii_orbit_frames():
    js = _read("static/js/spinner.js")
    assert "'[ .oO@ ]'" in js
    assert "ai-spinner ai-spinner-ascii" in js
    assert "--orb-white" in js
    assert "--orb-line" in js


def test_loader_assets_are_cache_busted():
    html = _read("static/index.html")
    assert "orbital.css?v=20260721asciiorb" in html
    assert "spinner.js?v=20260721asciiorb" in html

