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

FROM python:3.14-slim

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
#   - nodejs/npm/chromium: the built-in Browser MCP server
#   - tmux: Cookbook background downloads and model serves
#   - openssh-client: Cookbook remote server support
#   - libgl1/libglib2.0-0t64/libxcb1: runtime libs opencv-python (cv2) loads
#   - libmagic1: content-based MIME sniffing in src/upload_handler.py
#   - jq: parses /data/options.json in run.sh
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        curl \
        git \
        jq \
        nodejs \
        npm \
        chromium \
        tmux \
        openssh-client \
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

# Python dependencies first so this layer caches across source changes.
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir python-magic==0.4.27

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
    PYTHONUNBUFFERED=1

ENTRYPOINT ["/run.sh"]