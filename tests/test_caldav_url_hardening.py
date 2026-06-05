import asyncio
import ipaddress
import sys
import types
from pathlib import Path

import pytest

from src import caldav_sync


def test_validate_caldav_url_normalizes_safe_url(monkeypatch):
    monkeypatch.setattr(
        caldav_sync,
        "_resolve_caldav_host_ips",
        lambda host: [ipaddress.ip_address("93.184.216.34")],
    )
    assert (
        caldav_sync.validate_caldav_url(" https://calendar.example.com/dav/ ")
        == "https://calendar.example.com/dav"
    )


@pytest.mark.parametrize(
    "url, message",
    [
        ("ftp://calendar.example.com/dav", "must start with"),
        ("https://alice:secret@calendar.example.com/dav", "credentials"),
        ("https://calendar.example.com/dav#frag", "fragments"),
        ("http://localhost:5232/dav", "host is not allowed"),
        ("http://service.localhost/dav", "host is not allowed"),
        ("http://127.0.0.1:5232/dav", "host is not allowed"),
        ("http://[::1]:5232/dav", "host is not allowed"),
        ("http://169.254.169.254/latest", "host is not allowed"),
    ],
)
def test_validate_caldav_url_rejects_unsafe_urls(url, message):
    with pytest.raises(ValueError, match=message):
        caldav_sync.validate_caldav_url(url)


def test_validate_caldav_url_blocks_private_ips_unless_explicitly_allowed(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_ALLOW_PRIVATE_CALDAV", raising=False)
    with pytest.raises(ValueError, match="Private CalDAV IPs require"):
        caldav_sync.validate_caldav_url("http://10.0.0.5:5232/dav")

    monkeypatch.setenv("ODYSSEUS_ALLOW_PRIVATE_CALDAV", "1")
    assert caldav_sync.validate_caldav_url("http://10.0.0.5:5232/dav") == "http://10.0.0.5:5232/dav"


def test_validate_caldav_url_blocks_dns_to_private(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_ALLOW_PRIVATE_CALDAV", raising=False)
    monkeypatch.setattr(
        caldav_sync,
        "_resolve_caldav_host_ips",
        lambda host: [ipaddress.ip_address("10.0.0.5")],
    )

    with pytest.raises(ValueError, match="Private CalDAV IPs require"):
        caldav_sync.validate_caldav_url("https://calendar.example.com/dav")


def test_validate_caldav_url_blocks_dns_to_link_local_even_when_private_allowed(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_ALLOW_PRIVATE_CALDAV", "1")
    monkeypatch.setattr(
        caldav_sync,
        "_resolve_caldav_host_ips",
        lambda host: [ipaddress.ip_address("169.254.169.254")],
    )

    with pytest.raises(ValueError, match="host is not allowed"):
        caldav_sync.validate_caldav_url("https://calendar.example.com/dav")


def test_validate_caldav_url_fails_closed_when_hostname_does_not_resolve(monkeypatch):
    def _no_dns(host):
        raise OSError("no such host")

    monkeypatch.setattr(caldav_sync, "_resolve_caldav_host_ips", _no_dns)

    with pytest.raises(ValueError, match="host does not resolve"):
        caldav_sync.validate_caldav_url("https://calendar.example.com/dav")


def test_sync_caldav_decrypts_stored_password_and_validates_url(monkeypatch):
    monkeypatch.setattr(
        caldav_sync,
        "_resolve_caldav_host_ips",
        lambda host: [ipaddress.ip_address("93.184.216.34")],
    )
    prefs_mod = types.ModuleType("routes.prefs_routes")
    prefs_mod._load_for_user = lambda owner: {
        "caldav": {
            "url": " https://calendar.example.com/dav/ ",
            "username": owner,
            "password": "enc:stored",
        }
    }
    monkeypatch.setitem(sys.modules, "routes.prefs_routes", prefs_mod)

    secret_mod = types.ModuleType("src.secret_storage")
    secret_mod.decrypt = lambda value: "decrypted-password" if value == "enc:stored" else value
    monkeypatch.setitem(sys.modules, "src.secret_storage", secret_mod)

    captured = {}

    def fake_sync_blocking(owner, url, username, password):
        captured.update(
            {
                "owner": owner,
                "url": url,
                "username": username,
                "password": password,
            }
        )
        return {"calendars": 1, "events": 0, "deleted": 0, "errors": []}

    async def inline_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(caldav_sync, "_sync_blocking", fake_sync_blocking)
    monkeypatch.setattr(caldav_sync.asyncio, "to_thread", inline_to_thread)

    result = asyncio.run(caldav_sync.sync_caldav("alice"))

    assert result["calendars"] == 1
    assert captured == {
        "owner": "alice",
        "url": "https://calendar.example.com/dav",
        "username": "alice",
        "password": "decrypted-password",
    }


def test_calendar_routes_use_hardened_caldav_client_and_secret_storage():
    text = Path("routes/calendar_routes.py").read_text(encoding="utf-8")

    assert "validate_caldav_url(body.get(\"url\", \"\"))" in text
    assert "cfg[\"password\"] = encrypt(body[\"password\"])" in text
    assert "pw = decrypt(pw)" in text
    assert "follow_redirects=False, trust_env=False" in text
    assert "Redirects are not followed for CalDAV safety" in text
