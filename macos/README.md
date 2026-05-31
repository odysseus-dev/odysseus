# Odysseus on macOS

Odysseus is a local FastAPI web app. The macOS support in this folder makes it feel like a Mac app without rewriting the server:

- `install-macos.command` creates the Python virtual environment, installs requirements, runs first-time setup, builds `dist/Odysseus.app`, and launches the app.
- `launch-odysseus.command` starts the local Odysseus server and opens `http://127.0.0.1:7000`.
- `stop-odysseus.sh` stops the local server started by the launcher.
- `build-app-bundle.sh` creates a lightweight `.app` wrapper that points at this checkout.

## First Run

Double-click:

```text
macos/install-macos.command
```

The installer requires Python 3.11 or newer. It will print the generated admin password on first setup. Save that password, then change it after login.

## Daily Use

After install, open:

```text
dist/Odysseus.app
```

or double-click:

```text
macos/launch-odysseus.command
```

The launcher binds to `127.0.0.1:7000` by default and opens your browser to the local app.

## Stop Odysseus

Run:

```bash
./macos/stop-odysseus.sh
```

## Notes

- This is a local launcher, not a notarized public macOS distribution.
- Do not expose Odysseus directly to the public internet. Keep auth enabled and use a real HTTPS reverse proxy for anything beyond localhost or trusted LAN/VPN.
- Docker remains the most complete option if you want the bundled ChromaDB, SearXNG, and ntfy services managed together.
