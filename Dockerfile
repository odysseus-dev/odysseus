FROM python:3.14-slim

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

# Install Python deps first (layer cache). Optional extras (PyMuPDF AGPL, etc.)
# are opt-in so the default image stays MIT-core; see requirements-optional.txt.
ARG INSTALL_OPTIONAL=false
COPY requirements.txt requirements-optional.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$INSTALL_OPTIONAL" = "true" ]; then pip install --no-cache-dir -r requirements-optional.txt; fi

# Optional: bake the Playwright Browser MCP runtime so the built-in "Browser"
# server (src/builtin_mcp.py: builtin_browser, "npx @playwright/mcp") actually
# connects and works. It closes two startup gaps the app reports otherwise:
#   1. the @playwright/mcp npx package isn't cached -> the server is skipped;
#   2. Chromium isn't installed -> every browser tool call fails.
# The app drops privileges to uid 1000 with a HOME the root build can't
# predict, so we pin npm's cache and Playwright's browser dir to fixed,
# world-readable paths via ENV (these persist to runtime, where the cache
# check in builtin_mcp._npm_cache_roots() reads $npm_config_cache first).
# Installing the browser through @playwright/mcp's OWN bundled playwright CLI
# guarantees the Chromium revision matches its pinned playwright-core.
ARG INSTALL_BROWSER=false
ENV npm_config_cache=/npm-cache \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN if [ "$INSTALL_BROWSER" = "true" ]; then \
        apt-get update \
        && npx -y @playwright/mcp@latest --version \
        && npx -y --package=@playwright/mcp@latest -- playwright install --with-deps chromium \
        && chmod -R a+rwX /npm-cache \
        && chmod -R a+rX /ms-playwright \
        && rm -rf /var/lib/apt/lists/*; \
    fi

# Optional: bake Claude Code + Ollama so the chat's `delegate_to_claude_code`
# agent tool can hand a coding/build task to a headless Claude Code, backed by
# an Ollama Cloud model (default kimi-k2.7-code:cloud) via the local ollama
# daemon's Anthropic-compatible endpoint (Ollama >= 0.14). The cloud model runs
# on Ollama's infrastructure (no host GPU/RAM), billed to the operator's Ollama
# subscription. Requires OLLAMA_API_KEY at runtime (see docker-compose.override.yml).
#   - claude  -> /usr/local/bin/claude  (npm global, npm prefix is /usr/local)
#   - ollama  -> /usr/bin/ollama        (manual tarball install; no systemd/user
#                setup, which the official install.sh would attempt and fail on
#                in a slim build)
ARG INSTALL_CLAUDE=false
RUN if [ "$INSTALL_CLAUDE" = "true" ]; then \
        npm install -g @anthropic-ai/claude-code \
        && apt-get update && apt-get install -y --no-install-recommends zstd \
        && rm -rf /var/lib/apt/lists/* \
        && curl -fsSL https://github.com/ollama/ollama/releases/latest/download/ollama-linux-amd64.tar.zst -o /tmp/ollama.tar.zst \
        && tar --use-compress-program=unzstd -C /usr -xf /tmp/ollama.tar.zst \
        && rm -f /tmp/ollama.tar.zst \
        && { chmod -R a+rX /usr/lib/ollama 2>/dev/null || true; }; \
    fi

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
