# Phase 1: Tooling Foundation & Baseline Scorecard - Pattern Map

**Mapped:** 2026-06-03
**Files analyzed:** 13 new/modified
**Analogs found:** 6 with in-repo analog / 13 total (7 are greenfield — no prior analog, by design)

> This is a tooling/infra phase: **no application behavior change**. The artifacts are config + CI + one generator script, not app modules. The only bespoke code is `scripts/scorecard.py`. House conventions to honor everywhere: `snake_case`, module docstring on every file, f-string logging, double-quoted strings, no autoformatter existed before this phase, project root on `sys.path` (absolute `src.`/`routes.`/`core.` imports).

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `requirements.in` | config (dep intent) | transform (→lock) | `requirements.txt` | exact (same content shape) |
| `requirements-optional.in` | config (dep intent) | transform (→lock) | `requirements-optional.txt` | exact |
| `requirements-dev.in` | config (dev-tool pins) | transform (→lock) | `requirements.txt` | role-match |
| `requirements.lock` | config (resolved, hashed) | batch (generated) | — | **no analog** (greenfield) |
| `requirements-optional.lock` | config (resolved, hashed) | batch (generated) | — | **no analog** (greenfield) |
| `requirements-dev.lock` | config (resolved, hashed) | batch (generated) | — | **no analog** (greenfield) |
| `pyproject.toml` (EXTEND) | config | request-response (tool reads) | existing `[tool.pytest.ini_options]` in same file | exact (extend in place) |
| `.bandit` / `[tool.bandit]` | config (suppressions) | request-response | existing `[tool.pytest.ini_options]` block | role-match |
| `.git-blame-ignore-revs` | config (VCS metadata) | batch | — | **no analog** (greenfield) |
| `.github/workflows/quality-gate.yml` | CI workflow | event-driven (CI trigger) | `.github/workflows/issue-description-check.yml`, `pr-description-check.yml` | role-match (Actions conventions only) |
| `scripts/scorecard.py` | utility (generator) | batch / transform | `scripts/claim_ownerless.py`, `scripts/index_documents.py`, `scripts/hf_download.py`; reads `app.py` route table + `app.py` allow-list | role-match (script conventions); exact (route-table read) |
| `.planning/scorecard/baseline.json` | artifact (data) | batch (generated) | — | **no analog** (greenfield) |
| `.planning/scorecard/SCORECARD.md` | artifact (rendered doc) | batch (generated) | — | **no analog** (greenfield) |

## Pattern Assignments

---

### `requirements.in` (config, dep intent → lock)

**Analog:** `requirements.txt` (read this session — it is already a direct-deps-only list with loose pins and rich `why` comments)

**Why it's the analog:** RESEARCH Pattern 1 (D-07) says `requirements.txt` today is *effectively* the `.in` content. Copy its direct-dep set verbatim, strip the test deps (move `pytest`/`pytest-asyncio` to `requirements-dev.in`), keep its comment style.

**Direct-dep set to replicate** (`requirements.txt:1-41` — minus the last two lines):
```
fastapi
uvicorn
python-multipart
python-dotenv
httpx
pydantic>=2.0
pydantic-settings>=2.0
SQLAlchemy
pypdf
beautifulsoup4
charset-normalizer
numpy
chromadb-client   # the ML-group conflict hotspot (Pitfall 10) — resolved inside the unified lock
fastembed
youtube-transcript-api
markdown
icalendar
python-dateutil
caldav
cryptography
bcrypt
mcp
pyotp
qrcode[pil]
croniter
```
**Drop from `.in`:** `pytest`, `pytest-asyncio` (lines 40-41) → these move to `requirements-dev.in`.

**Comment convention to replicate** (the *why-not-what* house style; `requirements.txt:13-18`):
```
# Vector store + local embeddings for RAG, semantic memory, and tool
# selection. Used on core agent paths, so installed by default — the app
# still degrades to keyword fallback if they're ever missing.
chromadb-client
fastembed
```
Keep the inline rationale blocks — they match CONVENTIONS.md "comments explain why, citing the scenario."

---

### `requirements-optional.in` (config, dep intent → lock)

**Analog:** `requirements-optional.txt` (read this session — preserves the MIT-core / AGPL-quarantine boundary)

**Why it's the analog:** D-08 keeps the AGPL PyMuPDF + `markitdown` quarantine separate. Copy the exact dep set and the AGPL-warning comment verbatim — that comment is load-bearing per `ACKNOWLEDGMENTS.md`.

**Optional-dep set to replicate** (`requirements-optional.txt:13,18,25,36`):
```
faster-whisper
duckduckgo-search
PyMuPDF                                  # AGPL-3.0 — keep quarantined (see ACKNOWLEDGMENTS.md)
markitdown[docx,pptx,xlsx,xls]==0.1.5    # only version-pinned dep; magika pulls onnxruntime
```

**AGPL comment to preserve verbatim** (`requirements-optional.txt:22-24`):
```
# NOTE: PyMuPDF is AGPL-3.0. Installing it brings AGPL obligations for a
# network-served app — see ACKNOWLEDGMENTS.md. The MIT core (PDF *text*
# extraction via pypdf) works without it; this only unlocks form-filling.
```

---

### `requirements-dev.in` (config, dev-tool pins)

**Analog:** `requirements.txt` (same flat-list-with-comments shape; role-match)

**Why:** No prior dev-deps file exists; mirror the `requirements.txt` list style. RESEARCH §"Standard Stack" gives the exact pinned set (Pitfall 9 — pin so a tool bump can't silently break CI).

**Content to replicate** (RESEARCH lines 116-125, versions verified this session):
```
ruff==0.15.15
mypy==2.1.0
bandit==1.9.4
pip-audit==2.10.0
pytest
pytest-asyncio
pytest-cov
```
Keep these **out of `requirements.lock`** (anti-pattern in RESEARCH line 221 — runtime install surface must stay unchanged for behavior-preservation).

---

### `requirements.lock` / `requirements-optional.lock` / `requirements-dev.lock` (config, generated — NO ANALOG)

**Analog:** none in repo. These are uv-compiled, `--generate-hashes` outputs.

**Generation pattern** (NOT hand-authored — RESEARCH lines 367-380; D-09 mandates compile inside `python:3.12-slim`, never the local 3.14 host):
```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c '
  set -euo pipefail
  pip install --no-cache-dir uv==0.11.18
  uv pip compile requirements.in --generate-hashes --python-version 3.12 --output-file requirements.lock
  uv pip compile requirements-optional.in --generate-hashes --python-version 3.12 --output-file requirements-optional.lock
'
```
**ML-group fallback (D-06, only if unified resolve fails):** split `requirements-ml.in` (chromadb-client + fastembed) → `requirements-ml.lock`, document the split in a comment. Capture stderr either way.
**Validation (SC#3):** second clean container `pip install --require-hashes --no-cache-dir -r requirements.lock` → exit 0.

---

### `pyproject.toml` (EXTEND — config)

**Analog:** the existing `[tool.pytest.ini_options]` block **in this same file** (read this session — the entire file is 3 lines).

**Why it's the analog:** CONVENTIONS.md + D (single-config-file convention): all tool config lives in one `pyproject.toml`. Do NOT scatter `ruff.toml`/`mypy.ini`/`.flake8`. **Append** the new `[tool.*]` tables below the existing block; do not rewrite it.

**Existing content to preserve verbatim** (`pyproject.toml:1-3`):
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

**Blocks to append** (exact shapes from RESEARCH lines 242-345; honors D-15/D-17/D-19):
```toml
[tool.ruff]
target-version = "py312"
line-length = 88
extend-exclude = ["static/", "data/", "logs/"]

[tool.ruff.lint]
select = ["E", "F", "I", "W", "UP", "B"]   # LOCKED families (D-15) — no ALL, no preview
ignore = ["E501"]                          # no hard-column convention (D-19, CONVENTIONS.md)

[tool.ruff.format]
quote-style = "double"                     # matches house style (CONVENTIONS.md)

[tool.mypy]
python_version = "3.12"
ignore_missing_imports = true
check_untyped_defs = false                 # lenient global, zero errors day 1 (D-17)
disallow_untyped_defs = false
warn_unused_ignores = true
exclude = ["^static/", "^tests/", "^scripts/"]

[[tool.mypy.overrides]]
module = ["mcp.*", "chromadb.*", "fastembed.*", "caldav.*", "icalendar.*",
          "croniter.*", "qrcode.*", "pyotp.*", "fitz.*", "markitdown.*",
          "duckduckgo_search.*", "faster_whisper.*", "youtube_transcript_api.*"]
ignore_missing_imports = true

[tool.coverage.run]
source = ["."]
# concurrency = ["greenlet", "thread"]     # add only if async coverage flakes (Pitfall 11)

[tool.bandit]
exclude_dirs = ["tests", "static", "data", "logs"]
# skips = [...]  # populate AFTER the audit pass, with documented rationale (THREAT_MODEL.md)
```

---

### `.bandit` / `[tool.bandit]` (config, suppressions)

**Analog:** the `[tool.pytest.ini_options]` block (role-match — same "config-as-code in pyproject" convention). bandit 1.9 reads `pyproject.toml`, so prefer the `[tool.bandit]` table above over a separate `.bandit` file (single-config-file convention).

**Key constraint:** `THREAT_MODEL.md` treats shell/file/email as *intentional admin features* — subprocess checks (B602–B607) will fire heavily. Run an audit pass, classify each, encode documented `skips` with rationale. Do NOT blanket-disable.

---

### `.git-blame-ignore-revs` (config, VCS metadata — NO ANALOG)

**Analog:** none. Greenfield.

**Pattern** (D-16; RESEARCH lines 288-292): a one-SHA-per-line file recording the bulk `ruff format` commit so `git blame` skips the mechanical reformat:
```bash
ruff format .                                  # one standalone commit
git rev-parse HEAD >> .git-blame-ignore-revs   # record its SHA after committing
```
Keep that commit purely mechanical whitespace (RESEARCH line 601 — do not bundle `--unsafe-fixes` into it, or blame attribution breaks).

---

### `.github/workflows/quality-gate.yml` (CI workflow, event-driven)

**Analog:** `.github/workflows/issue-description-check.yml` + `.github/workflows/pr-description-check.yml` (read this session). **MUST NOT modify these two** (D-05) — add alongside.

**Why they're the analog:** only existing Actions in the repo. They fix the repo's house conventions: `name: ci / <thing>` prefix, `runs-on: ubuntu-latest`, `actions/checkout@v4`, `permissions:` block scoped minimal, `on:` trigger style. New workflow copies those conventions, then adds parallel jobs + setup-uv (which the doc-check workflows don't need).

**Naming convention to replicate** (`issue-description-check.yml:1`, `pr-description-check.yml:1`):
```yaml
name: ci / quality gate          # mirrors "ci / issue description check", "ci / PR description check"
```

**Checkout + permissions convention to replicate** (`issue-description-check.yml:7-17`):
```yaml
permissions:
  contents: read                 # scope minimal — doc-check workflows set issues/PR write; this only reads
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
```

**New-to-this-file (not in analogs, from RESEARCH lines 412-490; D-02/D-03/D-04):**
```yaml
on:
  pull_request:
  push:
    branches: [main]             # D-03

# one parallel hard-blocking job per gate (D-02): lint, format, mypy, pytest, bandit, pip-audit, scorecard
# each job: actions/checkout@v4 → astral-sh/setup-uv@v8 (version "0.11.18", enable-cache: true)
#           → uv pip install --system <pinned tool> → run the gate
# install-from-lock jobs (mypy/pytest/scorecard): uv pip install --system --require-hashes -r requirements.lock (D-04)
```
> Note the analogs use `sparse-checkout: .github/scripts` and `actions/github-script@v7` — those are doc-check-specific; the quality gate does a full checkout instead. Borrow only the checkout/runner/name/permissions conventions, not the github-script step.

---

### `scripts/scorecard.py` (utility / generator — the only bespoke code)

**Analogs:**
- `scripts/claim_ownerless.py` (read) — best match for the **script skeleton**: shebang, module docstring with `Usage:`, `sys.path` insert, `def main()` + `if __name__ == "__main__"`, lazy in-function imports of `core.*`.
- `scripts/index_documents.py` (read) — match for **logging setup** (`logging.basicConfig` + module logger, f-string logs).
- `scripts/hf_download.py` (read) — match for **argparse** (for `--write` / `--check` modes, D-12).
- `app.py:81,516+` — exact source for **importing `app` and walking `app.routes`** (the endpoint-enumeration metric).
- `app.py:162-194` — exact source for the **auth public/bypass allow-list** the enumeration must encode.

**Why these are the analogs:** scorecard is a standalone re-runnable generator (D-12) that shells out to ruff/mypy/coverage/bandit/pip-audit and reads the FastAPI route table. The `scripts/*.py` trio fix the house script conventions; `app.py` is ground truth for the route table and the allow-list (RESEARCH lines 521-523: classify by the middleware allow-list, NOT by scanning for `Depends`).

**Script skeleton to replicate** (`scripts/claim_ownerless.py:1-14, 28, 97-98`):
```python
#!/usr/bin/env python3
"""<one-line purpose>.

<paragraph: what it measures, that it is re-runnable, JSON-first>

Usage:
    python scripts/scorecard.py --write     # regenerate baseline.json + SCORECARD.md
    python scripts/scorecard.py --check      # recompute + ratchet-compare; nonzero on regression
"""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    ...


if __name__ == "__main__":
    main()
```

**Logging convention to replicate** (`scripts/index_documents.py:22-30` + CONVENTIONS.md f-string house style):
```python
import logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)
# usage: logger.info(f"ruff findings: {total}")   # f-string, NOT %-style (CONVENTIONS.md)
```

**argparse convention to replicate** (`scripts/hf_download.py:145-148`):
```python
import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--write", action="store_true", help="Regenerate baseline.json + SCORECARD.md")
parser.add_argument("--check", action="store_true", help="Recompute + compare to baseline; nonzero exit on regression")
args = parser.parse_args()
```

**Route-table + allow-list read (load-bearing — copy this logic, not a grep).** Import `app` and walk `app.routes`, then classify each route against the *exact* allow-list defined inline at `app.py:162-194`:
```python
# app.py:162-187 — the public/bypass allow-list the scorecard MUST encode verbatim
AUTH_EXEMPT_EXACT = {
    "/api/auth/setup", "/api/auth/signup", "/api/auth/login", "/api/auth/logout",
    "/api/auth/status", "/api/auth/features", "/api/auth/settings",
    "/api/auth/integrations/presets", "/api/health", "/api/version", "/login",
}
AUTH_EXEMPT_PREFIXES = ["/static"]
AUTH_EXEMPT_PATTERNS = [re.compile(r"^/api/tasks/[^/]+/webhook/[^/]+/?$")]
# app.py:189-194 — classification predicate (authenticated = NOT exempt)
def _is_auth_exempt(path): ...
```
```python
# Walk the route table (app.py builds ~41 routers via include_router, app.py:516+):
from fastapi.routing import APIRoute
import app as app_module
for r in app_module.app.routes:
    if isinstance(r, APIRoute):
        authenticated = not _is_auth_exempt(r.path)   # mirror app.py:189-194 exactly
        record(path=r.path, methods=sorted(r.methods), authenticated=authenticated)
```
> Importing `app` also enables the **perf metric** (RESEARCH line 520): time `import app` cold + optionally one `TestClient` GET to `/api/health` (confirmed present at `app.py:775`) or `/api/version` (`app.py:770`).

**Shell-out, don't reimplement** (RESEARCH lines 235, 513-521): the metric tools each emit machine-readable output — use them:
```python
import subprocess
ruff = json.loads(subprocess.run(["ruff", "check", ".", "--output-format=json"],
                                 capture_output=True, text=True).stdout or "[]")
# coverage: pytest --cov=. --cov-report=json → read coverage.json files{} map
# bandit -f json ; pip-audit -r requirements.lock -f json
# typed_pct: AST-walk (ast.FunctionDef/AsyncFunctionDef) — tool-version-independent (Open Q3)
# line counts: pathlib walk of src/ routes/ core/ services/ mcp_servers/ companion/ app.py
```

---

### `.planning/scorecard/baseline.json` + `SCORECARD.md` (artifacts — NO ANALOG)

**Analog:** none. Generated by `scripts/scorecard.py --write`. JSON is source of truth (D-10); markdown is rendered from it. Schema shape is in RESEARCH lines 526-551 (finalize in planning). Lives outside the shipped app tree (D-13).

## Shared Patterns

### House code style (apply to `scripts/scorecard.py`)
**Source:** CONVENTIONS.md + observed in all three script analogs
- `snake_case` functions/vars; module docstring on line 1 (after shebang); f-string logging (`logger.info(f"...")`), never `%`-style; double-quoted strings predominate.
- `sys.path.insert(0, ...repo root...)` then absolute imports `from core.database import ...` / `import app` (project root on path, no aliases — `scripts/claim_ownerless.py:14, 53`).
- Lazy in-function imports of heavy/circular deps (CONVENTIONS.md established pattern; `scripts/claim_ownerless.py:53`).

### Single-config-file convention (apply to `pyproject.toml`, bandit)
**Source:** existing `pyproject.toml` + CONVENTIONS.md
**Apply to:** ruff, mypy, coverage, bandit config — all extend `pyproject.toml`. Never create `ruff.toml`/`mypy.ini`/`.flake8`/`setup.cfg`.

### Atomic / standalone commits (apply to the whole phase rollout)
**Source:** PROJECT.md "extend, don't replace" + D-18
**Apply to:** the config→`--fix`→`format` sequence and the lockfile commit — each isolated, revertable, full pytest green after every code-touching step (RESEARCH lines 587-599). Record the format SHA in `.git-blame-ignore-revs`.

### GitHub Actions house conventions (apply to `quality-gate.yml`)
**Source:** `.github/workflows/issue-description-check.yml`, `pr-description-check.yml`
**Apply to:** `name: ci / <thing>`, `runs-on: ubuntu-latest`, `actions/checkout@v4`, minimal `permissions:` block. Do not modify the two existing doc-check workflows (D-05).

### Auth-via-middleware allow-list (apply to the endpoint-enumeration metric)
**Source:** `app.py:162-194` (`AUTH_EXEMPT_EXACT` / `_PREFIXES` / `_PATTERNS` / `_is_auth_exempt`) and `core/middleware.py:20-45` (`require_admin` internal-tool bypass)
**Apply to:** `scripts/scorecard.py` endpoint enumeration — auth is centralized in middleware, NOT per-route `Depends`. Classification = "every `APIRoute` whose path is not in the allow-list is authenticated." This list is the input for Phase 2 COV-03 and Phase 5 SEC-01, so it must mirror `app.py` exactly.

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `requirements.lock` | config (resolved) | batch | uv-compiled artifact; first lockfile in repo (deps were unpinned `.txt`) |
| `requirements-optional.lock` | config (resolved) | batch | same — no prior lock |
| `requirements-dev.lock` | config (resolved) | batch | no prior dev-deps file at all |
| `.git-blame-ignore-revs` | config (VCS) | batch | no autoformatter existed before, so no bulk-reformat to ignore |
| `.planning/scorecard/baseline.json` | artifact (data) | batch | first scorecard; generated, not hand-written |
| `.planning/scorecard/SCORECARD.md` | artifact (doc) | batch | rendered from baseline.json; greenfield |

> For the greenfield files, the planner should follow the RESEARCH §"Recommended Repo Structure" + the per-metric schema (RESEARCH lines 513-560) and the uv/Docker recipe (lines 360-404), since there is no in-repo pattern to copy.

## Metadata

**Analog search scope:** `requirements*.txt`, `pyproject.toml`, `.github/workflows/`, `scripts/*.py`, `app.py` (route table + auth allow-list), `core/middleware.py`
**Files read this session:** `pyproject.toml`, `requirements.txt`, `requirements-optional.txt`, `.github/workflows/issue-description-check.yml`, `.github/workflows/pr-description-check.yml`, `scripts/claim_ownerless.py`, `scripts/index_documents.py`, `app.py:160-259`, `core/middleware.py:1-102` (+ targeted greps of `app.py`, `scripts/add_hwfit_models.py`, `scripts/hf_download.py`)
**Pattern extraction date:** 2026-06-03
