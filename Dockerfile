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

# Optional opt-in: SOPS for encrypted-at-rest secrets (see SECURITY.md).
# Small static Go binary; only invoked when /app/secrets.env is present at
# container start. Pinned by version + per-arch SHA256 from the upstream
# release (https://github.com/getsops/sops/releases/tag/v3.13.1) so a
# compromised CDN cannot substitute a different binary.
ARG SOPS_VERSION=3.13.1
ARG SOPS_SHA256_AMD64=620a9d7e3352ababeca6908cea24a6e8b14ce89a448ddbd3f94f1ef3398f470a
ARG SOPS_SHA256_ARM64=19576fb1734dbf8fb77eda0cf0f3a2218f99bf4d33b814318e5e10d6babb9820
RUN arch="$(dpkg --print-architecture)" \
    && case "$arch" in \
        amd64) expected_sha="$SOPS_SHA256_AMD64" ;; \
        arm64) expected_sha="$SOPS_SHA256_ARM64" ;; \
        *) echo "sops install: unsupported arch '$arch'" >&2; exit 1 ;; \
    esac \
    && curl -fsSL -o /usr/local/bin/sops \
       "https://github.com/getsops/sops/releases/download/v${SOPS_VERSION}/sops-v${SOPS_VERSION}.linux.${arch}" \
    && echo "${expected_sha}  /usr/local/bin/sops" > /tmp/sops.sha256 \
    && sha256sum -c /tmp/sops.sha256 \
    && rm /tmp/sops.sha256 \
    && chmod +x /usr/local/bin/sops

WORKDIR /app

# Install Python deps first (layer cache). Optional extras (PyMuPDF AGPL, etc.)
# are opt-in so the default image stays MIT-core; see requirements-optional.txt.
ARG INSTALL_OPTIONAL=false
COPY requirements.txt requirements-optional.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$INSTALL_OPTIONAL" = "true" ]; then pip install --no-cache-dir -r requirements-optional.txt; fi

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
