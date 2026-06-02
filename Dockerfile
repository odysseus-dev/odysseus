# Stage 1 — build Python wheels (needs compiler toolchain)
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# Stage 2 — slim runtime image
FROM python:3.12-slim

# Runtime system deps. tmux is required by Cookbook for background
# downloads/serves. openssh-client is required for Cookbook remote server
# tests, setup, probes, downloads, and serves from Docker installs.
# git/cmake are required when Cookbook builds llama.cpp on first launch
# inside Docker. nodejs/npm provide npx for the optional built-in Browser
# MCP server. gosu lets the entrypoint drop privileges cleanly so signals
# still reach uvicorn directly (no extra shell layer like `su`/`sudo`).
RUN apt-get update && apt-get install -y --no-install-recommends \
    cmake \
    curl \
    git \
    nodejs \
    npm \
    tmux \
    openssh-client \
    gosu \
    && rm -rf /var/lib/apt/lists/*

# Pre-create the non-root app user (default UID 1000). The entrypoint
# reuses this user when PUID matches, or creates a new one on override.
RUN useradd -u 1000 -m -s /bin/sh -d /app odysseus

WORKDIR /app

# Copy pre-built Python packages from the builder stage
COPY --from=builder /install /usr/local

# Copy app code
COPY . .

# Create data directories (mount volumes here for persistence)
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
