import os

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

load_dotenv()

_DEFAULT_URL = "postgresql://flash:flash_dev_pw@localhost:5432/flash_research"


def _async_url() -> str:
    """DATABASE_URL del entorno, forzando el driver async (asyncpg).

    Alembic usa el driver sync (psycopg2); la API y el worker usan async.
    """
    url = os.environ.get("DATABASE_URL", _DEFAULT_URL)
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
