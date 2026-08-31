"""The service worker has to control the app at /, not just /static/.

static/sw.js precaches "/" and handles navigations to "/", but it is served from
/static/, and a worker's scope defaults to its own directory. Controlling / needs
both halves: the response must allow the wider scope and the registration must
ask for it. Either one alone leaves a worker that installs, fills its cache, and
controls nothing.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_registration_asks_for_root_scope():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert "navigator.serviceWorker.register('/static/sw.js',{scope:'/'})" in html


def test_only_the_worker_response_widens_its_scope(tmp_path):
    """Serving the header on every static file would let any of them claim /."""
    probe = textwrap.dedent(
        """
        import json
        from starlette.testclient import TestClient
        import app

        with TestClient(app.app) as client:
            out = {
                p: client.get(p).headers.get("Service-Worker-Allowed")
                for p in ("/static/sw.js", "/static/app.js")
            }
        print("RESULT=" + json.dumps(out, sort_keys=True))
        """
    )
    env = {**os.environ, "ODYSSEUS_DATA_DIR": str(tmp_path), "AUTH_ENABLED": "false"}
    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=180, check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    line = next((l for l in result.stdout.splitlines() if l.startswith("RESULT=")), None)
    assert line is not None, result.stdout[-2000:]

    headers = json.loads(line.removeprefix("RESULT="))
    assert headers["/static/sw.js"] == "/"
    assert headers["/static/app.js"] is None
