FROM python:3.12-slim

# System deps. tmux is required by Cookbook for background downloads/serves.
# openssh-client is required for Cookbook remote server tests, setup, probes,
# downloads, and serves from Docker installs.
# git/cmake are required when Cookbook builds llama.cpp on first llama.cpp
# launch inside Docker.
# nodejs/npm provide npx for the optional built-in Browser MCP server.
# gosu lets the entrypoint drop privileges cleanly so signals still reach
# uvicorn directly (no extra shell layer like `su`/`sudo` would add).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    curl \
    git \
    nodejs \
    npm \
    tmux \
    openssh-client \
    gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Codex Runtime. Pin the CLI so app-server JSON-RPC behavior is stable.
RUN npm install -g @openai/codex@0.137.0 \
    && mv /usr/local/bin/codex /usr/local/bin/codex-real \
    && printf '%s\n' \
        '#!/bin/sh' \
        'set -e' \
        'if [ "$(id -u)" = "0" ] && command -v gosu >/dev/null 2>&1; then' \
        '  PUID="${PUID:-1000}"' \
        '  PGID="${PGID:-1000}"' \
        '  CODEX_HOME="${CODEX_HOME:-/app/data/codex}"' \
        '  mkdir -p "$CODEX_HOME"' \
        '  chown -R "$PUID:$PGID" "$CODEX_HOME" 2>/dev/null || true' \
        '  exec gosu "$PUID:$PGID" /usr/local/bin/codex-real "$@"' \
        'fi' \
        'exec /usr/local/bin/codex-real "$@"' \
        > /usr/local/bin/codex \
    && chmod +x /usr/local/bin/codex

# Copy app code
COPY . .

# Create data directory (mount a volume here for persistence)
RUN mkdir -p data logs services/cache/search

# Entrypoint that drops to PUID/PGID (default 1000:1000) and repairs
# ownership on the bind-mounted /app/data and /app/logs. Without this,
# the container runs as root and writes root-owned files into host
# bind mounts — any later non-root run (or a host user trying to
# update them) silently fails on EPERM, breaking skill extraction,
# prefs persistence, mail attachments, etc.
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 7000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7000"]
