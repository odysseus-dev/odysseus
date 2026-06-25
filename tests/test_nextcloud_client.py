"""Unit tests for src.nextcloud_client.py — parsing, path safety, and SSRF.

No network: PROPFIND bodies are fixtures, and validate_nextcloud_url uses an
injected resolver so DNS is never touched. Behavioral-first: we drive the real
functions and assert their outputs, not source text.
"""

import pytest

from src.nextcloud_client import (
    NextcloudClient,
    _parse_propfind,
    _parse_single_propfind,
    _safe_relative_path,
    validate_nextcloud_url,
)


_PROPFIND_DOCUMENTS = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">
  <d:response>
    <d:href>/remote.php/dav/files/alice/Documents</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection/></d:resourcetype>
        <d:getlastmodified>Wed, 25 Jun 2025 10:00:00 GMT</d:getlastmodified>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/alice/Documents/report.txt</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype/>
        <d:getcontentlength>42</d:getcontentlength>
        <d:getcontenttype>text/plain</d:getcontenttype>
        <d:getlastmodified>Wed, 25 Jun 2025 09:00:00 GMT</d:getlastmodified>
        <d:displayname>report.txt</d:displayname>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/alice/Documents/Photos</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection/></d:resourcetype>
      </d:prop>
    </d:propstat>
  </d:response>
</d:multistatus>"""


def test_parse_propfind_lists_children_and_skips_self():
    entries = _parse_propfind(_PROPFIND_DOCUMENTS, "alice", "Documents")
    by_name = {e["name"]: e for e in entries}
    # The directory being listed ("Documents") is the self response and is excluded.
    assert "Documents" not in by_name
    assert set(by_name) == {"Photos", "report.txt"}
    assert by_name["Photos"]["is_dir"] is True
    report = by_name["report.txt"]
    assert report["is_dir"] is False
    assert report["size"] == 42
    assert report["content_type"] == "text/plain"
    # rel paths are relative to the user's DAV home.
    assert report["path"] == "Documents/report.txt"
    assert by_name["Photos"]["path"] == "Documents/Photos"
    # mtime parsed to ISO-8601.
    assert report["modified"] and report["modified"].startswith("2025-")


def test_parse_propfind_root_listing():
    # Server echoes the root as the bare user-home href with no trailing child path.
    root_xml = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/remote.php/dav/files/alice</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/alice/Documents</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat>
  </d:response>
</d:multistatus>"""
    entries = _parse_propfind(root_xml, "alice", "")
    assert [e["name"] for e in entries] == ["Documents"]
    assert entries[0]["path"] == "Documents"


def test_parse_single_propfind_returns_the_resource():
    entry = _parse_single_propfind(_PROPFIND_DOCUMENTS, "alice", "Documents")
    assert entry is not None
    assert entry["is_dir"] is True
    assert entry["path"] == "Documents"


def test_safe_relative_path_rejects_traversal():
    assert _safe_relative_path("a/b") == "a/b"
    assert _safe_relative_path("") == ""
    assert _safe_relative_path("/a/./b/") == "a/b"
    with pytest.raises(ValueError):
        _safe_relative_path("../etc/passwd")
    with pytest.raises(ValueError):
        _safe_relative_path("a/../../b")


def _pub(_host="x"):
    return ["203.0.113.10"]  # TEST-NET-3, a documented public example range


def test_validate_nextcloud_url_accepts_public_https(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_BLOCK_PRIVATE_NEXTCLOUD", raising=False)
    out = validate_nextcloud_url("https://cloud.example.com/", resolver=_pub)
    assert out == "https://cloud.example.com"


@pytest.mark.parametrize("bad", ["", "ftp://cloud.example.com", "cloud.example.com"])
def test_validate_nextcloud_url_rejects_bad_scheme_or_missing(monkeypatch, bad):
    with pytest.raises(ValueError):
        validate_nextcloud_url(bad, resolver=_pub)


def test_validate_nextcloud_url_rejects_credentials_in_url(monkeypatch):
    with pytest.raises(ValueError):
        validate_nextcloud_url("https://alice:secret@cloud.example.com", resolver=_pub)


def test_validate_nextcloud_url_always_blocks_link_local(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_BLOCK_PRIVATE_NEXTCLOUD", raising=False)
    # 169.254.169.254 is the cloud instance-metadata SSRF vector — always blocked.
    with pytest.raises(ValueError):
        validate_nextcloud_url("https://cloud.example.com", resolver=lambda _h: ["169.254.169.254"])


def test_validate_nextcloud_url_blocks_private_only_when_locked_down(monkeypatch):
    priv = lambda _h: ["192.168.1.5"]
    monkeypatch.delenv("ODYSSEUS_BLOCK_PRIVATE_NEXTCLOUD", raising=False)
    # Permissive by default (LAN/Tailscale Nextcloud is a normal self-hosted setup).
    assert validate_nextcloud_url("https://nc.lan", resolver=priv).startswith("https://nc.lan")
    # Multi-tenant lockdown via env.
    monkeypatch.setenv("ODYSSEUS_BLOCK_PRIVATE_NEXTCLOUD", "true")
    with pytest.raises(ValueError):
        validate_nextcloud_url("https://nc.lan", resolver=priv)


def test_client_constructor_validates_and_requires_credentials(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_BLOCK_PRIVATE_NEXTCLOUD", raising=False)
    c = NextcloudClient("https://cloud.example.com", "alice", "app-token", resolver=_pub)
    assert c.username == "alice"
    with pytest.raises(ValueError):
        NextcloudClient("https://cloud.example.com", "alice", "", resolver=_pub)
    with pytest.raises(ValueError):
        NextcloudClient("not a url", "alice", "pw", resolver=_pub)


def test_dav_url_quotes_path_segments(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_BLOCK_PRIVATE_NEXTCLOUD", raising=False)
    c = NextcloudClient("https://cloud.example.com", "alice", "tok", resolver=_pub)
    url = c._dav_url("Photos/vacation 2024/cat pic.jpg")
    # spaces and special chars are percent-encoded per segment; user in path.
    assert url == "https://cloud.example.com/remote.php/dav/files/alice/Photos/vacation%202024/cat%20pic.jpg"
