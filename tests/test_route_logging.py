import logging

from src.logging_config import configure_route_logging


class _RecordsHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def test_routes_logger_propagates_warning_to_root_handler():
    root = logging.getLogger()
    routes_logger = logging.getLogger("routes")
    original = (routes_logger.disabled, routes_logger.level, routes_logger.propagate)
    handler = _RecordsHandler()
    root.addHandler(handler)
    try:
        # Reproduce an existing server logging configuration that leaves the
        # application route namespace disabled and non-propagating.
        routes_logger.disabled = True
        routes_logger.setLevel(logging.CRITICAL)
        routes_logger.propagate = False

        configure_route_logging()
        logging.getLogger("routes.misumi_routes").warning("Misumi route warning")

        assert [record.getMessage() for record in handler.records] == ["Misumi route warning"]
    finally:
        root.removeHandler(handler)
        routes_logger.disabled, routes_logger.level, routes_logger.propagate = original
