"""Skill importer SSRF hardening: redirects must be re-validated per hop.

The importer follows redirects manually (`_get_checked`) and re-runs the SSRF
guard on every hop with ``block_private=True``, matching the hardened web-fetch
path in ``services/search/content.py:_get_public_url``. Previously it used
``httpx``'s ``follow_redirects=True`` with the lenient guard on the *initial*
URL only, so a ``3xx`` to an internal/metadata address was still connected to.

These tests are hermetic: every host is an IP literal, so ``check_outbound_url``
resolves them locally (``getaddrinfo`` on a numeric address does no DNS) and no
network access is required. The HTTP layer is faked so no real request is made.
"""
import pytest

from services.memory import skill_importer
from services.memory.skill_importer import (
    SkillImportError,
    _check_fetch_url,
    _fetch_bytes,
    _get_checked,
    parse_skill_source,
)

# Clearly-public, non-reserved IP literals for the initial (allowed) hop.
PUBLIC_A = "https://1.1.1.1/skill"
PUBLIC_B = "https://8.8.8.8/skill"
# Internal redirect targets that must be refused before connection.
LOOPBACK = "http://127.0.0.1/latest"
METADATA = "http://169.254.169.254/latest/meta-data/"


def _install_fake_client(monkeypatch, *, redirect_from, redirect_to):
    """Replace httpx.Client so `redirect_from` 302s to `redirect_to`, and any
    other URL returns 200. No real socket is opened."""

    class _Resp:
        def __init__(self, url, status, location):
            self.url = url
            self.status_code = status
            self.headers = {"location": location} if location else {}
            self.content = b"ok"
            self.text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return {}

    class _Client:
        def __init__(self, *args, **kwargs):
            # Safety invariant: the importer follows redirects by hand and
            # re-runs the SSRF guard per hop, so it MUST disable httpx's own
            # redirect following. Asserting ``follow_redirects is False`` here
            # (not merely accepting the kwarg) makes any regression to
            # ``follow_redirects=True`` fail these tests instead of passing
            # silently — httpx being faked would otherwise hide the change.
            assert kwargs.get("follow_redirects") is False, (
                "skill importer must construct httpx.Client with "
                "follow_redirects=False; got "
                f"{kwargs.get('follow_redirects')!r}"
            )

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers=None):
            if url == redirect_from:
                return _Resp(url, 302, redirect_to)
            return _Resp(url, 200, None)

    monkeypatch.setattr(skill_importer.httpx, "Client", _Client)


# --- Guard unit: block_private=True refuses internal, allows public ----------

@pytest.mark.parametrize("url", [LOOPBACK, METADATA, "http://10.0.0.5/", "http://[::1]/"])
def test_check_fetch_url_blocks_internal(url):
    with pytest.raises(SkillImportError):
        _check_fetch_url(url)


@pytest.mark.parametrize("url", [PUBLIC_A, PUBLIC_B])
def test_check_fetch_url_allows_public(url):
    # Should not raise for a public IP literal.
    _check_fetch_url(url)


# --- Redirect revalidation: the core regression ------------------------------

@pytest.mark.parametrize("internal", [LOOPBACK, METADATA])
def test_get_checked_blocks_redirect_to_internal(monkeypatch, internal):
    _install_fake_client(monkeypatch, redirect_from=PUBLIC_A, redirect_to=internal)
    with pytest.raises(SkillImportError, match="blocked"):
        _get_checked(PUBLIC_A)


@pytest.mark.parametrize("internal", [LOOPBACK, METADATA])
def test_fetch_bytes_blocks_redirect_to_internal(monkeypatch, internal):
    # Higher-level: the public fetch helpers inherit the per-hop guard.
    _install_fake_client(monkeypatch, redirect_from=PUBLIC_A, redirect_to=internal)
    with pytest.raises(SkillImportError, match="blocked"):
        _fetch_bytes(PUBLIC_A)


def test_skills_sh_entry_blocks_redirect_to_metadata(monkeypatch):
    # The skills.sh unwrap path (user-supplied host) must also revalidate hops.
    raw = "http://1.1.1.1/skills.sh"  # contains "skills.sh", not "github.com"
    _install_fake_client(monkeypatch, redirect_from=raw, redirect_to=METADATA)
    with pytest.raises(SkillImportError, match="blocked"):
        parse_skill_source(raw)


# --- Positive: a legitimate public->public redirect is still followed --------

def test_get_checked_follows_public_redirect(monkeypatch):
    _install_fake_client(monkeypatch, redirect_from=PUBLIC_A, redirect_to=PUBLIC_B)
    resp = _get_checked(PUBLIC_A)
    assert resp.status_code == 200
    assert str(resp.url) == PUBLIC_B
