# Run Odysseus on a throwaway cloud box (crabbox × islo.dev)

Odysseus is a self-hosted AI workspace. Normally you clone it, build a venv,
install deps, and boot uvicorn on your own machine. This fork adds a one-command
path to do all of that on a remote [islo.dev](https://islo.dev) microVM instead —
so you can try it (or run CI) without touching your laptop, and throw the box
away when you're done.

It's driven by [`crabbox.sh`](../crabbox.sh) at the repo root.

```bash
./crabbox.sh test     # warm a box, sync this checkout, run the test suite, tear down
./crabbox.sh serve    # boot Odysseus on a box and print a public URL you can click
./crabbox.sh shell    # interactive shell on the box (kept until you exit)
```

## Why two tools

| Path  | Tool | Box lifetime | Best for |
|-------|------|--------------|----------|
| `test` / `shell` | [crabbox](https://github.com/openclaw/crabbox) | ephemeral — warmed per run, released on exit | running the suite against your **uncommitted working tree** |
| `serve` | [islo.dev CLI](https://islo.dev) | persistent — survives until `islo rm` | a **clickable live workspace** at a public HTTPS URL |

Both run on islo.dev's sandbox fabric. crabbox warms a box, rsyncs your working
tree (including the local diff), runs the command, and tears down. islo clones
the repo straight from GitHub, keeps the box alive, and hands you a share URL.

## Setup

```bash
# 1. an islo.dev API key
islo api-key create odysseus-demo --output-file islo.key
export ISLO_API_KEY=$(cat islo.key)
```

That's it. `crabbox.sh` **auto-installs the real [openclaw crabbox](https://crabbox.sh)**
on first use if it isn't already on your PATH (Homebrew tap, or the GoReleaser
archive for your OS/arch). To install it yourself ahead of time:

```bash
brew install openclaw/tap/crabbox    # or grab an archive from the releases page
```

## Knobs

Everything is overridable by environment variable — see `./crabbox.sh --help`.
The defaults give a 4-vCPU / 8 GB `python:3.12-slim` box. `ODYSSEUS_TESTS=tests`
runs the full 355-file suite; the default runs a fast, dependency-light slice.

## CI

[`.github/workflows/crabbox-islo.yml`](../.github/workflows/crabbox-islo.yml)
runs the same `./crabbox.sh test` on push/dispatch. Add an `ISLO_API_KEY`
repository secret to enable it; without the secret the job no-ops cleanly.
