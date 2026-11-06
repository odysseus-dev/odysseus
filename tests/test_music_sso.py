import pytest

from src import invites, music_sso


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ODYSSEUS_DATA_DIR", str(tmp_path))


def test_roundtrip_returns_user_and_invite():
    c = music_sso.issue("lichi", "inv-1")
    assert music_sso.read(c) == {"u": "lichi", "i": "inv-1"}


def test_tampered_payload_rejected():
    c = music_sso.issue("lichi", "inv-1")
    payload, sig = c.rsplit(".", 1)
    forged = music_sso._b64(b'{"u":"iltius","i":"inv-1","e":9999999999}')
    assert music_sso.read(forged + "." + sig) is None
    assert music_sso.read(payload + "." + sig[:-2] + "AA") is None


def test_expired_rejected(monkeypatch):
    c = music_sso.issue("lichi", "inv-1")
    monkeypatch.setattr(music_sso.time, "time", lambda: 10**10)
    assert music_sso.read(c) is None


def test_garbage_rejected():
    assert music_sso.read(None) is None
    assert music_sso.read("") is None
    assert music_sso.read("nodot") is None
    assert music_sso.read("a.b") is None


def test_is_live_false_for_missing_invite():
    assert invites.is_live("no-existe") is False


def test_forward_headers_drops_sso_cookie_without_leading_space():
    from starlette.requests import Request
    from routes.music_sso_routes import _forward_headers

    cookie = f"{music_sso.COOKIE_NAME}=abc.def; X-ND-Client-Unique-Id=123"
    scope = {"type": "http", "method": "GET", "path": "/music/app/",
             "headers": [(b"cookie", cookie.encode())]}
    out = _forward_headers(Request(scope), "caro")
    assert out["cookie"] == "X-ND-Client-Unique-Id=123"
    assert out["X-Authenticated-User"] == "caro"
