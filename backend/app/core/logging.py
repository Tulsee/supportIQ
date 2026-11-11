"""Logging setup for the ``app.*`` logger namespace.

Uvicorn configures its own loggers; this only gives application modules
(``logging.getLogger(__name__)``) a handler and level so their output shows up
alongside uvicorn's. Structured/JSON logging can replace the formatter later
without touching call sites.
"""

import logging

LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"


def configure_logging(level: str = "INFO") -> None:
    app_logger = logging.getLogger("app")
    app_logger.setLevel(level.upper())
    if not app_logger.handlers:  # idempotent: create_app() may run more than once (tests)
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        app_logger.addHandler(handler)
