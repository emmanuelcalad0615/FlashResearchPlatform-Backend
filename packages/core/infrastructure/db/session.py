import os

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

load_dotenv()

def _async_url() -> str:
    """DATABASE_URL del entorno, forzando el driver async (asyncpg).

    Alembic usa el driver sync (psycopg2); la API y el worker usan async.

    No hay valor por defecto: una configuracion critica ausente debe fallar de
    inmediato, no caer a una base de desarrollo que en produccion seria la
    equivocada o inexistente.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL no esta definida. Copia .env.example a .env, "
            "o exportala en el entorno."
        )
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


# Engine global: mantiene el pool de conexiones a Postgres.
engine = create_async_engine(_async_url(), pool_pre_ping=True)

# Factory de sesiones async. expire_on_commit=False: los objetos siguen
# usables tras commit (no re-consulta la base para leer sus atributos).
SessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)
