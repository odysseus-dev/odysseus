"""Regression guards for DOM attribute sinks in signature/settings UI."""

from pathlib import Path


_REPO = Path(__file__).resolve().parent.parent


def test_signature_picker_allows_only_raster_data_urls():
    src = (_REPO / "static" / "js" / "signature.js").read_text(encoding="utf-8")

    assert "function _safeSignatureDataUrl(raw)" in src
    assert r"^data:image\/png;base64," in src
    assert '<img src="${_esc(dataUrl)}"/>' in src
    assert 'dataUrl: s.data_url' not in src


def test_settings_2fa_setup_escapes_secret_and_qr_src():
    src = (_REPO / "static" / "js" / "settings.js").read_text(encoding="utf-8")

    assert "function safeRasterDataUrl(raw)" in src
    assert "const qrCode = safeRasterDataUrl(setup.qr_code);" in src
    assert '<img src="${esc(qrCode)}"' in src
    assert "${esc(setup.secret)}" in src
    assert 'src="${setup.qr_code}"' not in src
    assert ">${setup.secret}</div>" not in src
