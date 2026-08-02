# Native Windows baseline

## Scope

Captured against upstream `dev` commit
`25c9e735ef5ce605f47f8f666ac6689056d2c10c` on branch
`codex/pdv-integration-v1`. This baseline does not start the application,
Docker, model servers, providers, GPU probes, or network listeners.

## Host and dependency state

- Windows `10.0.26200.0`; PowerShell `7.6.3`; Git `2.52.0.windows.1`.
- CPython `3.12.10` in ignored `.venv`; pip `26.2`.
- Git Bash available at the standard Git for Windows installation.
- Core `requirements.txt` installed successfully into `.venv`.
- `python -m pip check`: `No broken requirements found.`
- Python dependencies are unpinned; no Python lockfile exists. The npm
  `package-lock.json` is lockfile version 3.
- Optional dependencies from `requirements-optional.txt` were not installed.

## Guardrails

- Native bind contract: `127.0.0.1:7000`.
- Reserved model ports untouched: `11435`, `11436`.
- No key contents, paid APIs, provider credentials, Docker, or GPUs used.
- First-time `setup.py` was not run because it creates auth/data state and may
  emit a generated admin password. Runtime route/import tests use isolated test
  state instead.

## Reproduction commands

```powershell
git clone https://github.com/odysseus-dev/odysseus.git C:\Users\User\Desktop\PDV_APPS\_external\odysseus-pdv-integration-v1
git -C C:\Users\User\Desktop\PDV_APPS\_external\odysseus-pdv-integration-v1 fetch --all --tags --prune
git -C C:\Users\User\Desktop\PDV_APPS\_external\odysseus-pdv-integration-v1 switch -c codex/pdv-integration-v1
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

Validation results and any upstream failures are appended after the bounded test
run; no failure is hidden or converted to an expected failure.
