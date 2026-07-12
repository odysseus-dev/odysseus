"""Small application logging adjustments layered over the server config."""

import logging


def configure_route_logging() -> None:
    """Ensure route warnings reach the application's root logging sink."""
    routes_logger = logging.getLogger("routes")
    routes_logger.disabled = False
    routes_logger.setLevel(logging.WARNING)
    routes_logger.propagate = True
