FROM docker.io/searxng/searxng:2026.5.31-7159b8aed

USER root

COPY settings.yml /tmp/searxng-settings.yml.template
COPY entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# The official searxng image runs as the non-root `searxng` user.
# Our entrypoint runs as root so it can write /etc/searxng/settings.yml
# on first boot, then exec the original entrypoint which drops privs.
ENTRYPOINT ["/docker-entrypoint.sh"]
