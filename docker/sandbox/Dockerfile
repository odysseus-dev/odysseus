# Tier-B sandbox runner: a minimal, locked-down container that executes the
# agent's bash/python tool calls in isolation. See
# docs/security/bash-python-sandbox-scoping.md.
#
# Isolation is declared in docker-compose.yml (network_mode:none, read_only,
# tmpfs /work, cap_drop ALL, no-new-privileges, no secret/data/.ssh mounts,
# pids/mem/cpu limits). This image stays tiny: an interpreter, a shell, coreutils.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash coreutils ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY runner.py /runner.py

ENV RUNNER_SOCK=/ipc/runner.sock \
    RUNNER_WORKDIR=/work \
    PYTHONUNBUFFERED=1

# Runs as root, but inside a container with ALL capabilities dropped,
# no-new-privileges, a read-only rootfs, no network, and nothing sensitive
# mounted — so root here has effectively no reach. Root is used only so the
# server can create the socket in the shared (root-owned) /ipc volume; a future
# hardening can move to a non-root user with pre-chowned volume ownership.
ENTRYPOINT ["python", "/runner.py"]
