"""Configuración de logs de la aplicación.

Sin esto, Python solo muestra mensajes de nivel WARNING o mayor y los
logger.info del backend no aparecen en ningún lado. Se configura únicamente el
logger "app" (y sus hijos, como "app.services..."), sin tocar los de uvicorn.
"""

from __future__ import annotations

import logging

APP_LOGGER_NAME = "app"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(level: str) -> None:
    logger = logging.getLogger(APP_LOGGER_NAME)
    logger.setLevel(level.upper())
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        logger.addHandler(handler)
    # El handler propio ya imprime; propagar al root duplicaría los mensajes si
    # algún otro componente lo configura.
    logger.propagate = False
