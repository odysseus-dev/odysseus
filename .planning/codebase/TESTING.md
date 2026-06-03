# Testing Patterns

**Analysis Date:** 2026-06-03

## Test Framework

**Runner:**
- `pytest` with `pytest-asyncio` (both pinned in `requirements.txt`).
- Config: `pyproject.toml`
  ```toml
  [tool.pytest.ini_options]
  testpaths = ["tests"]
  asyncio_mode = "auto"
  ```
  `asyncio_mode = "auto"` means `async def test_*` functions run as coroutines automatically — no `@pytest.mark.asyncio` decorator needed.

**Assertion Library:**
- Plain `assert` (pytest rewrites assertions). No separate assertion library.

**Frontend tests:**
- A small number of JS tests run under Node directly: `tests/markdown_codefence_placeholder_regression.mjs` (run with `node`) and `tests/bombadil-spec.ts`. Many JS behaviors are instead tested *from Python* via `subprocess` calls to `node` (see "Test Types" below).

**Run Commands:**
```bash
python -m pytest                       # Run the full Python suite (~356 test files)
python -m pytest tests/test_app.py     # Run one file
python -m pytest -k needs_auto_name    # Run by name pattern
python -m pytest -x -q                 # Stop on first failure, quiet
node tests/markdown_codefence_placeholder_regression.mjs   # Run a standalone JS regression test
python -m py_compile app.py routes/*.py src/*.py           # Lightweight compile check (per CONTRIBUTING.md)
node --check static/js/<file>.js                           # Syntax-check a changed JS file
```

There is **no CI test workflow**. The only GitHub Actions workflows (`.github/workflows/issue-description-check.yml`, `pr-description-check.yml`) validate issue/PR description completeness, not code. Tests are expected to be run locally; mention what you ran in the PR (CONTRIBUTING.md).

## Test File Organization

**Location:**
- Flat directory: all tests live directly in `tests/` (no nested packages, no co-location with source). ~356 `test_*.py` files plus the `.mjs`/`.ts` JS specs.

**Naming:**
- `test_<subject>.py`, where the subject names the module/feature under test (`test_agent_loop.py`, `test_atomic_io.py`, `test_caldav_url_hardening.py`).
- JS-behavior tests driven from Python use a `_js` suffix: `test_calendar_utils_dates_js.py`, `test_compare_js.py`, `test_censor_pref_js.py` (~22 such files).
- Regression-focused files often name the bug/scenario (`test_calendar_rrule_until_utc.py`, `test_auth_session_revocation.py`).

**Structure:**
```
tests/
├── conftest.py                          # sys.path + optional-dep stubbing
├── test_<feature>.py                    # function-style or class-grouped tests
├── test_<feature>_js.py                 # Python tests that shell out to node
├── markdown_*_regression.mjs            # standalone Node test (run via `node`)
└── bombadil-spec.ts                     # Antithesis bombadil spec
```

## Test Structure

**Two coexisting styles** — match what neighboring tests use:

Function style with `@pytest.mark.parametrize` (preferred for pure predicates):
```python
import pytest
from routes.chat_helpers import needs_auto_name

@pytest.mark.parametrize("name,expected", [
    ("deepseek-v4-flash 14:05:33", True),
    ("custom title", False),
    ("", True),
])
def test_needs_auto_name(name, expected):
    assert needs_auto_name(name) == expected, f"needs_auto_name({name!r}) should be {expected}"
```

Class-grouped style (for related cases around one function), with a leading docstring and section banners:
```python
class TestDetectAdminIntent:
    """Test admin-intent detection from the last user message."""

    def _msgs(self, text: str):
        """Helper: wrap text in a minimal messages list."""
        return [{"role": "user", "content": text}]

    def test_add_endpoint(self):
        assert _detect_admin_intent(self._msgs("add a new endpoint")) is True
```

**Patterns:**
- One behavior per test; descriptive `test_<behavior>` names.
- Predicates asserted with `is True` / `is False`, not truthiness.
- Assertion messages frequently included for parametrized cases (`f"...{value!r}..."`).
- Comments mark regressions: `# Regression: only the first text block was returned`.
- No global setup/teardown fixtures beyond `conftest.py`; per-test state uses pytest's `tmp_path` (used in ~66 files).

## Mocking

**Framework:** `unittest.mock` (`MagicMock`, `patch`) plus pytest's `monkeypatch` fixture (the dominant approach — used in ~137 files). `MagicMock`/`mock.` appears in ~57 files.

**Two distinct mocking jobs:**

1. **Import-time dependency stubbing** — heavy/optional deps are replaced with `MagicMock` *before* importing the module under test, so unit tests don't pull in the DB/app stack. `tests/conftest.py` does this globally for deps that aren't installed:
   ```python
   for mod_name in ["sqlalchemy", "sqlalchemy.orm", "bcrypt", "pyotp",
                    "httpx", "fastapi", "pydantic", ...]:
       if mod_name not in sys.modules and not _has_module(mod_name):
           sys.modules[mod_name] = MagicMock()
   ```
   Individual tests do the same locally when they need to force a stub regardless of install state (`tests/test_agent_loop.py`):
   ```python
   import sys
   from unittest.mock import MagicMock
   for mod in ['sqlalchemy', 'src.database', 'src.agent_tools', 'core.models', 'core.database']:
       if mod not in sys.modules:
           sys.modules[mod] = MagicMock()
   from src.agent_loop import _detect_admin_intent  # import AFTER stubbing
   ```

2. **Behavioral mocking** — `monkeypatch.setattr(...)` to swap functions/attributes for a single test, and `MagicMock()` for collaborators.

**Direct-by-path import to avoid package side effects:** for pure units, tests load a single source file by path with `importlib.util` so importing the package (`core/__init__.py`) doesn't drag in the database. See `tests/test_atomic_io.py`:
```python
ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("_atomic_io_under_test", ROOT / "core" / "atomic_io.py")
atomic_io = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(atomic_io)
```

**What to Mock:**
- SQLAlchemy / the database layer, `bcrypt`, `pyotp`, network clients (`httpx`), and other heavy or optional deps.
- External services and any I/O that would require a running app.

**What NOT to Mock:**
- The function under test, and pure logic helpers.
- FastAPI/Starlette/Pydantic are deliberately NOT stubbed when actually installed — route tests import their subpackages (note the comment in `conftest.py`). Don't blanket-mock them.

## Fixtures and Factories

**Test data:**
- Inline literals / small helper methods (e.g. `_msgs(text)`) build minimal structures — messages are plain dicts `{"role": "user", "content": text}`, Anthropic responses are dicts with a `"content"` list of typed blocks.
- pytest's built-in `tmp_path` fixture is the standard for filesystem tests; tests write/read real temp files and assert no `.tmp.*` siblings remain.

**Location:**
- No dedicated fixtures/factories directory. Shared cross-test setup lives only in `tests/conftest.py`. Small helpers are defined locally inside the test module or test class.

## Coverage

**Requirements:** None enforced. No coverage gate, no `--cov` config, no CI thresholds. Aim for behavior/regression coverage of the specific code you touch.

**View Coverage:**
```bash
pip install pytest-cov     # not a declared dependency; install ad hoc
python -m pytest --cov=src --cov=routes --cov=core --cov-report=term-missing
```

## Test Types

**Unit Tests:**
- The vast majority. Import a single function (often a private `_helper`) with heavy deps stubbed, exercise pure logic. Examples: `test_anthropic_response_parse.py`, `test_chat_helpers.py`, `test_agent_loop.py`.

**Integration / route Tests:**
- A small number use FastAPI's `TestClient` (~4 files) to drive real route wiring. Most route logic is instead tested by calling the route's helper functions directly with mocked dependencies.

**Cross-language (Python → JS) Tests:**
- ~22 `test_*_js.py` files validate frontend JS from Python by shelling out to `node` and importing the real `static/js/*.js` module. These are skipped when `node` is absent:
  ```python
  ROOT = Path(__file__).resolve().parents[1]
  pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")

  def _node_eval(source: str):
      result = subprocess.run(["node", "--input-type=module", "-e", source],
                              cwd=ROOT, check=True, capture_output=True, text=True)
  ```

**Standalone JS Tests:**
- `tests/markdown_codefence_placeholder_regression.mjs` loads `static/js/markdown.js` into a `vm` sandbox (rewriting imports), runs `node:assert/strict`, and prints `ok`. Run directly with `node`.

**E2E:** No browser/E2E framework (no Playwright/Cypress/Selenium). `tests/bombadil-spec.ts` targets Antithesis (`@antithesishq/bombadil`) for autonomous testing.

## Common Patterns

**Async Testing:**
```python
# asyncio_mode = "auto" — no decorator needed
async def test_handler_returns_error_payload():
    result = await some_async_handler(request)
    assert result["error"]
```

**Error Testing:**
```python
import pytest

def test_raises_on_missing_session():
    with pytest.raises(SessionNotFoundError):
        manager.get("does-not-exist")

# Or, matching the "degrade gracefully" route convention — assert the
# structured error payload instead of an exception:
def test_search_handles_provider_failure(monkeypatch):
    monkeypatch.setattr("services.search.core._call_provider", _boom)
    out = run_search("query", "badprovider")
    assert out["results"] == [] and "error" in out
```

**Conditional skips:** use `pytest.mark.skipif` (module-level `pytestmark` or per-test) for tests that need optional binaries/deps like `node`.

---

*Testing analysis: 2026-06-03*
