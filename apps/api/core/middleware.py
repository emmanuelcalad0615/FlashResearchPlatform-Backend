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

from apps.api.core.error_handlers import unhandled_exception_handler
from apps.api.core.logging import (
    REQUEST_ID_HEADER,
    bind_request_id,
    clear_request_id,
    get_logger,
)

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
        # Queda disponible para los endpoints y para los handlers de error, que
        # lo devuelven en el cuerpo.
        request.state.request_id = request_id

        start = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception as exc:
                # La excepcion se atiende AQUI en vez de relanzarse. Si subiera,
                # la atenderia ServerErrorMiddleware, que Starlette monta por
                # FUERA de CORSMiddleware: la respuesta saldria sin cabeceras
                # CORS, el navegador la bloquearia, y el frontend nunca podria
                # leer el 500 ni el request_id que necesita para reportarlo.
                #
                # El middleware sigue sin decidir el formato: delega en el mismo
                # handler que registra register_error_handlers, que ademas es
                # quien loguea el stacktrace.
                response = await unhandled_exception_handler(request, exc)

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
