"""Logging estructurado de la API.

Cada evento sale como una linea JSON (LOG_JSON=true, para produccion) o como
texto coloreado (LOG_JSON=false, para desarrollo). El `request_id` de la
peticion en curso se inyecta solo, via ContextVar, sin pasarlo por parametro
a cada funcion.

Los logs de librerias externas (uvicorn, sqlalchemy) pasan por el mismo
formateador, asi toda la salida del proceso tiene la misma forma.
"""

import logging
import sys
from contextvars import ContextVar
from typing import Any, TextIO

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

from apps.api.core.config import settings

_request_id: ContextVar[str] = ContextVar("request_id", default="")


def bind_request_id(request_id: str) -> None:
    """Fija el request_id de la peticion en curso."""
    _request_id.set(request_id)


def get_request_id() -> str:
    """Devuelve el request_id actual, o cadena vacia fuera de una peticion."""
    return _request_id.get()


def clear_request_id() -> None:
    """Limpia el request_id al terminar la peticion."""
    _request_id.set("")


def add_request_id(
    _logger: WrappedLogger, _method_name: str, event_dict: EventDict
) -> EventDict:
    """Procesador que agrega el request_id a todo evento que se emita."""
    request_id = _request_id.get()
    if request_id:
        event_dict["request_id"] = request_id
    return event_dict


def _build_renderer() -> Processor:
    if settings.log_json:
        return structlog.processors.JSONRenderer()
    return structlog.dev.ConsoleRenderer(colors=True)


def configure_logging(stream: TextIO | None = None) -> None:
    """Configura structlog y el logging estandar. Llamar una vez al arrancar.

    `stream` existe para los tests: permite capturar la salida en memoria.
    """
    level = logging.getLevelNamesMapping().get(settings.log_level.upper(), logging.INFO)

    # Procesadores comunes a nuestros logs y a los de librerias externas.
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        add_request_id,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            # Entrega el evento al formateador de logging estandar en vez de
            # renderizarlo aqui, para que haya un solo punto de renderizado.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    render_chain: list[Processor] = [
        structlog.stdlib.ProcessorFormatter.remove_processors_meta
    ]
    if settings.log_json:
        # ConsoleRenderer formatea las excepciones por su cuenta; JSONRenderer no.
        render_chain.append(structlog.processors.format_exc_info)
    render_chain.append(_build_renderer())

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=render_chain,
    )

    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # uvicorn instala sus propios handlers: los quitamos para no duplicar lineas.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        external = logging.getLogger(name)
        external.handlers = []
        external.propagate = True

    # Su access log es texto plano y repite lo que emite RequestIDMiddleware.
    logging.getLogger("uvicorn.access").disabled = True


def get_logger(name: str | None = None) -> Any:
    """Atajo para obtener un logger ya configurado."""
    return structlog.get_logger(name)
