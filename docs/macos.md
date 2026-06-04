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
