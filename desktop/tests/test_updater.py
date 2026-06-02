"""Tests for the self-updater's overlay / backup / rollback logic — the
riskiest part of the desktop wrapper.

Runs with plain Python (``python tests/test_updater.py``) or under pytest.
No network: the GitHub download path (do_check) is not exercised here; these
cover do_apply (success + auto-rollback) and do_rollback, with subprocess
(pip + the ``import app`` smoke test) stubbed.
"""

import os
import shutil
import sys
import tempfile
import types

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src-tauri", "backend")
sys.path.insert(0, BACKEND)
import updater  # noqa: E402


def _fresh_app(tmp, content):
    with open(os.path.join(tmp, "app.py"), "w", encoding="utf-8") as f:
        f.write(content)
    os.makedirs(os.path.join(tmp, "runtime"), exist_ok=True)


def _configure(tmp):
    updater.APP_DIR = tmp
    updater.STATE_DIR = os.path.join(tmp, "data", "update")
    os.makedirs(updater.STATE_DIR, exist_ok=True)


def _stage(state, content, ref):
    pend = os.path.join(state, "pending")
    os.makedirs(pend, exist_ok=True)
    with open(os.path.join(pend, "app.py"), "w", encoding="utf-8") as f:
        f.write(content)
    with open(os.path.join(pend, "requirements.txt"), "w", encoding="utf-8") as f:
        f.write("")
    with open(os.path.join(state, "pending_ref"), "w", encoding="utf-8") as f:
        f.write(ref)


class _StubSubprocess:
    """Make pip 'succeed' and the ``import app`` smoke test pass or fail."""

    def __init__(self, smoke_ok):
        self.smoke_ok = smoke_ok
        self._orig = updater.subprocess.run

    def __enter__(self):
        def fake(args, **kw):
            rc = 0 if (self.smoke_ok or "-c" not in args) else 1
            return types.SimpleNamespace(returncode=rc)

        updater.subprocess.run = fake
        return self

    def __exit__(self, *exc):
        updater.subprocess.run = self._orig


def test_apply_rolls_back_when_smoke_fails():
    tmp = tempfile.mkdtemp()
    try:
        _configure(tmp)
        _fresh_app(tmp, "VERSION=1")
        updater._write("installed_ref", "v1")
        _stage(updater.STATE_DIR, "VERSION=2-broken", "v2")
        with _StubSubprocess(smoke_ok=False):
            updater.do_apply()
        # original code restored, version unchanged, staging cleared
        assert open(os.path.join(tmp, "app.py")).read() == "VERSION=1"
        assert updater._read("installed_ref") == "v1"
        assert not os.path.isdir(os.path.join(updater.STATE_DIR, "pending"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_apply_succeeds_and_retains_last_good():
    tmp = tempfile.mkdtemp()
    try:
        _configure(tmp)
        _fresh_app(tmp, "VERSION=1")
        updater._write("installed_ref", "v1")
        _stage(updater.STATE_DIR, "VERSION=2", "v2")
        with _StubSubprocess(smoke_ok=True):
            updater.do_apply()
        assert open(os.path.join(tmp, "app.py")).read() == "VERSION=2"
        assert updater._read("installed_ref") == "v2"
        # previous version kept so --rollback can recover it
        assert os.path.isfile(os.path.join(updater.STATE_DIR, "last_good", "app.py"))
        assert updater._read("last_good_ref") == "v1"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_rollback_restores_previous_version():
    tmp = tempfile.mkdtemp()
    try:
        _configure(tmp)
        _fresh_app(tmp, "VERSION=1")
        updater._write("installed_ref", "v1")
        _stage(updater.STATE_DIR, "VERSION=2", "v2")
        with _StubSubprocess(smoke_ok=True):
            updater.do_apply()  # now at v2, last_good == v1
        updater.do_rollback()
        assert open(os.path.join(tmp, "app.py")).read() == "VERSION=1"
        assert updater._read("installed_ref") == "v1"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    passed = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print("ok:", _name)
            passed += 1
    print(f"all {passed} updater tests passed")
