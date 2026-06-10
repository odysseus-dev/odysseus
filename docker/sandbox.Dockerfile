# Isolated code-execution sandbox for Odysseus.
# Ubuntu 22.04 with Python 3 and Node.js, used to run untrusted snippets in an
# ephemeral container (see src/sandbox_manager.py). No Odysseus code lives here;
# the host mounts the snippet read-only at /sandbox and runs it as an
# unprivileged user.
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Python 3, pip, and Node.js 20 (NodeSource) + npm. Cleaned up to keep the image
# small.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        ca-certificates \
        curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Unprivileged user; snippets never run as root.
RUN useradd --create-home --uid 1000 sandbox

USER sandbox
WORKDIR /sandbox

CMD ["python3", "--version"]
