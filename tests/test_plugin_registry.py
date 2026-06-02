"""Tests for the plugin registry / installer.

Focus on the security-critical bits: zip-slip and symlink rejection during
extract, sha256 verification, id validation — plus a happy-path install/uninstall
with the network mocked (no real download).
"""
import hashlib
import io
import os
import zipfile

import pytest

from src import plugin_registry as reg


def _zip(files):
    """Build an in-memory zip from {arcname: bytes|str}."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content if isinstance(content, (bytes, str)) else "")
    return buf.getvalue()


def _mock_download(monkeypatch, blob):
    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, *a): return blob   # real responses accept a size arg
    monkeypatch.setattr(reg, "_urlopen", lambda *a, **k: _Resp())


@pytest.fixture
def pdir(tmp_path, monkeypatch):
    d = tmp_path / "plugins"; d.mkdir()
    monkeypatch.setenv("ODYSSEUS_PLUGINS_DIR", str(d))
    monkeypatch.setattr(reg, "get_manager", lambda: None)   # no live manager in unit tests
    return str(d)


# -- security -----------------------------------------------------------------

def test_safe_extract_blocks_zip_slip(tmp_path):
    dest = tmp_path / "dest"; dest.mkdir()
    blob = _zip({"../escape.py": "x = 1"})
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        with pytest.raises(ValueError):
            reg._safe_extract(zf, str(dest))
    assert not (tmp_path / "escape.py").exists()


def test_safe_extract_blocks_absolute_path(tmp_path):
    dest = tmp_path / "dest"; dest.mkdir()
    # zip with an absolute-ish member
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("/etc/evil", "x")
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
        with pytest.raises(ValueError):
            reg._safe_extract(zf, str(dest))


def test_install_rejects_non_https(pdir):
    with pytest.raises(ValueError):
        reg.install(url="http://example.com/p.zip", plugin_id="p")


def test_install_rejects_bad_id(pdir, monkeypatch):
    _mock_download(monkeypatch, _zip({"plugin.py": "PLUGIN={}"}))
    with pytest.raises(ValueError):
        reg.install(url="https://x/p.zip", plugin_id="../evil")


def test_sha256_mismatch_raises(pdir, monkeypatch):
    blob = _zip({"plugin.py": "PLUGIN={'name':'P'}"})
    _mock_download(monkeypatch, blob)
    with pytest.raises(ValueError):   # valid 64-hex format, wrong value → mismatch
        reg.install(url="https://x/p.zip", plugin_id="p", sha256="0" * 64)


def test_install_requires_sha256(pdir, monkeypatch):
    blob = _zip({"plugin.py": "PLUGIN={'name':'P'}"})
    _mock_download(monkeypatch, blob)
    for bad in (None, "", "abc", "g" * 64):   # missing / malformed digests are rejected
        with pytest.raises(ValueError):
            reg.install(url="https://x/p.zip", plugin_id="p", sha256=bad)


def test_safe_extract_rejects_oversized(tmp_path, monkeypatch):
    dest = tmp_path / "dest"; dest.mkdir()
    monkeypatch.setattr(reg, "MAX_TOTAL_UNCOMPRESSED", 1000)
    blob = _zip({"big.bin": b"x" * 5000})
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        with pytest.raises(ValueError):
            reg._safe_extract(zf, str(dest))


# -- happy path ---------------------------------------------------------------

def test_install_extracts_and_verifies(pdir, monkeypatch):
    blob = _zip({"plugin.py": "PLUGIN = {'name': 'Demo'}\n",
                 "data/notes.txt": "hi"})
    _mock_download(monkeypatch, blob)
    digest = hashlib.sha256(blob).hexdigest()
    reg.install(url="https://x/demo.zip", plugin_id="demo", sha256=digest)
    assert os.path.isfile(os.path.join(pdir, "demo", "plugin.py"))
    assert os.path.isfile(os.path.join(pdir, "demo", "data", "notes.txt"))


def test_install_flattens_single_root_dir(pdir, monkeypatch):
    # a zip that wraps everything under one top folder (common GitHub archive shape)
    blob = _zip({"demo-1.0/plugin.py": "PLUGIN={'name':'D'}"})
    _mock_download(monkeypatch, blob)
    reg.install(url="https://x/demo.zip", plugin_id="demo", sha256=hashlib.sha256(blob).hexdigest())
    assert os.path.isfile(os.path.join(pdir, "demo", "plugin.py"))


def test_install_requires_a_plugin_entry(pdir, monkeypatch):
    blob = _zip({"readme.txt": "no plugin here"})
    _mock_download(monkeypatch, blob)
    with pytest.raises(ValueError):   # valid digest → reaches (and fails) the no-plugin-entry check
        reg.install(url="https://x/demo.zip", plugin_id="demo", sha256=hashlib.sha256(blob).hexdigest())
    assert not os.path.isdir(os.path.join(pdir, "demo"))


def test_allowed_url_hardening():
    assert reg._allowed_url("https://example.com/x.zip")
    assert reg._allowed_url("http://127.0.0.1:7000/r.json")
    assert reg._allowed_url("http://localhost/r.json")
    assert not reg._allowed_url("http://evil.com/x")              # http to non-loopback
    assert not reg._allowed_url("http://127.0.0.1:1@evil.com/x")  # userinfo trick → real host evil.com
    assert not reg._allowed_url("https://127.0.0.1@evil.com/x")   # userinfo
    assert not reg._allowed_url("ftp://127.0.0.1/x")
    assert not reg._allowed_url("file:///etc/passwd")
    assert not reg._allowed_url(" https://example.com")           # leading whitespace
    assert not reg._allowed_url("https://exa\tmple.com")          # embedded whitespace


def test_uninstall_removes_folder(pdir, monkeypatch):
    blob = _zip({"plugin.py": "PLUGIN={'name':'D'}"})
    _mock_download(monkeypatch, blob)
    reg.install(url="https://x/demo.zip", plugin_id="demo", sha256=hashlib.sha256(blob).hexdigest())
    assert os.path.isdir(os.path.join(pdir, "demo"))
    reg.uninstall("demo")
    assert not os.path.isdir(os.path.join(pdir, "demo"))


def test_install_update_reimports_new_code(tmp_path, monkeypatch):
    """Updating an installed plugin must run the NEW code, not the stale module."""
    import src.plugin_system as ps
    from fastapi import FastAPI
    pdir = tmp_path / "plugins"; pdir.mkdir()
    data = tmp_path / "data"; data.mkdir()
    monkeypatch.setenv("ODYSSEUS_PLUGINS_DIR", str(pdir))
    monkeypatch.setenv("ODYSSEUS_DATA_DIR", str(data))
    mgr = ps.PluginManager(app=FastAPI(), directory=str(pdir))
    monkeypatch.setattr(reg, "get_manager", lambda: mgr)

    def _src(v):
        return ("PLUGIN = {'name': 'U', 'version': '%s'}\n" % v +
                "def setup(ctx):\n"
                "    from fastapi import APIRouter\n"
                "    r = APIRouter()\n"
                "    @r.get('/api/plugins/u/ver')\n"
                "    async def ver(): return {'v': '%s'}\n" % v +
                "    ctx.add_router(r)\n")

    b1 = _zip({"plugin.py": _src("1")})
    _mock_download(monkeypatch, b1)
    reg.install(url="https://x/u.zip", plugin_id="u", sha256=hashlib.sha256(b1).hexdigest())
    assert mgr.records["u"].status == "loaded"
    assert mgr.records["u"].module.PLUGIN["version"] == "1"

    b2 = _zip({"plugin.py": _src("2")})
    _mock_download(monkeypatch, b2)
    reg.install(url="https://x/u.zip", plugin_id="u", sha256=hashlib.sha256(b2).hexdigest())
    assert mgr.records["u"].module.PLUGIN["version"] == "2"   # NEW code re-imported, not stale
