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

# Non-root runner. The /ipc and /work mount points are created and chowned to the
# runner user at BUILD time; when the shared `sandbox-ipc` named volume is first
# mounted (empty), Docker initialises it from this directory's ownership, so the
# non-root user can create the socket there without any runtime chown (which
# cap_drop:ALL would forbid). 0755 on /ipc lets the app container's user traverse
# to connect to the 0666 socket.
RUN useradd --uid 65532 --no-create-home --shell /usr/sbin/nologin sandbox \
    && mkdir -p /ipc /work \
    && chown sandbox:sandbox /ipc /work \
    && chmod 0755 /ipc /work

ENV RUNNER_SOCK=/ipc/runner.sock \
    RUNNER_WORKDIR=/work \
    PYTHONUNBUFFERED=1

USER 65532:65532

ENTRYPOINT ["python", "/runner.py"]
