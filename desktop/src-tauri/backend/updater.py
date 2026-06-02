#!/usr/bin/env python3
"""Odysseus desktop — weekly self-updater.

Pulls the latest Odysseus *app source* from a published GitHub **release** and
applies it in place, leaving the bundled Python runtime, Git-Bash, and the
user's data untouched. Only published releases are applied — deliberate and
safer than auto-pulling every commit; if the repo has no releases yet, nothing
auto-updates.

Modes:
  --check    : at most once a week, see if a newer release exists; if so
               download and STAGE it (no changes to the running app's files).
  --apply    : if an update is staged, back up the current code, overlay the new
               source, install new deps, smoke-test ``import app``, and roll
               back automatically on failure. Run before the backend starts.
  --rollback : restore the previous (last-good) version — recovery hatch if an
               applied release misbehaves but still imports.

State lives in ``<backend>/data/update/`` (writable, preserved across updates):
  last_check     - unix ts of the last --check
  installed_ref  - the release currently applied
  pending_ref    - ref of a staged update
  pending/       - staged source overlay
  last_good/     - previous version's source (for --rollback)
  last_good_ref  - the ref --rollback restores
"""

import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

REPO = "pewdiepie-archdaemon/odysseus"
APP_DIR = os.path.dirname(os.path.abspath(__file__))          # the installed backend/
STATE_DIR = os.path.join(APP_DIR, "data", "update")
WEEK = 7 * 24 * 3600
PIP_TIMEOUT = 300  # bound the worst-case launch delay if pip stalls on an update

# Only these are refreshed from upstream. Everything else — runtime/, git/,
# data/, chroma_server.py, updater.py — is ours and is left alone.
OVERLAY = [
    "app.py", "core", "src", "services", "routes", "static",
    "mcp_servers", "config", "scripts", "requirements.txt",
]


def _gh(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "odysseus-updater", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _latest_ref():
    """Latest *published release* tag, or '' if the repo has none / is offline.

    Releases-only is deliberate: the app never auto-pulls arbitrary commits,
    only versions the maintainer chose to publish.
    """
    try:
        rel = _gh(f"https://api.github.com/repos/{REPO}/releases/latest")
        return rel.get("tag_name") or ""
    except Exception:
        return ""


def _read(name):
    p = os.path.join(STATE_DIR, name)
    return open(p, encoding="utf-8").read().strip() if os.path.exists(p) else ""


def _write(name, val):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, name), "w", encoding="utf-8") as f:
        f.write(str(val))


def _copy(src, dst):
    if os.path.isdir(src):
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)


def _remove(path):
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
    elif os.path.exists(path):
        os.remove(path)


def do_check():
    last = _read("last_check")
    now = int(time.time())
    if last and (now - int(last)) < WEEK:
        return
    ref = _latest_ref()
    if not ref:
        return  # no releases yet, or offline — try again next week
    _write("last_check", now)

    installed = _read("installed_ref")
    if not installed:
        _write("installed_ref", ref)  # first run: baseline, don't re-download
        return
    if ref == installed or ref == _read("pending_ref"):
        return

    pending = os.path.join(STATE_DIR, "pending")
    tmp = tempfile.mkdtemp(prefix="ody_upd_")
    try:
        tarp = os.path.join(tmp, "src.tar.gz")
        req = urllib.request.Request(
            f"https://codeload.github.com/{REPO}/tar.gz/{ref}",
            headers={"User-Agent": "odysseus-updater"},
        )
        with urllib.request.urlopen(req, timeout=180) as r, open(tarp, "wb") as f:
            shutil.copyfileobj(r, f)
        with tarfile.open(tarp) as t:
            t.extractall(tmp, filter="data")  # filter='data' blocks path traversal
        top = next(
            os.path.join(tmp, d) for d in os.listdir(tmp)
            if os.path.isdir(os.path.join(tmp, d)) and d != "__MACOSX"
        )
        _remove(pending)
        os.makedirs(pending, exist_ok=True)
        for item in OVERLAY:
            s = os.path.join(top, item)
            if os.path.exists(s):
                _copy(s, os.path.join(pending, item))
        _write("pending_ref", ref)
    except Exception:
        _remove(pending)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def do_apply():
    pending = os.path.join(STATE_DIR, "pending")
    ref = _read("pending_ref")
    if not (os.path.isdir(pending) and ref):
        return

    prev_ref = _read("installed_ref")
    backup = os.path.join(STATE_DIR, "backup")
    _remove(backup)
    os.makedirs(backup, exist_ok=True)
    items = os.listdir(pending)
    applied = []
    try:
        for item in items:  # back up current
            cur = os.path.join(APP_DIR, item)
            if os.path.exists(cur):
                _copy(cur, os.path.join(backup, item))
        for item in items:  # overlay new
            _remove(os.path.join(APP_DIR, item))
            _copy(os.path.join(pending, item), os.path.join(APP_DIR, item))
            applied.append(item)

        py = os.path.join(APP_DIR, "runtime", "python.exe")
        subprocess.run(
            [py, "-m", "pip", "install", "-r", os.path.join(APP_DIR, "requirements.txt")],
            cwd=APP_DIR, timeout=PIP_TIMEOUT,
        )
        if subprocess.run([py, "-c", "import app"], cwd=APP_DIR, timeout=180).returncode != 0:
            raise RuntimeError("import app failed after update")
        _write("installed_ref", ref)
        # success: keep the previous version as the rollback point
        last_good = os.path.join(STATE_DIR, "last_good")
        _remove(last_good)
        os.rename(backup, last_good)
        _write("last_good_ref", prev_ref)
    except Exception:
        for item in applied:  # auto-roll-back this failed apply
            _remove(os.path.join(APP_DIR, item))
            bak = os.path.join(backup, item)
            if os.path.exists(bak):
                _copy(bak, os.path.join(APP_DIR, item))
        _remove(backup)
    finally:
        _remove(pending)
        _remove(os.path.join(STATE_DIR, "pending_ref"))


def do_rollback():
    """Restore the previous (last-good) version — manual recovery hatch."""
    last_good = os.path.join(STATE_DIR, "last_good")
    if not os.path.isdir(last_good):
        return
    for item in os.listdir(last_good):
        _remove(os.path.join(APP_DIR, item))
        _copy(os.path.join(last_good, item), os.path.join(APP_DIR, item))
    lgr = _read("last_good_ref")
    if lgr:
        _write("installed_ref", lgr)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--check"
    if mode == "--apply":
        do_apply()
    elif mode == "--rollback":
        do_rollback()
    else:
        do_check()
