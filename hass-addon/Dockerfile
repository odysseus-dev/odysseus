# Home Assistant add-on image for Odysseus.
#
# Home Assistant builds an add-on with the add-on folder as the Docker build
# context, so the application source cannot be copied from the parent repo.
# Instead this image clones the Odysseus source at build time. Point
# ODYSSEUS_REPO / ODYSSEUS_REF at a fork or a release tag to build something
# other than the default branch.
ARG BUILD_VERSION
ARG BUILD_ARCH
ARG ODYSSEUS_REPO=https://github.com/odysseus-dev/odysseus.git
ARG ODYSSEUS_REF=dev

FROM python:3.14.9-slim

ARG BUILD_VERSION
ARG BUILD_ARCH
ARG ODYSSEUS_REPO
ARG ODYSSEUS_REF

LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="addon" \
  io.hass.arch="amd64|aarch64"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# System deps mirror the upstream Odysseus image:
#   - build-essential/cmake/git: Cookbook builds llama.cpp on first use
#   - nodejs/npm/chromium: the built-in Browser MCP server + private_browser
#   - fontconfig/fonts-noto-cjk: real fallback glyphs for CJK pages in Chromium
#   - tmux: Cookbook background downloads and model serves
#   - openssh-client: Cookbook remote server support
#   - libgl1/libglib2.0-0t64/libxcb1: runtime libs opencv-python (cv2) loads
#   - libmagic1: content-based MIME sniffing in src/upload_handler.py
#   - jq: parses /data/options.json in run.sh
#   - iproute2/iputils-ping/net-tools/dnsutils/nmap: network inspection tools
#     used by agent tasks (ping, ss, netstat, dig, nmap)
#   - gosu: present so the entrypoint can drop privileges when run manually;
#     the add-on itself starts as root and keeps /data owned by root.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        curl \
        ca-certificates \
        git \
        jq \
        nodejs \
        npm \
        chromium \
        fontconfig \
        fonts-noto-cjk \
        tmux \
        openssh-client \
        iproute2 \
        iputils-ping \
        net-tools \
        dnsutils \
        nmap \
        gosu \
        libgl1 \
        libglib2.0-0t64 \
        libxcb1 \
        libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# Clone the application source. Branch/tag refs use a shallow clone; anything
# else (a commit SHA) falls back to a full clone plus checkout so pinned
# commits keep working.
RUN set -eux; \
    if ! git clone --depth 1 --branch "${ODYSSEUS_REF}" "${ODYSSEUS_REPO}" /app 2>/dev/null; then \
        git clone "${ODYSSEUS_REPO}" /app; \
        git -C /app checkout "${ODYSSEUS_REF}"; \
    fi; \
    rm -rf /app/.git

WORKDIR /app

# Refresh both Python trust bundles before installing application dependencies,
# then verify the native Brotli decoder that Debian's Node links against. The
# checks live in the cloned repo, so they run after the clone above.
RUN python scripts/check_ca_trust.py --system \
    && python -m pip install --no-cache-dir --require-hashes --only-binary=:all: --no-deps \
        -r requirements-bootstrap.txt \
    && python scripts/check_ca_trust.py --python \
    && python scripts/check_brotli.py --node /usr/bin/node

# Propagate hardened trust to Node (OpenSSL mode) and gRPC, matching upstream.
ENV NODE_OPTIONS=--use-openssl-ca \
    GRPC_DEFAULT_SSL_ROOTS_FILE_PATH=/etc/ssl/certs/ca-certificates.crt

# Chromium keeps a separate root store; apply the same eMudhra distrust policy.
RUN mkdir -p /etc/chromium/policies/managed \
    && cp docker/chromium-emudhra.json /etc/chromium/policies/managed/odysseus-emudhra.json

# Private browser automation wrapper used by the native `private_browser` tool.
# Chromium is installed above, so agent-browser drives the existing browser
# binary without paying `npx` startup/install overhead on each tool call.
RUN npm install -g agent-browser@0.35.0 --omit=dev --loglevel=error

# Python dependencies. requirements.txt already pins the new packages the
# 2026-10 dev line added (pypdfium2, pdfplumber, faster-whisper, yt-dlp,
# Pillow>=13, certifi>=2026.10.23, grpcio>=1.84.1, fastembed>=0.9.1).
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir python-magic==0.4.27

# Check gRPC's effective defaults after all Python dependencies are installed.
RUN python scripts/check_ca_trust.py --system --python --grpc

RUN mkdir -p data logs services/cache/search

COPY run.sh /run.sh
COPY seed_provider.py /seed_provider.py
COPY seed_settings.py /seed_settings.py
COPY db_url.py /db_url.py
RUN chmod a+x /run.sh

# All persistent state lives on the Home Assistant /data volume. HOME points
# there too so pip --user installs, HuggingFace downloads and other caches that
# land under $HOME survive restarts and are included in add-on backups.
ENV ODYSSEUS_DATA_DIR=/data \
    HOME=/data \
    APP_PORT=7000 \
    PYTHONUNBUFFERED=1 \
    PATH="/data/.local/bin:${PATH}"

ENTRYPOINT ["/run.sh"]
