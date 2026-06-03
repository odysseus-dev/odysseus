# Testing patterns

Practical patterns for writing tests that are fast and pass reliably in this
project. These supplement the short "Running Checks" notes in
[CONTRIBUTING.md](../CONTRIBUTING.md) — read that first.

## Environment

- Use Python 3.11. A virtualenv keeps things predictable:

  ```bash
  python3 -m venv venv
  ./venv/bin/python -m pip install -r requirements.txt
  mkdir -p data            # some code paths expect the runtime data dir to exist
  ./venv/bin/python -m pytest -q
  ```

- `pyproject.toml` sets `asyncio_mode = "auto"`, so an `async def test_*` runs
  directly — no `@pytest.mark.asyncio` needed.

## Route / handler tests: call the handler directly

Driving FastAPI routes through Starlette's `TestClient` can hang in this
environment (its middleware app plus threadpool), which shows up as a test that
never returns. Prefer calling the async route function directly:

```python
import asyncio
from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

# 1. point the route module's SessionLocal at a throwaway DB
import core.database as cdb
from routes import session_routes as sroutes

def _temp_db(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path/'t.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    monkeypatch.setattr(sroutes, "SessionLocal", sessionmaker(bind=engine))

# 2. find the endpoint and await it with a minimal request
def _endpoint(router, path, method="GET"):
    for r in router.routes:
        if r.path == path and method in r.methods:
            return r.endpoint
    raise AssertionError(f"{method} {path} not found")

async def test_example(tmp_path, monkeypatch):
    _temp_db(tmp_path, monkeypatch)
    router = sroutes.setup_session_routes(...)            # build the router
    req = SimpleNamespace(state=SimpleNamespace(current_user="tester"))
    result = await _endpoint(router, "/api/...")(req, ...)
    assert ...
```

Notes:

- Don't set `DATABASE_URL` at import time to swap the DB — if `core.database`
  is already imported it's ignored, and the test can fall back to the dev DB and
  hang on lock contention. Patch the route module's `SessionLocal` instead.
- If a route module defines its `router` at module scope, reset it before
  re-running setup in a second test, or duplicate routes accumulate and the
  second request 404s. Modules that build the router *inside* the setup function
  avoid this.

See `tests/test_document_close_clears_active_route.py` for a working example.

## Frontend logic: extract it and run it under node

Most `static/js` modules import browser globals (`document`, `window`, other UI
modules) and can't be imported in a test. Move the pure decision/logic into a
small dependency-free module and drive it with `node`:

```python
import json, shutil, subprocess, textwrap
import pytest

def _run_node(script):
    if not shutil.which("node"):
        pytest.skip("node not on PATH")
    res = subprocess.run(["node", "--input-type=module", "-e", script],
                         capture_output=True, text=True, timeout=15)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])

def test_pure_helper():
    out = _run_node(textwrap.dedent("""
        const { fn } = await import('./static/js/my_helper.js');
        console.log(JSON.stringify(fn(...)));
    """))
    assert out == ...
```

- Use `node --check static/js/<file>.js` as a syntax gate.
- A source-only string match (asserting the fix text is present) can pass while
  the code throws at runtime — execute the logic wherever you can.

See `tests/test_compare_js.py` for the established pattern.

## Prove the test actually catches the bug

For a regression fix, confirm red → green: with the fix reverted the new test
should fail, and with it applied it should pass. A test that passes both ways
isn't guarding anything.
