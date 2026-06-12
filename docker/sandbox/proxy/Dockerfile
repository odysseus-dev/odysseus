# Tier-C egress allowlist proxy (opt-in). The only door to the internet for the
# sandbox when egress is enabled; default-deny, allowlist via PROXY_ALLOWLIST.
# See docs/security/bash-python-sandbox-scoping.md (Tier C).
FROM python:3.12-slim

COPY proxy.py /proxy.py

RUN useradd --uid 65532 --no-create-home --shell /usr/sbin/nologin proxyusr

ENV PROXY_PORT=8888 \
    PYTHONUNBUFFERED=1

USER 65532:65532

ENTRYPOINT ["python", "/proxy.py"]
