"""CardDAV outbound URL hardening tests."""

import pytest

import routes.contacts_routes as contacts


def test_validate_carddav_url_blocks_metadata_targets(monkeypatch):
    monkeypatch.setattr(
        contacts,
        "check_outbound_url",
        lambda url, *, block_private=False: (False, "link-local address blocked"),
    )

    with pytest.raises(ValueError, match="link-local"):
        contacts._validate_carddav_url("http://169.254.169.254/latest/meta-data")


def test_validate_carddav_url_rejects_non_string(monkeypatch):
    monkeypatch.setattr(
        contacts,
        "check_outbound_url",
        lambda url, *, block_private=False: (False, "URL is required"),
    )

    with pytest.raises(ValueError, match="URL is required"):
        contacts._validate_carddav_url(12345)


def test_abs_url_pins_cross_origin_href_to_configured_carddav_origin(monkeypatch):
    monkeypatch.setattr(
        contacts,
        "_get_carddav_config",
        lambda: {"url": "https://dav.example.com/addressbooks/alice", "username": "", "password": ""},
    )
    monkeypatch.setattr(
        contacts,
        "check_outbound_url",
        lambda url, *, block_private=False: (True, "ok"),
    )

    assert (
        contacts._abs_url("http://169.254.169.254/latest/meta-data")
        == "https://dav.example.com/latest/meta-data"
    )


def test_vcard_url_validates_base_and_quotes_uid(monkeypatch):
    seen = []
    monkeypatch.setattr(
        contacts,
        "_get_carddav_config",
        lambda: {"url": "https://dav.example.com/addressbooks/alice/", "username": "", "password": ""},
    )

    def _safe(url, *, block_private=False):
        seen.append((url, block_private))
        return True, "ok"

    monkeypatch.setattr(contacts, "check_outbound_url", _safe)

    assert (
        contacts._vcard_url("uid/../../escape")
        == "https://dav.example.com/addressbooks/alice/uid%2F..%2F..%2Fescape.vcf"
    )
    assert seen == [("https://dav.example.com/addressbooks/alice", False)]
