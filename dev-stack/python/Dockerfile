# Python development container — Python 3.12 + build tooling + uv (fast pip).
# Built by docker-compose.dev.yml (service: python). Your code is bind-mounted
# at /workspace; the pip cache is persisted in a named volume.
FROM python:3.12-slim

ARG DEBIAN_FRONTEND=noninteractive

# Build essentials so packages with C extensions (numpy, psycopg, etc.) compile,
# plus git/curl for general dev. Postgres/Mongo/Redis/Kafka client libraries are
# left to your per-project requirements.txt — install them inside /workspace.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        git \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --upgrade pip uv

# Non-root dev user (uid 1000) so files in the bind-mounted /workspace stay
# editable from the host. Pre-create the pip cache dir owned by dev.
RUN useradd -m -u 1000 -s /bin/bash dev \
    && mkdir -p /home/dev/.cache/pip /workspace \
    && chown -R dev:dev /home/dev /workspace

USER dev
WORKDIR /workspace

RUN python --version && pip --version && uv --version

CMD ["sleep", "infinity"]
