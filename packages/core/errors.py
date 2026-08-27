"""Errores de dominio de Flash Research.

Python puro: no importa FastAPI, ni HTTP, ni SQLAlchemy. Lo comparten la API y
el worker, porque ambos lanzan errores de negocio. La traduccion a codigos de
estado HTTP vive en la capa de API (apps/api/core/error_handlers.py): el
dominio no sabe que existe HTTP.
"""


class AppError(Exception):
    """Base de todo error esperado de la aplicacion.

    'Esperado' = lo lanza el codigo a proposito. Un bug NO es un AppError: ese
    lo atrapa la red de seguridad del handler generico.
    """

    # Atributos de clase: las subclases los sobrescriben sin escribir __init__.
    code: str = "internal_error"
    default_message: str = "An unexpected error occurred"

    def __init__(self, message: str | None = None, *, details: dict | None = None) -> None:
        self.message = message or self.default_message
        # Nunca None: el handler lee .details sin preguntar.
        self.details = details or {}
        # Sin esto str(exc) devuelve "" y los logs salen mudos.
        super().__init__(self.message)


class NotFoundError(AppError):
    """El recurso pedido no existe."""

    code = "not_found"
    default_message = "Resource not found"


class ConflictError(AppError):
    """El recurso ya existe o el estado actual choca con la operacion."""

    code = "conflict"
    default_message = "Conflicting resource state"


class UnauthorizedError(AppError):
    """No hay credenciales, o son invalidas."""

    code = "unauthorized"
    default_message = "Authentication required"


class ForbiddenError(AppError):
    """Hay credenciales validas, pero no alcanzan para esta operacion."""

    code = "forbidden"
    default_message = "Insufficient permissions"


class DomainValidationError(AppError):
    """Los datos violan una regla de negocio.

    Nombre a proposito distinto de ValidationError: Pydantic ya exporta ese y
    los dos en el mismo codigo serian imposibles de distinguir al leerlo.
    """

    code = "validation_error"
    default_message = "The request violates a business rule"


class ExternalServiceError(AppError):
    """Fallo un proveedor externo (Polygon, etc.).

    La lanzan los adapters para que ni el worker ni la API tengan que conocer
    la libreria HTTP concreta que hay detras del puerto.
    """

    code = "external_service_error"
    default_message = "An upstream provider failed"


class RateLimitError(AppError):
    """El cliente hizo demasiadas peticiones."""

    code = "rate_limited"
    default_message = "Too many requests"


class InvalidCredentialsError(AppError):
    """Email inexistente o contrasena incorrecta.

    UN SOLO error para los dos casos, a proposito. Distinguirlos le permitiria
    a un atacante averiguar que emails estan registrados (user enumeration).
    """

    code = "invalid_credentials"
    default_message = "Invalid email or password"


class EmailNotVerifiedError(AppError):
    """La cuenta existe y la contrasena es correcta, pero falta verificar el email.

    Excepcion deliberada a la regla de arriba: quien llega aqui ya demostro que
    sabe la contrasena, asi que decirselo no filtra nada nuevo, y necesita saber
    que tiene que revisar su correo.
    """

    code = "email_not_verified"
    default_message = "Verify your email address before signing in"


class InvalidTokenError(AppError):
    """Token malformado, desconocido, ya usado o revocado."""

    code = "invalid_token"
    default_message = "The token is not valid"


class TokenExpiredError(AppError):
    """Token bien formado pero fuera de su ventana de validez.

    Separado de InvalidTokenError porque el cliente reacciona distinto: ante un
    access token expirado pide un refresh; ante uno invalido, manda al login.
    """

    code = "token_expired"
    default_message = "The token has expired"
