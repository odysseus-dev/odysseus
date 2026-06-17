#!/bin/bash
# SessionStart hook for Claude Code on the web.
# Installs Python dependencies and starts the Docker daemon so AI Workspaces
# (Docker-backed containers) and the test/lint tooling work in web sessions.
#
# Runs synchronously: the session waits until this completes, so dependencies
# and Docker are guaranteed ready before the agent loop starts.
set -euo pipefail

# Only run in the remote (Claude Code on the web) environment. Local sessions
# manage their own venv / Docker and should not be disturbed.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"

# ── 1. Python dependencies ───────────────────────────────────────────────────
# Idempotent and cache-friendly: re-running when everything is present is fast.
# requirements.txt pins runtime deps (incl. the docker SDK) plus pytest /
# pytest-asyncio for the test suite.
if [ -f requirements.txt ]; then
  # The base image ships debian-managed builds of PyJWT and cryptography that
  # are both broken here (missing _cffi_backend / panicking rust bindings) and
  # that pip cannot uninstall ("RECORD file not found"). Left alone they either
  # abort the requirements install or crash any import of cryptography (used by
  # secret storage, JWT, the DB init, and therefore the whole test suite).
  # Re-installing them into /usr/local — which shadows the debian copies —
  # gives working wheels. Best effort; harmless if already current.
  pip install --quiet --ignore-installed PyJWT cryptography >/dev/null 2>&1 || true
  pip install --quiet -r requirements.txt
fi

# ── 2. Docker daemon (for AI Workspaces) ─────────────────────────────────────
# The app talks to Docker via the docker-py SDK over /var/run/docker.sock.
# The sandbox ships the docker CLI/daemon binaries but does not start dockerd,
# so launch it in the background and wait for the socket to come up.
if ! docker info >/dev/null 2>&1; then
  if command -v dockerd >/dev/null 2>&1; then
    dockerd >/tmp/dockerd.log 2>&1 &
    for _ in $(seq 1 20); do
      if docker info >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done
    if ! docker info >/dev/null 2>&1; then
      echo "WARNING: Docker daemon did not become ready; AI Workspaces will be unavailable. See /tmp/dockerd.log" >&2
    fi
  else
    echo "WARNING: dockerd not found; AI Workspaces will be unavailable." >&2
  fi
fi
