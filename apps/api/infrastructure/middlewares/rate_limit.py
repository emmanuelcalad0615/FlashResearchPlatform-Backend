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

from apps.api.config import RateLimitRule
from apps.api.infrastructure.logging import get_logger
from apps.api.infrastructure.middlewares.error_handlers import build_error_response
from packages.core.domain.errors import RateLimitError
from packages.core.infrastructure.cache.redis_client import get_redis

logger = get_logger(__name__)

LIMIT_HEADER = "X-RateLimit-Limit"
REMAINING_HEADER = "X-RateLimit-Remaining"
RETRY_AFTER_HEADER = "Retry-After"

KEY_PREFIX = "ratelimit"

UNKNOWN_CLIENT = "unknown"

# Nombre del contador del limite general, el que se aplica cuando ninguna regla
# casa. Va en la clave igual que el de las reglas, para que todos los
# contadores tengan la misma forma.
DEFAULT_SCOPE = "default"


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
        rules: tuple[RateLimitRule, ...] = (),
        redis_factory: Callable[[], Redis] = get_redis,
    ) -> None:
        super().__init__(app)
        self.limit = limit
        self.window_seconds = window_seconds
        self.exempt_paths = exempt_paths
        # El orden se respeta: la primera regla que casa gana.
        self.rules = rules
        # Inyectable para poder testear contra un Redis falso, sin red.
        self._redis_factory = redis_factory

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path in self.exempt_paths:
            return await call_next(request)

        identity = client_identity(request)
        limit, window_seconds, scope = self._rule_for(request.url.path)
        # El scope separa los contadores: el cupo de login no se gasta
        # navegando por el dashboard, ni al reves.
        key = f"{KEY_PREFIX}:{scope}:{identity}"

        try:
            count, ttl = await self._register_hit(key, window_seconds)
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

        if count > limit:
            retry_after = max(ttl, 1)
            logger.warning(
                "rate_limit_exceeded",
                client=identity,
                path=request.url.path,
                scope=scope,
                count=count,
                limit=limit,
            )
            return build_error_response(
                request,
                429,
                RateLimitError.code,
                RateLimitError.default_message,
                details={"limit": limit, "window_seconds": window_seconds},
                headers={
                    LIMIT_HEADER: str(limit),
                    REMAINING_HEADER: "0",
                    # Sin esto el cliente reintenta a ciegas y empeora la
                    # congestion; con esto espera exactamente lo necesario.
                    RETRY_AFTER_HEADER: str(retry_after),
                },
            )

        response = await call_next(request)
        response.headers[LIMIT_HEADER] = str(limit)
        response.headers[REMAINING_HEADER] = str(max(limit - count, 0))
        return response

    def _rule_for(self, path: str) -> tuple[int, int, str]:
        """El limite que le toca a esta ruta: (peticiones, segundos, scope).

        Gana la PRIMERA regla cuyo prefijo case, no la mas larga ni la mas
        especifica. Es una decision consciente: hace el resultado predecible
        leyendo la configuracion de arriba abajo, a cambio de que el orden
        importe. Un prefijo generico escrito antes tapa a uno mas concreto.

        Sin ninguna coincidencia se aplica el limite general.
        """
        for rule in self.rules:
            if path.startswith(rule.prefix):
                return rule.limit, rule.window_seconds, rule.scope

        return self.limit, self.window_seconds, DEFAULT_SCOPE

    async def _register_hit(self, key: str, window_seconds: int) -> tuple[int, int]:
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
        pipe.expire(key, window_seconds, nx=True)
        pipe.ttl(key)
        count, _, ttl = await pipe.execute()
        return int(count), int(ttl)
