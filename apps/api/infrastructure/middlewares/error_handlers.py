"""Manejo centralizado de errores de la API.

Un solo lugar decide como se ve TODO error que sale de la API. Los endpoints y
el dominio lanzan excepciones; aqui se traducen a HTTP. Ningun router arma
respuestas de error a mano.

Formato unico:
    {"error": {"code": ..., "message": ..., "details": {...}, "request_id": ...}}

`code` es el contrato con el frontend: snake_case, estable, legible por maquina.
`message` es para humanos y puede cambiar sin romper a nadie.
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from apps.api.infrastructure.logging import REQUEST_ID_HEADER, get_logger, get_request_id
from packages.core.domain.errors import (
    AppError,
    ConflictError,
    DomainValidationError,
    EmailDeliveryError,
    EmailNotVerifiedError,
    ExternalServiceError,
    ForbiddenError,
    InvalidCredentialsError,
    InvalidTokenError,
    NotFoundError,
    RateLimitError,
    TokenExpiredError,
    UnauthorizedError,
)

logger = get_logger(__name__)

# Traduccion dominio -> HTTP. Vive AQUI y no en packages/core a proposito: el
# 404 es vocabulario de HTTP, y el dominio no conoce HTTP. El worker lanza las
# mismas excepciones sin arrastrar esta tabla.
STATUS_BY_ERROR: dict[type[AppError], int] = {
    NotFoundError: 404,
    ConflictError: 409,
    UnauthorizedError: 401,
    ForbiddenError: 403,
    DomainValidationError: 422,
    RateLimitError: 429,
    ExternalServiceError: 502,
    # Auth
    InvalidCredentialsError: 401,
    EmailNotVerifiedError: 403,
    InvalidTokenError: 400,
    # Un fallo de entrega es culpa de un tercero, no del cliente.
    EmailDeliveryError: 502,
    # 410 Gone y no 401: el token existio y era valido, pero ya no. Le dice al
    # cliente que pida uno nuevo en vez de mandar al usuario al login.
    TokenExpiredError: 410,
}

# Un AppError sin mapear cae aqui. El test test_every_domain_error_has_a_status
# evita que eso pase por olvido.
DEFAULT_STATUS = 500

# Errores que no nacen de AppError (los del router, los bugs) tambien necesitan
# un `code` estable.
CODE_BY_STATUS: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    406: "not_acceptable",
    409: "conflict",
    415: "unsupported_media_type",
    422: "validation_error",
    410: "gone",
    429: "rate_limited",
    500: "internal_error",
    502: "bad_gateway",
    503: "service_unavailable",
}

INTERNAL_ERROR_CODE = "internal_error"
INTERNAL_ERROR_MESSAGE = "An unexpected error occurred"


def status_for(exc: AppError) -> int:
    """Status HTTP de una excepcion de dominio.

    Recorre el MRO para que una subclase futura de NotFoundError herede su 404
    sin tener que registrarse aparte.
    """
    for klass in type(exc).__mro__:
        if klass in STATUS_BY_ERROR:
            return STATUS_BY_ERROR[klass]
    return DEFAULT_STATUS


def _request_id_of(request: Request) -> str:
    """El request_id de la peticion en curso.

    request.state primero: el handler del 500 corre por fuera del middleware,
    cuando el ContextVar ya se limpio, pero el scope sigue siendo el mismo.
    """
    return getattr(request.state, "request_id", "") or get_request_id()


def build_error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Arma el unico formato de error de la API."""
    error: dict[str, Any] = {"code": code, "message": message, "details": details or {}}

    request_id = _request_id_of(request)
    if request_id:
        # Lo que el usuario reporta cuando algo falla: con este id se filtra el
        # log y se reconstruye la peticion completa.
        error["request_id"] = request_id

    response_headers = dict(headers or {})
    if request_id:
        response_headers[REQUEST_ID_HEADER] = request_id

    return JSONResponse(status_code=status_code, content={"error": error}, headers=response_headers)


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Errores de dominio: los esperados, lanzados a proposito."""
    status_code = status_for(exc)
    # 4xx es culpa del cliente (ruido si se loguea como error); 5xx es fallo
    # nuestro o de un tercero, y ese si hay que mirarlo.
    log = logger.warning if status_code < 500 else logger.error
    log(
        "app_error",
        code=exc.code,
        status_code=status_code,
        path=request.url.path,
        details=exc.details,
    )
    return build_error_response(request, status_code, exc.code, exc.message, exc.details)


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Body, query o path que no pasan la validacion de Pydantic."""
    fields = [
        {
            "field": ".".join(str(part) for part in error["loc"]),
            "message": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors()
    ]
    logger.warning("request_validation_failed", path=request.url.path, fields=fields)
    return build_error_response(
        request,
        422,
        "validation_error",
        "Request validation failed",
        {"fields": fields},
    )


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """404 de ruta inexistente, 405 de metodo equivocado, etc.

    Normaliza el {"detail": ...} de Starlette al formato propio.
    """
    code = CODE_BY_STATUS.get(exc.status_code, "http_error")
    message = exc.detail if isinstance(exc.detail, str) else "HTTP error"
    # Algunas HTTPException traen cabeceras con significado (Allow en un 405,
    # WWW-Authenticate en un 401): se conservan.
    return build_error_response(
        request, exc.status_code, code, message, headers=getattr(exc, "headers", None)
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Red de seguridad: cualquier bug no previsto.

    SEGURIDAD: el mensaje real de la excepcion NUNCA sale al cliente. Puede
    contener el connection string de la base, rutas del servidor o datos de
    otro usuario. El detalle completo va al log, donde solo lo ve el equipo.
    """
    logger.exception(
        "unhandled_exception",
        path=request.url.path,
        method=request.method,
        exception_type=type(exc).__name__,
    )
    return build_error_response(
        request, 500, INTERNAL_ERROR_CODE, INTERNAL_ERROR_MESSAGE
    )


def register_error_handlers(app: FastAPI) -> None:
    """Registra los cuatro handlers. Llamar una vez al construir la app."""
    # AppError cubre las 7 subclases: Starlette busca por el MRO de la excepcion.
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
