# Stack Research — Odysseus Modernization Tooling

**Domain:** Behavior-preserving refactor + security audit of a large Python 3.12 / FastAPI monolith
**Researched:** 2026-06-03
**Confidence:** HIGH — all versions verified against PyPI/npm at research time; all tool behavior verified against official docs via Context7 + WebFetch

---

## Context and Constraints

This is NOT a greenfield stack recommendation. Odysseus already runs on Python 3.12, FastAPI/Uvicorn, SQLAlchemy/SQLite, pip, pytest + pytest-asyncio, and a vanilla-JS SPA with no build step. The tooling stack below adds **quality gates and CI enforcement** on top of that existing foundation without changing it.

Key constraints that drive choices:
- Stay on pip (not Poetry / PDM). The project has an existing `requirements.txt` workflow.
- No frontend framework migration — vanilla JS ES modules, served directly without bundling.
- The 355-test pytest suite is the behavior contract; tooling must coexist with `asyncio_mode = "auto"` and the existing `conftest.py` stub-loading pattern.
- No CI exists today (`.github/workflows/` only has PR/issue description checks). The goal is to establish the first real CI pipeline.

---

## 1. Type Checking — mypy with Gradual Strictness Ramp

### Recommendation: mypy 2.1.0

**Why mypy over pyright/ty:** mypy is the canonical, most widely deployed Python type checker; it has the deepest per-module override system specifically designed for gradual adoption in large existing codebases. Pyright/ty are faster but their per-module ignore granularity is coarser, and ty (Astral's new checker) is still pre-1.0 as of June 2026. For a brownfield codebase with many partially-typed modules, mypy's `[[tool.mypy.overrides]]` mechanism is the right tool.

**Gradual adoption strategy for Odysseus:**

The correct approach for a large untyped codebase is the **inverted strictness model**: start with `ignore_errors = true` globally (so mypy runs cleanly from day 1), then flip modules to strict one at a time as you annotate them. This lets you commit to mypy in CI immediately without blocking on full annotation.

Three-phase ramp:

**Phase 1 (Baseline — week 1):** Get mypy running cleanly on the whole codebase. Use global `ignore_errors = false` but only `check_untyped_defs = true`. This gives you errors-in-typed-functions only.

**Phase 2 (Per-module tightening):** As god-files are split, flip each new focused module to `disallow_untyped_defs = true`. Keep legacy god-files in a named override with relaxed settings.

**Phase 3 (Strict gate):** Once coverage is high, move to near-`strict` globally with specific carve-outs for third-party stubs and legacy surfaces.

**pyproject.toml config (Phase 1 baseline):**

```toml
[tool.mypy]
python_version = "3.12"
# Phase 1: check existing typed code, catch obvious errors, don't require annotations yet
warn_unused_configs = true
warn_redundant_casts = true
warn_unused_ignores = true
strict_equality = true
check_untyped_defs = true         # type-check bodies even if unannotated
# Not yet: disallow_untyped_defs, disallow_untyped_calls
exclude = [
    "^setup\\.py$",               # bootstrap script, not a package
    "^tests/",                    # will add separately when coverage is higher
]

# Third-party libraries without stubs — suppress missing-import noise
[[tool.mypy.overrides]]
module = [
    "chromadb.*",
    "fastembed.*",
    "caldav.*",
    "icalendar.*",
    "croniter.*",
    "pyotp.*",
    "qrcode.*",
    "youtube_transcript_api.*",
    "faster_whisper.*",
    "duckduckgo_search.*",
    "fitz.*",                     # PyMuPDF
    "markitdown.*",
    "mcp.*",
]
ignore_missing_imports = true

# Legacy god-files: run mypy but tolerate annotation gaps during split phase
[[tool.mypy.overrides]]
module = [
    "src.tool_implementations",
    "routes.email_routes",
    "routes.cookbook_routes",
    "routes.model_routes",
    "routes.gallery_routes",
    "src.task_scheduler",
]
disallow_untyped_defs = false
warn_return_any = false

# Modules already cleaned up: full strictness
# (populate this list as god-files are split)
# [[tool.mypy.overrides]]
# module = ["src.config", "core.auth", "core.atomic_io"]
# disallow_untyped_defs = true
# warn_return_any = true
```

**Run command:**
```bash
python -m mypy src/ routes/ core/ services/
```

**Type stubs to install:**
```bash
pip install mypy==2.1.0 types-beautifulsoup4 types-python-dateutil types-PyYAML types-requests types-docutils
```
Most major libraries (fastapi, pydantic, sqlalchemy, httpx, cryptography, bcrypt) ship inline stubs or are fully typed — no separate `types-*` package needed.

**What NOT to do:**
- Do not run `mypy --strict` on the whole codebase on day 1. The ~4100-line `tool_implementations.py` alone would generate hundreds of errors.
- Do not use `# type: ignore` without `[error-code]` suffix — bare ignores hide new errors silently. Use `# type: ignore[attr-defined]` etc.
- Do not use pyright as the CI gate on this codebase at this time. Mixing two type checkers is expensive; mypy's ecosystem (stubs, plugins) is deeper for FastAPI/SQLAlchemy.

---

## 2. Lint + Format — ruff

### Recommendation: ruff 0.15.15

**Why ruff:** ruff replaces flake8, isort, pyupgrade, and black in a single Rust binary. It is 10-100× faster than any Python-based equivalent, which matters for a pre-commit and CI gate on a large codebase. FastAPI itself uses ruff. The `ruff format` subcommand is Black-compatible (same line length, same quote style defaults), so there is no stylistic disruption to existing well-formatted code.

**Ruff is a hard requirement here because:**
1. Currently Odysseus has *no linter at all* — the only check is `python -m py_compile`. The first linter run will produce noise; ruff's `--fix` flag and per-rule ignore capability make the initial cleanup tractable.
2. `pyupgrade` as a standalone tool would be needed to fix `datetime.utcnow()` in 24 files — ruff's `UP` (pyupgrade) rule set handles this automatically.
3. The `B` (flake8-bugbear) ruleset catches the class of "degrade gracefully" overuse that is flagged in the concerns.

**pyproject.toml config:**

```toml
[tool.ruff]
target-version = "py312"
line-length = 88                  # Black default; matches existing code style
indent-width = 4

# Exclude generated/bootstrap files
exclude = [
    ".git",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    "*.egg-info",
    "setup.py",                   # bootstrap, not a package module
]

[tool.ruff.lint]
select = [
    "E",    # pycodestyle errors
    "F",    # pyflakes (undefined names, unused imports)
    "I",    # isort (import ordering)
    "UP",   # pyupgrade (datetime.utcnow → datetime.now(UTC), f-strings, etc.)
    "B",    # flake8-bugbear (common footguns)
    "C4",   # flake8-comprehensions (unnecessary list() calls etc.)
    "SIM",  # flake8-simplify (simplifiable conditions)
    "TCH",  # flake8-type-checking (move pure-typing imports to TYPE_CHECKING block)
]

# Rules to leave off until the initial cleanup pass is done
# "D"  = docstring enforcement — too noisy for initial adoption
# "ANN" = annotation enforcement — covered by mypy instead
# "S"   = bandit rules — bandit is a separate tool with better baseline support

ignore = [
    "E501",   # line too long — not enforced (no hard column limit in conventions)
    "B008",   # Do not perform function calls in default args — FastAPI's Depends() pattern
    "SIM102", # nested if → use and — too aggressive for existing code style
    "SIM117", # nested with → combine — style preference, not correctness
]

# Per-file relaxations
[tool.ruff.lint.per-file-ignores]
"tests/*" = ["F401", "F811"]      # test files re-import for clarity; redefinition OK
"__init__.py" = ["F401"]          # barrel re-exports — unused import is intentional

[tool.ruff.lint.isort]
known-first-party = ["src", "routes", "core", "services"]
force-sort-within-sections = true

[tool.ruff.format]
quote-style = "double"            # matches existing code convention
indent-style = "space"
skip-magic-trailing-comma = false  # preserve trailing commas (used extensively)
line-ending = "auto"
```

**Run commands:**
```bash
ruff check .                      # lint only (review findings)
ruff check . --fix                # lint + auto-fix safe issues
ruff format .                     # format (Black-compatible)
ruff format . --check             # CI: fail if formatting differs
```

**CI enforcement order:** Run `ruff format --check` first (fast, no output noise), then `ruff check` (returns exit code 1 on violations). Both must pass before mypy runs.

**Initial cleanup approach:** On the first run, expect UP violations (utcnow, old-style Union/Optional), I violations (import order), and some F violations (unused imports in barrel files). Run `ruff check . --fix` to auto-fix the safe ones, then manually review the remainder. Commit the formatting pass as a standalone "chore: ruff format" commit before any structural changes so that blame history is clean.

**What NOT to use:**
- **black + isort + flake8 separately:** ruff supersedes all three; maintaining three tools has no benefit.
- **pylint:** Extremely slow, produces high false-positive noise on a large untyped codebase. Not worth the CI time.

---

## 3. Dependency Pinning — uv pip compile (preferred) or pip-tools

### Recommendation: `uv pip compile` as a drop-in pip-tools replacement

**Current state:** `requirements.txt` is unpinned (only `markitdown==0.1.5` is pinned). This is a security and reproducibility risk.

**Target state:** A `requirements.in` (abstract, human-maintained) compiled to a fully-pinned `requirements.txt` (concrete lockfile, committed). Existing pip-based Docker build requires no changes.

**Why uv over pip-tools:**
- `uv pip compile` is a drop-in replacement for `pip-compile` — same input/output format, same `requirements.in` → `requirements.txt` workflow.
- uv 0.11.18 is 10-100× faster than pip-tools 7.5.3 for resolution. On Odysseus's large dependency tree (chromadb, fastembed, onnxruntime, etc.) this matters for CI speed.
- uv is already the standard choice for Python tooling in 2026 (used by FastAPI, Pydantic, and most major Python projects for CI).
- uv ships as a single static binary — no pip install required in CI.

**If uv is not acceptable** (e.g., team prefers no new binaries in CI), use `pip-tools 7.5.3` — same workflow, slower but equally correct.

**Migration workflow:**

1. Create `requirements.in` from current `requirements.txt` (remove version pins, keep extras like `qrcode[pil]`):
```bash
# Extract abstract deps (strip version specifiers)
grep -v '^#' requirements.txt | sed 's/==.*//' > requirements.in
# Add back any intentional constraints (e.g. markitdown)
echo "markitdown[docx,pptx,xlsx,xls]==0.1.5" >> requirements.in
```

2. Compile to pinned lockfile:
```bash
uv pip compile requirements.in -o requirements.txt --python-version 3.12
```

3. For dev/CI tools, create `requirements-dev.in`:
```
-r requirements.in
mypy==2.1.0
ruff==0.15.15
pytest-cov==7.1.0
coverage==7.14.1
bandit==1.9.4
pip-audit==2.10.0
semgrep==1.164.0
```
Compile separately: `uv pip compile requirements-dev.in -o requirements-dev.txt --python-version 3.12`

4. Update regeneration command (in Makefile or CONTRIBUTING.md):
```bash
uv pip compile requirements.in -o requirements.txt --python-version 3.12
uv pip compile requirements-dev.in -o requirements-dev.txt --python-version 3.12
```

5. The Docker build stays identical — `pip install -r requirements.txt` works against the pinned file.

**What NOT to do:**
- Do not use `pip freeze > requirements.txt` — this captures the entire installed environment (including transitive deps of transitive deps of dev tools), is unportable, and cannot be maintained.
- Do not pin to `requirements.txt` directly without a `requirements.in` source of truth — you cannot regenerate or upgrade without knowing what's abstract vs. what's resolved.
- Do not use Poetry or PDM — the project has an existing pip-based Docker workflow; introducing a new package manager mid-project risks Dockerfile breakage and adds significant migration risk to what is supposed to be a behavior-preserving refactor.

**Note on `requirements-optional.txt`:** Apply the same compile workflow. Create `requirements-optional.in` and compile to a separate pinned file.

---

## 4. Test Coverage — coverage.py + pytest-cov

### Recommendation: coverage.py 7.14.1 + pytest-cov 7.1.0

**Why these are the right tools:** pytest-cov is the de facto pytest integration for coverage.py. Both are already mentioned informally in `TESTING.md` (`pip install pytest-cov` ad hoc). The modernization task is to formalize them in pyproject.toml and establish a baseline.

**Critical note for Odysseus:** The goal is NOT to enforce a coverage percentage gate immediately. The goal is to **generate a coverage map** to identify which surfaces (especially `tool_implementations.py`, `email_routes.py`) have thin test coverage *before* refactoring them. The tests currently have no `--cov` config at all — adding it produces the map needed.

**pyproject.toml config:**

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
# Add coverage collection as default (can be overridden with --no-cov for faster local runs)
addopts = "--cov=src --cov=routes --cov=core --cov=services --cov-report=term-missing --cov-report=html:htmlcov --no-cov-on-fail"

[tool.coverage.run]
branch = true                     # branch coverage, not just line coverage
source = ["src", "routes", "core", "services"]
omit = [
    "setup.py",
    "tests/*",
    "*/__init__.py",
    "*/conftest.py",
]
parallel = false                  # single-process (no parallel pytest workers configured)

[tool.coverage.report]
show_missing = true
precision = 1
# Do NOT set fail_under yet — establish baseline first, then set threshold
# fail_under = 70  # add after baseline measurement
exclude_lines = [
    "pragma: no cover",
    "if TYPE_CHECKING:",
    "raise NotImplementedError",
    "if __name__ == .__main__.:",
    "@(abc\\.)?abstractmethod",
]
```

**Run commands:**
```bash
# Full coverage run (generates HTML report in htmlcov/)
python -m pytest --cov=src --cov=routes --cov=core --cov=services \
    --cov-report=term-missing --cov-report=html:htmlcov

# Fast local run without coverage
python -m pytest --no-cov

# Coverage for a specific module (before refactoring)
python -m pytest tests/ --cov=src.tool_implementations --cov-report=term-missing
```

**Baseline measurement protocol:**
1. Run with `--cov-report=json:coverage.json` to get machine-readable coverage data.
2. Commit `coverage.json` baseline to `.planning/` (not to repo root — it's too large for runtime).
3. The modules with <30% coverage are the ones that need tests before refactoring: expected candidates are `src/tool_implementations.py`, `routes/email_routes.py`, `routes/cookbook_routes.py`.

**Warning — conftest.py stubbing interaction:** Odysseus's `conftest.py` stubs out `sqlalchemy`, `httpx`, `fastapi`, etc. with `MagicMock` before import. This will suppress coverage for branches that exercise those imports. The coverage map produced is a *lower bound* on real coverage — actual runtime coverage in Docker is higher. Don't mistake low stub-test coverage for "this code is never reached."

**What NOT to do:**
- Do not add `fail_under = 80` (or any number) in CI on day 1. The god-files have thin coverage and you'll block CI before you've had a chance to measure and fill gaps.
- Do not install `pytest-cov` as ad hoc only — formalize it in `requirements-dev.in` so all contributors measure coverage the same way.

---

## 5. Security Scanning

### 5a. SAST — bandit 1.9.4 + semgrep 1.164.0

Use **both** — they are complementary, not redundant.

**bandit:** Fast, local, AST-based. Finds the most common Python security anti-patterns (hardcoded credentials, `subprocess(shell=True)`, `eval()`, weak crypto, `assert` used for security). Zero configuration required for a first run. Produces a baseline JSON file so subsequent runs only report *new* findings.

**semgrep:** Rule-based pattern matching with taint-tracking. The `p/python` and `p/fastapi` rulesets cover OWASP Top 10 patterns specific to FastAPI/Starlette (e.g., CORS misconfiguration, missing auth decorators, SQL injection through SQLAlchemy text()). Free for self-hosted CLI use.

**Use bandit for:** Quick code-smell scan, subprocess/eval/hardcoded-secret detection. Fast enough for pre-commit.

**Use semgrep for:** The formal security audit pass. Run `semgrep scan --config p/python --config p/fastapi` once to generate the audit findings list. Triage by severity. This is the systematic OWASP ASVS L1 coverage layer.

**bandit pyproject.toml config:**
```toml
[tool.bandit]
exclude_dirs = ["tests", "setup.py"]
# Start with medium+ severity to reduce noise on first run
# skips = ["B101"]  # assert used as security check — review manually
```

**bandit baseline workflow:**
```bash
# Generate baseline (run once, commit the JSON)
bandit -r src/ routes/ core/ services/ -f json -o .planning/bandit-baseline.json

# Subsequent CI runs: only fail on NEW findings vs baseline
bandit -r src/ routes/ core/ services/ -b .planning/bandit-baseline.json
```

**semgrep audit command:**
```bash
# Full audit pass
semgrep scan --config p/python --config p/fastapi --config p/secrets \
    src/ routes/ core/ services/ --json -o .planning/semgrep-audit.json

# CI gate (post-triage): only run curated ruleset
semgrep scan --config .semgrep.yml src/ routes/ core/ services/
```

**Note on semgrep Pro vs CE:** The free Community Edition covers OWASP Top 10 patterns and has FastAPI-specific rules. The Pro taint-tracking engine (paid) adds cross-file dataflow analysis. For this audit, CE is sufficient — the taint cases in Odysseus (user input → SQL/shell) are contained within single route handlers, not spread across multiple files.

### 5b. Dependency Scanning — pip-audit 2.10.0

pip-audit scans `requirements.txt` against the OSV (Open Source Vulnerabilities) and PyPI advisory databases. It is maintained by PyPA (the Python Packaging Authority) — use this over `safety` (safety's free tier is rate-limited and requires an API key; pip-audit is fully free and uses the same advisory data).

```bash
# Audit pinned requirements
pip-audit -r requirements.txt

# Audit including optional
pip-audit -r requirements.txt -r requirements-optional.txt

# CI: output JSON for tracking
pip-audit -r requirements.txt --format json -o .planning/pip-audit-report.json
```

Add to CI as a blocking gate once requirements.txt is pinned (it requires pinned versions to function; an unpinned file produces no useful output).

### 5c. OWASP ASVS L1 Audit Framework

ASVS L1 is a checklist of ~130 controls for self-hosted web applications. For Odysseus, the relevant L1 items to systematically verify are:

| ASVS Category | Odysseus surface | Verification approach |
|---|---|---|
| V2 Authentication | `core/auth.py`, TOTP, session cookie | Code review + `test_security_regressions.py` |
| V3 Session Management | Cookie flags, session revocation | `test_auth_session_revocation.py` + bandit |
| V4 Access Control | Per-owner row scoping, `LOCALHOST_BYPASS` | `test_security_regressions.py` + manual probe |
| V5 Input Validation | Route handler parsing, `_request_values` | semgrep `p/fastapi` |
| V6 Cryptography | bcrypt, Fernet key co-location | bandit B324/B303 + key storage review |
| V7 Error Handling | Structured error payloads (no stack traces) | code review |
| V9 Comms | HTTPS/TLS in deployment | Docker Compose + security headers |
| V13 API Security | Rate limiting, CORS | `core/middleware.py` review |
| V14 Configuration | SECRET_KEY, `.env` handling, SECURE_COOKIES | semgrep `p/secrets` |

The existing `THREAT_MODEL.md` and `tests/test_security_regressions.py` already cover substantial V2/V3/V4 ground. Run the ASVS audit as a structured checklist, not a from-scratch exercise.

---

## 6. Frontend Quality — ESLint + Prettier (lightweight, no build step)

### Recommendation: ESLint 10.4.1 + Prettier 3.8.3 (npm devDependencies only)

The vanilla-JS frontend (`static/`, 65 modules, native ES modules with `.js` extensions, no bundler) can benefit from basic linting and formatting without adding a build step. Both tools run via CLI against the source files and don't require transpilation or bundling.

**ESLint 10 with flat config** is fully stabilized (the legacy `.eslintrc` format was removed in v10). For vanilla browser JS with ES modules, the minimal config is:

```javascript
// eslint.config.mjs  (in project root)
import globals from "globals";
import js from "@eslint/js";

export default [
    {
        files: ["static/js/**/*.js"],
        languageOptions: {
            globals: {
                ...globals.browser,
                ...globals.es2022,
            },
            ecmaVersion: 2022,
            sourceType: "module",
        },
    },
    js.configs.recommended,
    {
        rules: {
            "no-console": "warn",
            "no-unused-vars": ["error", { "argsIgnorePattern": "^_" }],
        },
    },
];
```

**Prettier config** (`.prettierrc` in project root):
```json
{
    "singleQuote": false,
    "semi": true,
    "tabWidth": 4,
    "printWidth": 100,
    "trailingComma": "es5"
}
```

**package.json scripts:**
```json
{
    "scripts": {
        "lint:js": "eslint static/js/",
        "lint:js:fix": "eslint static/js/ --fix",
        "format:js": "prettier --write static/js/",
        "format:js:check": "prettier --check static/js/"
    },
    "devDependencies": {
        "eslint": "^10.4.1",
        "@eslint/js": "^10.4.1",
        "globals": "^15.0.0",
        "prettier": "^3.8.3"
    }
}
```

**Install:**
```bash
npm install -D eslint @eslint/js globals prettier
```

**What NOT to do:**
- Do not add TypeScript compilation, webpack, Vite, or any bundler. PROJECT.md explicitly prohibits a build-step migration.
- Do not use the legacy `.eslintrc` format — it was removed in ESLint v10.
- Do not enforce formatting in CI on the first pass. Run `prettier --check` as a non-blocking warning initially; the existing JS likely has inconsistent style and cleaning it up should be a separate commit.
- Do not add eslint-plugin-react, eslint-plugin-vue, or similar framework plugins — this is plain vanilla JS.

**Priority note:** JS linting is the lowest-priority item in this modernization milestone. The Python quality gates (mypy, ruff, coverage, security) deliver materially more value. Establish the Python CI first; add JS linting as a follow-up.

---

## Recommended Stack Summary Table

| Tool | Version | Category | Priority |
|------|---------|----------|----------|
| ruff | 0.15.15 | Lint + format (Python) | Critical — gates commit |
| mypy | 2.1.0 | Type checking | Critical — gates commit |
| coverage.py | 7.14.1 | Test coverage measurement | High — needed before refactor |
| pytest-cov | 7.1.0 | Coverage pytest plugin | High — needed before refactor |
| uv | 0.11.18 | Dependency pinning / compile | High — security + reproducibility |
| bandit | 1.9.4 | Python SAST | High — audit phase |
| pip-audit | 2.10.0 | Dependency CVE scanning | High — audit phase |
| semgrep | 1.164.0 | Framework-aware SAST | Medium — audit pass |
| eslint | 10.4.1 | JS lint | Low — post-Python-CI |
| prettier | 3.8.3 | JS format | Low — post-Python-CI |

---

## Installation (dev environment bootstrap)

```bash
# Python quality tools (add to requirements-dev.in, then compile)
pip install \
    mypy==2.1.0 \
    types-beautifulsoup4 types-python-dateutil types-PyYAML \
    ruff==0.15.15 \
    coverage==7.14.1 \
    pytest-cov==7.1.0 \
    bandit==1.9.4 \
    pip-audit==2.10.0 \
    semgrep==1.164.0

# uv (single binary, install separately from pip ecosystem)
curl -LsSf https://astral.sh/uv/install.sh | sh
# Or: pip install uv==0.11.18

# JS tools (only if working on frontend)
npm install -D eslint @eslint/js globals prettier
```

---

## CI Pipeline Order

Run gates in this order (fastest/cheapest first, most expensive last):

```
1. ruff format --check       (~2s)     — formatting gate
2. ruff check .              (~5s)     — lint gate
3. mypy src/ routes/ core/   (~30s)    — type gate
4. python -m pytest          (~varies) — behavior contract
5. pytest --cov              (same run as 4 or separate)
6. bandit -r ... -b baseline (~10s)    — security SAST
7. pip-audit -r requirements.txt (~15s) — dependency CVE gate
```

Steps 1-3 are pre-merge blocking. Steps 4-5 are blocking (suite must be green). Steps 6-7 are blocking once the baseline is established; advisory-only during the initial audit pass.

---

## Alternatives Considered

| Recommended | Alternative | Why Not |
|---|---|---|
| ruff | black + flake8 + isort | Three separate tools, slower, no auto-upgrade rules (UP). ruff is a strict superset in capability. |
| ruff | pylint | Pylint is ~10× slower and produces very high false-positive noise on an untyped codebase. Not suitable as a CI gate for Odysseus at this stage. |
| mypy | pyright | Pyright is faster but has coarser per-module override support; designed for VS Code IDE use, not brownfield CI adoption. |
| mypy | ty (Astral) | ty is pre-1.0 as of June 2026. Not production-ready for a CI gate. Worth revisiting in 6-12 months. |
| uv pip compile | pip-tools pip-compile | pip-tools is correct but 10-100× slower. uv is a drop-in replacement with identical output format. |
| uv pip compile | pip freeze | pip freeze captures dev-environment-specific paths and doesn't produce a maintainable lockfile. |
| uv pip compile | Poetry / PDM | Would require converting the entire project to a new package manager. Adds migration risk to a behavior-preserving refactor. |
| pip-audit | safety | safety's free tier is rate-limited and requires an account. pip-audit uses the same advisory data (OSV) and is fully free, maintained by PyPA. |
| semgrep CE | semgrep Pro | Pro's cross-file taint-tracking is valuable but paid. Odysseus's attack surface doesn't require cross-file taint analysis at L1 audit level. |
| ESLint flat config | eslint + eslintrc | Legacy .eslintrc removed in ESLint v10. Flat config is the only supported format. |

---

## Version Compatibility

| Package | Compatible With | Notes |
|---|---|---|
| mypy 2.1.0 | Python 3.12 | Fully compatible. Python 3.12 syntax (PEP 695 type aliases) supported. |
| ruff 0.15.15 | Python 3.12 | Set `target-version = "py312"` in config. |
| pytest-cov 7.1.0 | pytest 7.x / 8.x | Compatible with `asyncio_mode = "auto"`. |
| coverage.py 7.14.1 | pytest-cov 7.1.0 | Required version — pytest-cov 7.x requires coverage.py 7.x. |
| bandit 1.9.4 | Python 3.12 | No known compatibility issues. |
| pip-audit 2.10.0 | pip 24+ | Requires packages to be pinned (semver) — audit is triggered after pinning is complete. |
| ESLint 10.4.1 | Node.js ≥ 20.19 | ESLint v10 dropped Node < 20. Odysseus's Dockerfile installs Node from apt — verify version. |

---

## Sources

- Context7 `/astral-sh/ruff` — ruff configuration, rule sets, pyproject.toml format (HIGH confidence)
- Context7 `/python/mypy` — gradual typing strategy, per-module overrides, existing codebase guide (HIGH confidence)
- Context7 `/jazzband/pip-tools` — pip-compile workflow (HIGH confidence)
- Context7 `/astral-sh/uv` — uv pip compile, requirements.txt export, pip compat (HIGH confidence)
- Context7 `/pytest-dev/pytest-cov` — configuration, pyproject.toml integration (HIGH confidence)
- Context7 `/pycqa/bandit` — config, baseline workflow (HIGH confidence)
- Context7 `/pypa/pip-audit` — CLI options, requirements.txt scanning (HIGH confidence)
- PyPI `ruff` — verified version 0.15.15 (June 2026)
- PyPI `mypy` — verified version 2.1.0 (May 2026)
- PyPI `pip-tools` — verified version 7.5.3 (Feb 2026)
- PyPI `uv` — verified version 0.11.18 (June 2026)
- PyPI `coverage` — verified version 7.14.1 (May 2026)
- PyPI `pytest-cov` — verified version 7.1.0 (March 2026)
- PyPI `bandit` — verified version 1.9.4 (Feb 2026)
- PyPI `pip-audit` — verified version 2.10.0 (Dec 2025)
- PyPI `semgrep` — verified version 1.164.0 (May 2026)
- ESLint blog — v10.0.0 released Feb 2026; flat config is default; v10.4.1 latest
- prettier changelog — v3.8.3 latest stable (April 2026)
- mypy docs (existing_code.html) — per-module override strategy, ignore_errors inversion pattern
- [Semgrep Community Edition](https://semgrep.dev/products/community-edition/) — free OSS CLI, p/fastapi ruleset confirmed
- [DEV: Semgrep vs Bandit 2026](https://dev.to/rahulxsingh/semgrep-vs-bandit-python-security-scanning-compared-2026-5e5j) — complementary tool roles (MEDIUM confidence — single source)

---

*Stack research for: Odysseus modernization tooling (Python 3.12 / FastAPI monolith)*
*Researched: 2026-06-03*
