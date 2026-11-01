"""Quick Edit must report image-generation failures in plain language.

Three cases are covered, each asserting both layers:
  1. image server unreachable / timed out  -> fixed "offline" sentence
  2. server up but generation switched off -> fixed "disabled" sentence (HTTP 503)
  3. anything else                         -> the server's own short reason
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EDITOR = (ROOT / "static" / "js" / "galleryEditor.js").read_text()
ROUTES = (ROOT / "routes" / "gallery" / "gallery_routes.py").read_text()


def test_offline_message_mapping():
    assert "Image server is offline right now, so I cannot generate. Try again later." in EDITOR
    assert "_GEN_OFFLINE_MSG" in EDITOR
    # network failure, a timeout, or an unreachable image server => offline
    assert "if (network || timedOut || unreachable || status === 0) return _GEN_OFFLINE_MSG;" in EDITOR
    assert "const unreachable = /unreachable|offline|timed out|timeout|connection|connect |refused|no route|network|failed to fetch/.test(low);" in EDITOR
    # client never hangs forever
    assert "_generationErrorMessage({ status: lastStatus" in EDITOR
    # server side: unreachable endpoints are a 502 (gateway), not a 503
    assert "Image server unreachable" in ROUTES
    assert 'raise HTTPException(504, f"Image server timed out' in ROUTES


def test_disabled_message_mapping():
    assert "Image generation is switched off on the server at the moment." in EDITOR
    assert "_GEN_DISABLED_MSG" in EDITOR
    assert "if (status === 503) return _GEN_DISABLED_MSG;" in EDITOR
    # server side: generation disabled by the admin -> 503
    assert '"Image generation is switched off on the server at the moment."' in ROUTES
    assert 'if "disabled" in low or "switched off" in low or "not enabled" in low:' in ROUTES


def test_other_errors_show_the_server_reason():
    assert "return reason ? `Could not generate: ${reason}`" in EDITOR
    assert 'raise HTTPException(502, msg)' in ROUTES


def test_quick_edit_shows_the_generation_message_verbatim():
    """The card prints the mapped sentence itself (no generic prefix) and resets."""
    assert "_isGenOfflineMsg" in EDITOR
    assert "_setAiCommandStatus(_isGenOfflineMsg(_genErr) ? _genErr : `Could not generate that edit: ${_genErr}`, 'error');" in EDITOR
    assert "_setAiCommandStatus(_isGenOfflineMsg(msg) ? msg : `Could not generate an image: ${msg}`, 'error');" in EDITOR
    # the generateImage op maps its own fetch/HTTP failures through the classifier
    assert "_generationErrorMessage({ network: true" in EDITOR
    assert "_generationErrorMessage({ status: res.status, detail: _gd })" in EDITOR


def test_sw_cache_is_bumped():
    import re

    sw = (ROOT / "static" / "sw.js").read_text()
    m = re.search(r"CACHE_NAME = '([^']+)'", sw)
    assert m, "CACHE_NAME not found"
    assert re.fullmatch(r"odysseus-v\d+-pr40-editor-precache", m.group(1)), m.group(1)
