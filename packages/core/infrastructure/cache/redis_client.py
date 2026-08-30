"""Cliente Redis compartido por la API y el worker.

Sigue el mismo patron que db/session.py: lee la configuracion del entorno con
os.environ, no de apps.api.config, porque packages/core es la capa de
abajo y no puede importar hacia arriba.
"""

import os

from dotenv import load_dotenv
from redis.asyncio import Redis

load_dotenv()

_DEFAULT_URL = "redis://localhost:6379/0"

# Cliente global: el pool de conexiones se reusa entre peticiones. Abrir una
# conexion nueva por peticion desperdiciaria el handshake TCP cada vez.
_client: Redis | None = None


def get_redis() -> Redis:
    """Cliente Redis del proceso. Se crea perezosamente en el primer uso."""
    global _client
    if _client is None:
        _client = Redis.from_url(
            os.environ.get("REDIS_URL", _DEFAULT_URL),
            decode_responses=True,
            # Falla rapido en vez de colgar la peticion si Redis no responde:
            # quien lo use decide que hacer (el rate limiter deja pasar).
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )
    return _client


async def close_redis() -> None:
    """Cierra el cliente. Llamar al apagar el proceso."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
