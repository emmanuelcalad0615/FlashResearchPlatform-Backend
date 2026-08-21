"""Rate limiting por IP, con contador compartido en Redis.

Algoritmo: ventana fija. Un contador por cliente que se autodestruye al vencer
la ventana. Dos comandos de Redis y nada de estado en memoria del proceso: con
varios workers de uvicorn, un contador local daria un limite N veces mas alto.

Su defecto conocido es el borde de la ventana (60 peticiones al final de un
minuto y 60 al principio del siguiente = 120 en un instante). Se acepta a
cambio de la simplicidad: 120 peticiones en un segundo no tumban nada.
"""

from collections.abc import Awaitable, Callable

from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from apps.api.core.error_handlers import build_error_response
from apps.api.core.logging import get_logger
from packages.core.errors import RateLimitError
from packages.core.redis_client import get_redis

logger = get_logger(__name__)

LIMIT_HEADER = "X-RateLimit-Limit"
REMAINING_HEADER = "X-RateLimit-Remaining"
RETRY_AFTER_HEADER = "Retry-After"

KEY_PREFIX = "ratelimit"

UNKNOWN_CLIENT = "unknown"


def client_identity(request: Request) -> str:
    """Con quien se lleva la cuenta.

    Se usa la IP del socket, NO la cabecera X-Forwarded-For: esa la puede
    falsificar cualquiera y bastaria cambiarla en cada peticion para esquivar
    el limite. Al desplegar detras de un proxy hay que leerla, pero solo tras
    configurar explicitamente en cuales proxies se confia.
    """
    return request.client.host if request.client else UNKNOWN_CLIENT


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Corta al cliente que se pasa del cupo, con 429 y Retry-After."""

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        *,
        limit: int,
        window_seconds: int,
        exempt_paths: frozenset[str] = frozenset(),
        redis_factory: Callable[[], Redis] = get_redis,
    ) -> None:
        super().__init__(app)
        self.limit = limit
        self.window_seconds = window_seconds
        self.exempt_paths = exempt_paths
        # Inyectable para poder testear contra un Redis falso, sin red.
        self._redis_factory = redis_factory

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path in self.exempt_paths:
            return await call_next(request)

        identity = client_identity(request)
        key = f"{KEY_PREFIX}:{identity}"

        try:
            count, ttl = await self._register_hit(key)
        except RedisError as exc:
            # FAIL OPEN a proposito: el rate limiter es una proteccion, no una
            # funcion esencial. Que Redis se caiga no debe tumbar la API entera;
            # se prefiere un rato sin limite a un apagon total.
            logger.warning(
                "rate_limit_unavailable",
                error=type(exc).__name__,
                path=request.url.path,
            )
            return await call_next(request)

        if count > self.limit:
            retry_after = max(ttl, 1)
            logger.warning(
                "rate_limit_exceeded",
                client=identity,
                path=request.url.path,
                count=count,
                limit=self.limit,
            )
            return build_error_response(
                request,
                429,
                RateLimitError.code,
                RateLimitError.default_message,
                details={"limit": self.limit, "window_seconds": self.window_seconds},
                headers={
                    LIMIT_HEADER: str(self.limit),
                    REMAINING_HEADER: "0",
                    # Sin esto el cliente reintenta a ciegas y empeora la
                    # congestion; con esto espera exactamente lo necesario.
                    RETRY_AFTER_HEADER: str(retry_after),
                },
            )

        response = await call_next(request)
        response.headers[LIMIT_HEADER] = str(self.limit)
        response.headers[REMAINING_HEADER] = str(max(self.limit - count, 0))
        return response

    async def _register_hit(self, key: str) -> tuple[int, int]:
        """Suma una peticion al contador. Devuelve (total, segundos restantes).

        INCR es atomico: con peticiones concurrentes la cuenta sigue siendo
        correcta, cosa que un contador en Python o un SELECT+UPDATE no dan.

        El EXPIRE lleva nx=True para fijar el vencimiento solo la primera vez.
        Sin eso, cada peticion reiniciaria la ventana y bajo carga constante el
        contador no venceria nunca: el cliente quedaria bloqueado para siempre.
        """
        client = self._redis_factory()
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.expire(key, self.window_seconds, nx=True)
        pipe.ttl(key)
        count, _, ttl = await pipe.execute()
        return int(count), int(ttl)
