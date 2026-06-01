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
        def read(self): return blob
    monkeypatch.setattr(reg.urllib.request, "urlopen", lambda *a, **k: _Resp())


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
    with pytest.raises(ValueError):
        reg.install(url="https://x/p.zip", plugin_id="p", sha256="deadbeef")


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
    reg.install(url="https://x/demo.zip", plugin_id="demo")
    assert os.path.isfile(os.path.join(pdir, "demo", "plugin.py"))


def test_install_requires_a_plugin_entry(pdir, monkeypatch):
    _mock_download(monkeypatch, _zip({"readme.txt": "no plugin here"}))
    with pytest.raises(ValueError):
        reg.install(url="https://x/demo.zip", plugin_id="demo")
    assert not os.path.isdir(os.path.join(pdir, "demo"))


def test_uninstall_removes_folder(pdir, monkeypatch):
    _mock_download(monkeypatch, _zip({"plugin.py": "PLUGIN={'name':'D'}"}))
    reg.install(url="https://x/demo.zip", plugin_id="demo")
    assert os.path.isdir(os.path.join(pdir, "demo"))
    reg.uninstall("demo")
    assert not os.path.isdir(os.path.join(pdir, "demo"))
