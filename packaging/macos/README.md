# Odysseus — Portable macOS App

Builds a self-contained `Odysseus.app` and drag-to-install `.dmg` for Apple
Silicon Macs. Unlike the repo's `build-macos-app.sh` (which creates a
path-baked launcher that requires a local venv), this produces a truly
**portable bundle** — Python, all pip deps, and SearXNG are frozen inside the
`.app`. End users install nothing.

## Requirements

**Apple Silicon (M-series) only.**

```bash
xcode-select --install
brew install python@3.11 create-dmg
```

## Build

Run from the repo root or from this directory:

```bash
cd packaging/macos
./build.sh
```

Output: `packaging/macos/dist/Odysseus-1.0.1-macOS-arm64.dmg`

The build is fully reproducible — the standalone Python runtime is verified
against a pinned SHA-256, and SearXNG is checked out at a fixed commit.

## Signing and notarization (optional)

By default the app is unsigned — users right-click → Open once (Gatekeeper).
For distribution with a Developer ID:

```bash
CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" ./build.sh

xcrun notarytool submit dist/Odysseus-*-macOS-arm64.dmg \
  --keychain-profile "notary" --wait
xcrun stapler staple dist/Odysseus-*-macOS-arm64.dmg
```

## Architecture

```
Odysseus.app/Contents/
├── MacOS/Odysseus          ← launcher.sh (entry point)
└── Resources/
    ├── odysseus_app/       ← PyInstaller-frozen server + all pip deps
    ├── Odysseus.app/       ← PyInstaller GUI bundle (WKWebView window)
    ├── searxng_runtime/    ← Standalone Python 3.11 + SearXNG
    ├── config/searxng/     ← SearXNG settings template
    ├── bootstrap.py        ← First-launch setup (admin user, .env, DB)
    └── .env.example        ← Default configuration template
```

Runtime data lives at `~/Library/Application Support/Odysseus/`.

## Logs

```
~/Library/Application Support/Odysseus/logs/
├── launcher.log
├── odysseus.log
├── searxng.log
└── bootstrap.log   ← password not logged
```
