"""Configuración central de logging estructurado con `structlog`.

En desarrollo (`NODE_ENV=development`) los logs se pintan en consola con
formato legible. En producción salen como JSON, listos para recopiladores
de logs (CloudWatch, Datadog, ELK, etc.).
"""

import logging
import sys

import structlog


def configure_logging(node_env: str = "development") -> None:
    """Configura `structlog` y el logging estándar de Python.

    - development: consola coloreada y legible.
    - production: líneas JSON compactas.
    """
    is_dev = node_env == "development"

    shared_processors = [
        structlog.stdlib.filter_by_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    if is_dev:
        processors = shared_processors + [structlog.dev.ConsoleRenderer(colors=True)]
    else:
        processors = shared_processors + [structlog.processors.JSONRenderer()]

    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Devuelve un logger `structlog` identificado por `name`."""
    return structlog.get_logger(name)
