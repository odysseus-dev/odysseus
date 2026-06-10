# Odysseus on macOS

This doc covers everything macOS-specific. For the launcher flags
themselves, see [`docs/launcher.md`](launcher.md).

## One-shot install

```sh
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
./odysseus.sh --launch=native
```

That command:

1. Installs Homebrew if it's missing.
2. Installs Python 3.11, Docker Desktop, and git via Homebrew if any of
   them are missing.
3. Creates `venv/` with `python3.11` and installs the pinned deps.
4. If Docker is available, starts a persistent `searxng/searxng` container
   on `127.0.0.1:8080` (honors `SEARXNG_INSTANCE` and `ODYSSEUS_NO_SEARXNG=1`).
5. Runs `python setup.py` to provision the local data directory.
6. Starts uvicorn on `127.0.0.1:7860` and opens the browser.

Re-running is fast: the requirements-hash cache skips `pip install` when
`requirements.txt` is unchanged.

## Auto-start at login

```sh
./odysseus.sh --install-service
```

> **The repo must NOT be under `~/Desktop`, `~/Documents`, or
> `~/Downloads`.** macOS's TCC framework blocks launchd from
> executing scripts that live in those folders. The install script
> detects this and prints a one-liner fix. The convention is
> `~/odysseus`.

This drops a per-user LaunchAgent at:

```
~/Library/LaunchAgents/com.odysseus.ui.plist
```

and bootstraps it into the `gui/$(id -u)` launchd domain. The agent:

- starts on login (`RunAtLoad = true`)
- respawns on crash (`KeepAlive.Crashed = true`, `SuccessfulExit = false`)
- throttles to one restart per 10 s so a crash loop doesn't hammer the box
- logs to `~/Library/Logs/Odysseus/{stdout,stderr}.log`
- runs `odysseus.sh --launch=native --no-open` (so the server starts but
  the browser doesn't pop every time you log in)
- inherits an explicit `PATH` (`/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin`)
  because launchd's default `PATH` doesn't include Homebrew
- runs in the user's GUI session (`ProcessType = Interactive`) so any
  Keychain access works the same as a Terminal launch

### Update flow

```sh
./odysseus.sh --update
```

On macOS, the launcher:

1. Detects whether the agent is loaded.
2. If so, `launchctl bootout`s it (cleanly stops the uvicorn child).
3. `git pull --rebase --autostash`.
4. Re-bootstraps the agent and `kickstart -k`s it to start fresh.

The native launcher (running inside the agent) also re-checks the
requirements-hash on every launch, so a no-op update doesn't trigger
a reinstall.

### Inspection

```sh
launchctl print gui/$(id -u)/com.odysseus.ui
tail -f ~/Library/Logs/Odysseus/stdout.log
launchctl list | grep odysseus
```

### Removal

```sh
./odysseus.sh --uninstall-service
```

Bootouts the agent, removes the plist, and leaves `~/Library/Logs/Odysseus/`
in place for grepping. Reinstall is one command away.

## The .app bundle

`./odysseus.sh --package-mac` (or `./build-macos-app.sh` directly) builds
`dist/Odysseus.app` + `dist/Odysseus.dmg`. Drag the .app from the dmg to
Applications and double-click.

What the .app does, in plain terms:

- Spawns a menu bar item ("⚙︎ Odysseus" → "● Odysseus" once the server
  is up) with Open in Browser, Open in Terminal, Reveal Log in Finder,
  and Quit.
- Drags the same repo's venv as `--install-service` does. The .app is a
  *launcher*, not a self-contained bundle — the install dir is baked in
  at build time. After moving the repo, re-run `--package-mac`.
- On first launch, sets up the venv (idempotently) and starts uvicorn.
  The first cold start downloads an embedding model — allow ~2 minutes.
- Caches your data under `~/Library/Application Support/Odysseus/` —
  the repo's `data/` and `logs/` become symlinks to that location.
  This is the macOS-conventional place for user data and means the
  .app can be moved/updated without losing your chats, notes, etc.
- Cmd-Q (or Quit from the menu) sends SIGTERM to the worker, which
  SIGTERMs uvicorn and waits for clean exit. No orphan processes.
- Ad-hoc code signed (no Developer ID required) so it launches
  without Gatekeeper prompts on your own machine. Distributing the
  .app to other people needs a Developer ID, which is out of scope.

If you move the repo and don't rebuild, the menu bar item shows the
"✕ Odysseus" error state with a clear "Install folder not found" /
"Repo at <path> is missing odysseus.sh" message. The "Open in Terminal"
menu item drops you into a shell at the install dir for recovery.

### Why a Swift stub?

The .app is a Cocoa app, not a shell script. A real menu bar item and
proper Cmd-Q semantics (where quitting sends SIGTERM and waits for
the child to flush logs before exiting) need a Cocoa run loop. The
Swift stub is ~300 lines and handles:

- NSStatusItem (menu bar) with state-aware title + tooltip
- DispatchSource on SIGTERM/SIGINT so a `kill` from another terminal
  still results in a clean worker shutdown (Cocoa doesn't always
  surface those as `applicationShouldTerminate` for accessory apps)
- Spawning the bash worker (`Contents/Resources/odysseus-app.sh`),
  forwarding its stdout/stderr to the user's log file
- Watching `~/Library/Application Support/Odysseus/state.json` and
  re-rendering the menu bar when it changes
- One-click "Open in Browser" using Chromium's `--app=URL` if a
  Chromium-family browser is installed, else the default browser
- Error → user notification (toast) so the user doesn't have to
  click the menu bar to find out something went wrong

The bash worker handles everything else: data relocation, first-run
setup, uvicorn lifecycle, status file writes, signal traps. Splitting
it this way means the Swift code is "just" Cocoa glue, and the
install/run logic lives in a script that's shellcheck-clean and
debuggable in isolation.

## Port 7000 vs 7860

macOS Monterey+ runs AirPlay Receiver on port 7000. Odysseus detects
this and flips the default to 7860. To force a different port:

```sh
./odysseus.sh --port=7900
# or persistently in .env:
echo "APP_PORT=7900" >> .env
```

## Apple Silicon vs Intel

The launcher auto-detects Apple Silicon via `uname -m` and points
to `/opt/homebrew` (Apple Silicon's Homebrew prefix) rather than
`/usr/local` (Intel's). It also enables Metal in llama.cpp/Ollama
where the relevant env var exists.

## GPU acceleration

The native path uses llama.cpp or Ollama with Metal on Apple Silicon.
The Docker path does not — Docker on macOS runs in a Linux VM with no
GPU passthrough. If you see a "CPU only" warning from
`odysseus --launch=docker`, that's expected; use `--launch=native` for
GPU.

## File layout when run as a service

| File | Purpose |
|---|---|
| `~/Library/LaunchAgents/com.odysseus.ui.plist` | LaunchAgent definition |
| `~/Library/Logs/Odysseus/stdout.log` | stdout of the launcher |
| `~/Library/Logs/Odysseus/stderr.log` | stderr of the launcher (uvicorn logs go here) |

Nothing under the repo is moved by the service install — `data/`,
`venv/`, and the SearXNG settings live in their original locations so
the CLI and service paths share state. (The `.app` distribution in
Phase 3 will move `data/` to `~/Library/Application Support/Odysseus/`
only when launched from the .app bundle, not from the CLI.)

## Why the repo must live outside `~/Desktop`

macOS's Transparency, Consent, and Control (TCC) framework restricts
what `launchd` can access when it runs a LaunchAgent. A Terminal
session inherits your user's TCC consents (Desktop, Documents,
Downloads); `launchd` does not. So a repo cloned into `~/Desktop/`
will install the plist fine, but every time the agent tries to spawn
it will fail with:

```
bash: /Users/you/Desktop/odysseus/odysseus.sh: Operation not permitted
shell-init: error retrieving current directory: getcwd: cannot access
parent directories: Operation not permitted
```

The fix is to keep the repo outside TCC scope. `~/odysseus` is the
convention. The install script detects this and refuses to install
with a one-liner fix; you don't have to remember.

## Homebrew install

The repo ships a Homebrew formula at `homebrew/odysseus.rb`. Once
it's in a tap, the install is one command:

```sh
brew install pewdiepie-archdaemon/tap/odysseus
```

This installs the whole repo to `libexec/` and symlinks the entry
points into `bin/`:

| Path | Resolves to |
|---|---|
| `odysseus` | the launcher |
| `odysseus-package` | the .app builder |
| `odysseus-install-service` | the launchd installer |
| `odysseus-uninstall-service` | the launchd uninstaller |

The TCC scope problem doesn't apply here: the formula puts the repo
under `/opt/homebrew/Cellar/odysseus/`, which is outside TCC's
restricted folders.

### First-time setup

```sh
brew services start odysseus   # auto-start at login, restart on crash
odysseus --launch=native       # or just run it once
```

The first launch provisions a venv at `libexec/venv/` using
Homebrew's `python@3.11`. Subsequent runs reuse the venv; the
launcher's requirements-hash cache skips `pip install` when nothing
changed.

### Logs and data

```
/opt/homebrew/var/log/odysseus.log
/opt/homebrew/var/log/odysseus-error.log
~/Library/Logs/Odysseus/   (when also using --install-service or the .app)
~/Library/Application Support/Odysseus/   (data + per-app state)
```

### Upgrade

```sh
brew update && brew upgrade odysseus
odysseus --update            # re-resolves Python deps if requirements.txt changed
```

`brew upgrade` swaps the keg. `odysseus --update` (in the launcher)
is also the right call for a `requirements.txt` change — the venv
is keyed on its hash, so a no-op install is a no-op.

### Setting up a tap

The formula lives at `homebrew/odysseus.rb` in the main repo. To
publish it as a tap, mirror it to a Homebrew tap repo:

```sh
git clone https://github.com/pewdiepie-archdaemon/homebrew-tap.git
cp odysseus/homebrew/odysseus.rb homebrew-tap/Formula/odysseus.rb
cd homebrew-tap && git add Formula/odysseus.rb && git commit -m "add odysseus" && git push
```

Until the formula's `url` and `sha256` are filled in (those need a
real `v0.1.0` tag), the install is `brew install --HEAD` only.
After a release:

```sh
brew install --build-from-source pewdiepie-archdaemon/tap/odysseus
```

### How it works

The formula uses the `service do` block so `brew services start
odysseus` writes a launchd plist that runs `odysseus
--launch=native --no-open` from the libexec dir, keep-alive on
crash, with logs to `/opt/homebrew/var/log/odysseus*.log`. The
`bin/odysseus` symlink is followed by the launcher's symlink-aware
`REPO_DIR` resolution — when launched via the symlink, the
launcher follows the chain to the real `libexec/` dir so paths
like `app.py` and the venv resolve correctly.
