# Odysseus Desktop (Windows)

A native Windows desktop build of [Odysseus](https://github.com/pewdiepie-archdaemon/odysseus) — a
[Tauri](https://tauri.app/) shell that runs the full app locally with **no Python, Node, Git, or
terminal required by the end user**. Install, launch, done.

It's a thin wrapper: a single `.exe` installer that bundles a private Python runtime and starts the
real Odysseus backend behind a frameless window pointed at `127.0.0.1:7000`.

## What it does

- **One-click install** — a per-user NSIS installer (`Odysseus_*-setup.exe`); no admin rights.
- **Self-contained** — bundles a standalone Python runtime + all dependencies, so nothing needs to be
  installed on the machine.
- **Vector memory** — starts a local **ChromaDB** server (`127.0.0.1:8100`) for embeddings/RAG.
- **Web search** — DuckDuckGo, no API key.
- **Agent shell** — bundles **PortableGit** so the app's bash tool works without Git installed.
- **First-run setup** — a friendly screen that detects the machine (virtualization, WSL2, GPUs, a
  running Ollama) and links out to optional local-model setup.
- **Frameless UI** — a custom title bar injected over the app to match its theme.
- **Weekly self-update** — see below.

## Building

**Prerequisites:** [Rust](https://rustup.rs/) (stable, MSVC toolchain), [Node.js](https://nodejs.org/)
(18+), and the WebView2 runtime (present on Windows 10/11 by default).

```powershell
# optional: point at a local clone of the app; otherwise it's cloned fresh
$env:ODYSSEUS_SRC = "C:\path\to\odysseus"

.\build.ps1
```

`build.ps1` reproduces the bundled payload (which is **not** committed — see `.gitignore`):

1. downloads a standalone **Python 3.13** runtime into `src-tauri/backend/runtime/`,
2. copies the app source from upstream into `src-tauri/backend/`,
3. `pip install`s the app's requirements + the desktop extras (chromadb, duckduckgo-search, uvicorn),
4. downloads **PortableGit** into `src-tauri/backend/git/`,
5. runs `npx tauri build`.

Output: `src-tauri/target/release/bundle/nsis/Odysseus_*-setup.exe` (~150 MB).

> The repo holds only the wrapper source (Rust shell, two launchers, HTML, icons, build script).
> The ~800 MB payload is built locally — keep it out of git.

## How it works

On launch the Rust shell (`src-tauri/src/main.rs`) spawns, off the UI thread:

1. `chroma_server.py` → ChromaDB on `127.0.0.1:8100`
2. `uvicorn app:app` → the Odysseus backend on `127.0.0.1:7000`

…waits for `:7000`, then points the window at it (or, on first run, shows the setup screen). The
bundled Python runtime and PortableGit are prepended to `PATH` for the child processes. On exit, the
shell tears the backend processes down.

## Self-update

`src-tauri/backend/updater.py` runs on launch:

- **`--apply`** (synchronous, before the backend starts): if an update was staged, it backs up the
  current code, overlays the new source, installs new deps, smoke-tests `import app`, and **rolls back
  automatically** if that fails.
- **`--check`** (background, at most weekly): checks for a newer **published GitHub release** — only
  releases are ever applied, never arbitrary commits — and stages it.
- **`--rollback`** (manual recovery hatch): restores the previous known-good version:
  ```powershell
  & "$env:LOCALAPPDATA\Odysseus\backend\runtime\python.exe" "$env:LOCALAPPDATA\Odysseus\backend\updater.py" --rollback
  ```

> **Trust model:** auto-update runs whatever an applied release ships; the `import app` smoke test only
> catches *crashes*, not malicious or subtly-broken code. It is gated to published releases and the
> runtime/Git/user-data are never touched, but there is no signature verification. Harden this (release
> signing, a review gate) before relying on it in a hostile environment.

## Logs

All diagnostics are written to **`%LOCALAPPDATA%\Odysseus\backend\logs\`** (there's no console — this
is a GUI app):

- `desktop.log` — the wrapper (backend start, PIDs, updater, errors)
- `backend.log` — the full app/uvicorn log
- `chroma.log` — the ChromaDB server
- `updater.log` — update activity

If something fails to start, these are the first place to look.

## Tests

```powershell
python tests\test_updater.py   # or: pytest
```

Covers the updater's overlay / auto-rollback / `--rollback` paths.

## Security notes

- `open_url` opens only `http(s)` URLs via `explorer.exe` (no shell parsing).
- `install_wsl2` triggers an elevated `wsl --install` (a UAC prompt the user must accept).
- All services bind to `127.0.0.1` only.
- See the trust-model note under **Self-update** above.

## License

This wrapper bundles and runs upstream Odysseus; it is offered under the **same license** as the app
(see the upstream `LICENSE`). The build pulls in third-party dependencies under their own licenses —
review them before redistributing any built binary.
