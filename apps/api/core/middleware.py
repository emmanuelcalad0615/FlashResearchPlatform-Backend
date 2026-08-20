"""Middleware transversal de la API.

RequestIDMiddleware le asigna un identificador unico a cada peticion HTTP y
registra como termino. Todo lo que se loguee mientras esa peticion esta viva
lleva el identificador pegado, asi se puede reconstruir su historia completa
filtrando los logs por un solo valor.
"""

import re
import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from apps.api.core.logging import bind_request_id, clear_request_id, get_logger

REQUEST_ID_HEADER = "X-Request-ID"

# El header entrante lo controla el cliente, asi que no se acepta tal cual:
# un valor con saltos de linea podria inyectar lineas falsas en los logs, y uno
# sin limite de tamano inflaria cada evento. Solo pasan ids cortos e inocuos.
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

logger = get_logger(__name__)


def resolve_request_id(raw: str | None) -> str:
    """Reusa el id que mando el cliente si es seguro; si no, genera uno nuevo."""
    if raw and _SAFE_REQUEST_ID.fullmatch(raw):
        return raw
    return str(uuid.uuid4())


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Asigna el request_id, mide la peticion y la registra."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = resolve_request_id(request.headers.get(REQUEST_ID_HEADER))
        bind_request_id(request_id)
        # Queda disponible para los endpoints y, mas adelante, para los
        # handlers de error que lo devuelven en el cuerpo.
        request.state.request_id = request_id

        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # .exception() agrega el stacktrace. Se relanza para que el manejo
            # de errores centralizado decida que responder: aqui solo se observa.
            logger.exception(
                "http_request_failed",
                method=request.method,
                path=request.url.path,
                duration_ms=_elapsed_ms(start),
            )
            raise
        else:
            logger.info(
                "http_request",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=_elapsed_ms(start),
            )
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            # Siempre, incluso si hubo excepcion: el contexto es por tarea y no
            # debe filtrarse a la siguiente peticion que reuse el hilo.
            clear_request_id()
