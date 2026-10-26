# Validation only. Deploy the unmodified upstream image selected by Compose.
ARG SEARXNG_IMAGE
FROM ${SEARXNG_IMAGE} AS validation
COPY scripts/check_ca_trust.py docker/searxng/validate_image.py /opt/odysseus-trust/
COPY tests/fixtures/ca-trust/incident.json /opt/odysseus-trust/incident.json
RUN /usr/local/searxng/.venv/bin/python /opt/odysseus-trust/validate_image.py

FROM validation AS live-validation
RUN /usr/local/searxng/.venv/bin/python /opt/odysseus-trust/validate_image.py --live
