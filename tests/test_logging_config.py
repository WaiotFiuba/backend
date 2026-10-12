import logging

from app.core.logging_config import APP_LOGGER_NAME, configure_logging


def test_configure_logging_sets_level_and_a_single_handler():
    logger = logging.getLogger(APP_LOGGER_NAME)
    previous = (logger.level, list(logger.handlers), logger.propagate)
    try:
        logger.handlers.clear()

        configure_logging("debug")
        configure_logging("warning")  # idempotente: no duplica handlers

        assert logger.level == logging.WARNING
        assert len(logger.handlers) == 1
        assert logger.propagate is False
        # Los módulos del backend (app.services...) heredan esta configuración.
        assert logging.getLogger("app.services.x").getEffectiveLevel() == (
            logging.WARNING
        )
    finally:
        logger.level, logger.handlers[:], logger.propagate = previous
