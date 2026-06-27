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
    libgl1 \
    libglib2.0-0t64 \
    libxcb1 \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# libgl1/libglib2.0-0t64/libxcb1 are runtime shared libs (libGL.so.1,
# libglib-2.0/libgthread, libxcb.so.1) that opencv-python (cv2) loads. The
# slim base omits them, so the Cookbook "install realesrgan" path imports cv2
# and dies with `libxcb.so.1: cannot open shared object file` despite a clean
# pip install. Using full opencv-python (not -headless) because basicsr/gfpgan/
# facexlib/realesrgan all depend on the `opencv-python` distribution by name.
#
# libmagic1 is the shared lib (libmagic.so.1) that python-magic dlopens for
# content-based MIME sniffing in src/upload_handler.py. We install both here
# (libmagic1 + the python-magic wrapper, below) rather than in requirements.txt
# because python-magic resolves libmagic at import time: where the lib is
# absent the import can block or raise, so keeping it image-only avoids
# regressing pip/venv installs on hosts without libmagic. Debian always has the
# lib here, so the import is instant and detection actually works.
# Docker CLI (client only — daemon stays on the host via the
# /var/run/docker.sock mount). The Debian `docker.io` package ships
# dockerd but not the client binary on slim, so grab the static client
# tarball from download.docker.com instead.
ARG DOCKER_CLI_VERSION=27.5.1
RUN ARCH="$(dpkg --print-architecture)" \
    && case "$ARCH" in \
         amd64) DARCH=x86_64 ;; \
         arm64) DARCH=aarch64 ;; \
         *) echo "unsupported arch $ARCH"; exit 1 ;; \
       esac \
    && curl -fsSL "https://download.docker.com/linux/static/stable/${DARCH}/docker-${DOCKER_CLI_VERSION}.tgz" \
       -o /tmp/docker.tgz \
    && tar -xzf /tmp/docker.tgz -C /tmp \
    && install -m 0755 /tmp/docker/docker /usr/local/bin/docker \
    && rm -rf /tmp/docker /tmp/docker.tgz

WORKDIR /app

# Install Python deps first (layer cache). Optional extras (PyMuPDF AGPL, etc.)
# are opt-in so the default image stays MIT-core; see requirements-optional.txt.
ARG INSTALL_OPTIONAL=false
COPY requirements.txt requirements-optional.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$INSTALL_OPTIONAL" = "true" ]; then pip install --no-cache-dir -r requirements-optional.txt; fi

# python-magic powers content-based MIME sniffing in src/upload_handler.py.
# Image-only (not in requirements.txt) because it needs the libmagic1 system
# lib installed above.
RUN pip install --no-cache-dir python-magic==0.4.27

# Pre-bake the FastEmbed models so the container doesn't need
# network egress (HuggingFace) at first chat/embedding request. Without
# this step, the slim image fails the first VectorRAG init on hosts
# where HF is blocked, leaving RAG in DEGRADED state forever. The
# models land in /app/.cache/fastembed, which is the same path the
# running app will use because FASTEMBED_CACHE_PATH is exported below
# in the same layer. The entrypoint chowns the whole /app tree to
# PUID:PGID at first boot (root-created files become readable by the
# non-root app user), so the bake is reachable on first request.
#
# We only need to bake one model: `sentence-transformers/all-MiniLM-L6-v2`.
# That's the model name `src/embeddings.py` passes to TextEmbedding, AND
# it's fastembed's library default — so `src/rag_vector.py` and
# `src/memory_vector.py` (which call `fastembed.TextEmbedding()` with
# no model_name arg) also pick this same model. (Internally fastembed
# renames this entry to the qdrant/all-MiniLM-L6-v2-onnx HF repo to
# download, but you can NOT pass `qdrant/*` to TextEmbedding — it's
# not in `list_supported_models()` and raises ValueError.)
#
# Set FASTEMBED_MODEL_BAKE=false in the build to skip (saves ~95 MB
# and 1-2 min in CI when the host definitely has HF access at runtime
# and a smaller image is preferred).
#
# Implementation note: the bake lives in a separate script file
# (docker/bake-fastembed.sh) to avoid the multi-level escaping trap
# that inline heredoc-style RUN commands fall into with bash -c nested
# inside sh -c.
COPY docker/bake-fastembed.sh /usr/local/bin/bake-fastembed.sh
RUN chmod +x /usr/local/bin/bake-fastembed.sh
ARG FASTEMBED_MODEL_BAKE=true
RUN if [ "$FASTEMBED_MODEL_BAKE" = "true" ]; then \
      FASTEMBED_CACHE_PATH=/app/.cache/fastembed \
      HF_HUB_DISABLE_SYMLINKS=1 \
      /usr/local/bin/bake-fastembed.sh; \
    fi

# Tell the running app where to find the pre-baked models. FastEmbed
# reads FASTEMBED_CACHE_PATH at instantiation time; without this the
# runtime default would be /tmp/fastembed_cache (empty), and the bake
# at /app/.cache/fastembed would be ignored.
ENV FASTEMBED_CACHE_PATH=/app/.cache/fastembed

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
