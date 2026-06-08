# ============================================================
# Stage 1: Builder — installs system build deps and Python packages
# ============================================================
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    curl \
    git \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps into a venv so we can copy it to the final stage.
# Optional extras (PyMuPDF AGPL, etc.) are opt-in so the default image
# stays MIT-core; see requirements-optional.txt.
ARG INSTALL_OPTIONAL=false
COPY requirements.txt requirements-optional.txt ./
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt \
    && if [ "$INSTALL_OPTIONAL" = "true" ]; then /opt/venv/bin/pip install --no-cache-dir -r requirements-optional.txt; fi

# ============================================================
# Stage 2: Runtime — slim image with venv from builder
# ============================================================
FROM python:3.12-slim

# Runtime system deps:
#   build-essential, cmake, git — Cookbook builds llama.cpp from source at
#                                  runtime on Linux (cmake + compiler needed)
#   tmux            — Cookbook background downloads/serves
#   openssh-client  — Cookbook remote server tests/probes
#   gosu            — privilege-dropping entrypoint
#   curl            — health checks, MCP server connectivity
#   nodejs, npm     — optional built-in Browser MCP server (npx)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    curl \
    git \
    gosu \
    nodejs \
    npm \
    openssh-client \
    tmux \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Create writable dirs BEFORE copying code (layer cache)
RUN mkdir -p data logs services/cache/search

# Copy venv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy app code (respects .dockerignore)
COPY . .

# Entrypoint
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f "http://localhost:${APP_PORT:-7000}/api/health" || exit 1

EXPOSE 7000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7000"]
