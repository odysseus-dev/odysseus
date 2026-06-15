ARG PYTHON_BASE=python:3.14-slim
FROM ${PYTHON_BASE}

ARG ODYSSEUS_LLAMA_CPP_CUDA=
ARG ODYSSEUS_LLAMA_CPP_CUDA_FLAVOR=auto

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
    ca-certificates \
    cmake \
    curl \
    git \
    nodejs \
    npm \
    tmux \
    openssh-client \
    gosu \
    && rm -rf /var/lib/apt/lists/*

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Optional CUDA build toolchain for Cookbook's native llama.cpp bootstrap.
# Enabled by the NVIDIA compose overlay when ODYSSEUS_LLAMA_CPP_CUDA=ON is set.
# auto chooses CUDA 12.4 on Debian 12/bookworm and CUDA 13.3 on Debian 13/trixie.
RUN _cuda_opt="${ODYSSEUS_LLAMA_CPP_CUDA^^}" \
    && _cuda_flavor="${ODYSSEUS_LLAMA_CPP_CUDA_FLAVOR:-auto}" \
    && _cuda_flavor="${_cuda_flavor,,}" \
    && if [ "$_cuda_opt" = "ON" ] || [ "$_cuda_opt" = "1" ] || [ "$_cuda_opt" = "TRUE" ]; then \
        if [ "$(dpkg --print-architecture)" != "amd64" ]; then \
            echo "ODYSSEUS_LLAMA_CPP_CUDA=ON currently supports amd64 images only." >&2; exit 1; \
        fi; \
        . /etc/os-release \
        && case "$_cuda_flavor" in \
            ""|auto) \
                case "${VERSION_CODENAME:-}" in \
                    bookworm) _cuda_repo=debian12; _cuda_pkgs=(cuda-nvcc-12-4 cuda-cudart-dev-12-4 libcublas-dev-12-4); _cuda_cudart_major=12 ;; \
                    trixie) _cuda_repo=debian13; _cuda_pkgs=(cuda-nvcc-13-3 cuda-cudart-dev-13-3 libcublas-dev-13-3); _cuda_cudart_major=13 ;; \
                    *) echo "ODYSSEUS_LLAMA_CPP_CUDA=ON supports Debian bookworm/trixie bases only (got ${VERSION_CODENAME:-unknown})." >&2; exit 1 ;; \
                esac ;; \
            cuda12|cuda12-bookworm|12|12.4|12-4) \
                if [ "${VERSION_CODENAME:-}" != "bookworm" ]; then \
                    echo "ODYSSEUS_LLAMA_CPP_CUDA_FLAVOR=$_cuda_flavor requires python:3.12-slim-bookworm." >&2; exit 1; \
                fi; \
                _cuda_repo=debian12; _cuda_pkgs=(cuda-nvcc-12-4 cuda-cudart-dev-12-4 libcublas-dev-12-4); _cuda_cudart_major=12 ;; \
            cuda13|cuda13-trixie|13|13.3|13-3) \
                if [ "${VERSION_CODENAME:-}" != "trixie" ]; then \
                    echo "ODYSSEUS_LLAMA_CPP_CUDA_FLAVOR=$_cuda_flavor requires python:3.12-slim-trixie." >&2; exit 1; \
                fi; \
                _cuda_repo=debian13; _cuda_pkgs=(cuda-nvcc-13-3 cuda-cudart-dev-13-3 libcublas-dev-13-3); _cuda_cudart_major=13 ;; \
            *) echo "Unsupported ODYSSEUS_LLAMA_CPP_CUDA_FLAVOR=$_cuda_flavor. Use auto, cuda12-bookworm, or cuda13-trixie." >&2; exit 1 ;; \
        esac \
        && echo "Installing NVIDIA CUDA build packages from $_cuda_repo: ${_cuda_pkgs[*]}" \
        && curl -fsSL -o /tmp/cuda-keyring.deb \
            "https://developer.download.nvidia.com/compute/cuda/repos/${_cuda_repo}/x86_64/cuda-keyring_1.1-1_all.deb" \
        && dpkg -i /tmp/cuda-keyring.deb \
        && rm -f /tmp/cuda-keyring.deb \
        && apt-get update \
        && apt-get install -y --no-install-recommends "${_cuda_pkgs[@]}" \
        && rm -rf /var/lib/apt/lists/* \
        && /usr/local/cuda/bin/nvcc --version \
        && test -e "/usr/local/cuda/lib64/libcudart.so.${_cuda_cudart_major}"; \
    fi

ENV CUDA_HOME=/usr/local/cuda
ENV PATH=/usr/local/cuda/bin:${PATH}
ENV LD_LIBRARY_PATH=/usr/local/cuda/lib64

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
