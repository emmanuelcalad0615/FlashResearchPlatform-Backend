import os

# El rate limiting necesita Redis y los tests NO tocan la red (CLAUDE.md).
# Se apaga para la suite general; su comportamiento se prueba aparte en
# test_rate_limit.py, contra un Redis falso en memoria.
# Va antes de que cualquier test importe apps.api.config, que lee el entorno
# al importarse.
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

# ---------------------------------------------------------------------------
# Tests de integracion
#
# Solo se ejecutan con `uv run pytest -m integration`, y solo si hay una base
# de pruebas. Sin ella se OMITEN con un mensaje claro, en vez de fallar con un
# error de conexion de cuarenta lineas.
# ---------------------------------------------------------------------------

# os.environ no ve el .env por si solo: hay que cargarlo.
load_dotenv()

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

# GitHub Actions define CI=true. En local, que falte la base de pruebas es
# aceptable y los tests de integracion se omiten. En CI NO: una suite donde
# todo se omitio se ve identica a una donde todo paso.
_EN_CI = os.environ.get("CI", "").lower() == "true"


def pytest_configure(config) -> None:
    """Aborta si en CI falta la base de pruebas.

    Sin esto, borrar TEST_DATABASE_URL del pipeline dejaria 18 tests omitidos
    en silencio y el CI seguiria en verde.
    """
    if _EN_CI and not TEST_DATABASE_URL:
        pytest.exit(
            "TEST_DATABASE_URL es obligatoria en CI: sin ella los tests de "
            "integracion se omitirian y el pipeline pasaria sin probarlos.",
            returncode=1,
        )


requiere_bd = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason=(
        "falta TEST_DATABASE_URL. Levanta Docker y crea la base:\n"
        '  docker compose exec db psql -U flash -d flash_research '
        '-c "CREATE DATABASE flash_test;"'
    ),
)


# Rol de pruebas SIN privilegios de superusuario. Es lo unico que permite
# verificar que la RLS filtra de verdad: `flash` es superusuario y los
# superusuarios se saltan la RLS incondicionalmente, asi que un test de RLS
# conectado como flash pasaria en falso.
#
# Es el hermano de pruebas del rol `flash_app` que hara falta en produccion
# (ver deployment-decisions.md): los GRANT de aqui son los mismos de alla.
RLS_ROLE = "flash_test_app"
RLS_PASSWORD = "solo-para-tests"  # noqa: S105 - base local de pruebas


def _ensure_rls_role() -> None:
    """Crea el rol de pruebas si no existe. Idempotente.

    Sincrono a proposito (psycopg2, no asyncpg): correrlo fuera del event loop
    evita por completo el lio de bucles que da pytest-asyncio.

    OJO: en Postgres los roles son de todo el CLUSTER, no de una base. Este rol
    queda visible tambien desde flash_research. En un contenedor de desarrollo
    no es problema, pero conviene saberlo.
    """
    import psycopg2

    conexion = psycopg2.connect(TEST_DATABASE_URL)
    conexion.autocommit = True
    try:
        with conexion.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (RLS_ROLE,))
            if cur.fetchone() is None:
                # CREATE ROLE no admite IF NOT EXISTS.
                # NOSUPERUSER NOBYPASSRLS son los valores por defecto, pero se
                # escriben para dejar claro que son EL punto de este rol.
                cur.execute(
                    f"CREATE ROLE {RLS_ROLE} LOGIN PASSWORD %s "
                    f"NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE",
                    (RLS_PASSWORD,),
                )
            cur.execute(f"GRANT USAGE ON SCHEMA public TO {RLS_ROLE}")
            cur.execute(
                f"GRANT SELECT, INSERT, UPDATE, DELETE "
                f"ON ALL TABLES IN SCHEMA public TO {RLS_ROLE}"
            )
    finally:
        conexion.close()


def _rls_url() -> str:
    """La misma base, pero conectandose con el rol restringido."""
    from urllib.parse import urlparse, urlunparse

    partes = urlparse(TEST_DATABASE_URL)
    autoridad = f"{RLS_ROLE}:{RLS_PASSWORD}@{partes.hostname}:{partes.port}"
    return urlunparse(partes._replace(netloc=autoridad))


def _async_url(url: str) -> str:
    """Alembic usa el driver sync; los tests, el async."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


@pytest_asyncio.fixture
async def engine():
    """Un engine por test.

    Lo natural seria uno por sesion, pero pytest-asyncio da a cada test su
    propio event loop y las conexiones de asyncpg quedan atadas al loop donde
    nacieron: reusarlas revienta con "attached to a different loop".

    La alternativa —forzar un unico loop para toda la suite— afectaria tambien
    a los 105 tests rapidos. Con NullPool no se guardan conexiones entre tests,
    asi que crear el engine cuesta poco y no quedan sockets colgando.
    """
    if not TEST_DATABASE_URL:
        pytest.skip("falta TEST_DATABASE_URL")

    motor = create_async_engine(_async_url(TEST_DATABASE_URL), poolclass=NullPool)
    yield motor
    await motor.dispose()


@pytest_asyncio.fixture
async def db_session(engine) -> AsyncSession:
    """Sesion aislada: todo lo que el test escriba se revierte al terminar.

    El codigo bajo prueba llama a commit() —el caso de uso del signup lo hace—,
    y sin mas eso confirmaria la transaccion externa y el rollback de abajo no
    tendria nada que revertir.

    join_transaction_mode="create_savepoint" hace que la sesion trabaje sobre un
    SAVEPOINT anidado:

        BEGIN                      <- esta fixture
          SAVEPOINT                <- la sesion
            ... lo que haga el test ...
          RELEASE SAVEPOINT        <- session.commit() hace esto
        ROLLBACK                   <- esta fixture: se lo lleva todo

    El codigo cree que confirmo, y para lo que le importa asi fue, pero al final
    no persiste nada. Es lo que permite probar codigo transaccional de verdad
    sin ensuciar la base ni depender del orden de los tests.
    """
    async with engine.connect() as conexion:
        transaccion = await conexion.begin()
        sesion = AsyncSession(
            bind=conexion,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )

        yield sesion

        await sesion.close()
        await transaccion.rollback()


@pytest_asyncio.fixture
async def rls_session() -> AsyncSession:
    """Sesion conectada como flash_test_app, SIN privilegios de superusuario.

    Es la unica fixture que puede verificar que la RLS filtra: db_session se
    conecta como `flash`, que se la salta por ser superusuario.

    No lleva rollback envolvente: el rol restringido no puede tocar los datos
    de otros usuarios, asi que cada test limpia lo suyo. A cambio, las
    politicas se evaluan igual que en produccion.
    """
    if not TEST_DATABASE_URL:
        pytest.skip("falta TEST_DATABASE_URL")

    _ensure_rls_role()

    motor = create_async_engine(_async_url(_rls_url()), poolclass=NullPool)
    try:
        async with AsyncSession(motor, expire_on_commit=False) as sesion:
            yield sesion
    finally:
        await motor.dispose()



@pytest_asyncio.fixture
async def rls_db_session() -> AsyncSession:
    """Como db_session, pero conectada con el rol SUJETO a las politicas RLS.

    Combina las dos fixtures de arriba:

      - el motor de rls_session, que entra como flash_test_app y por tanto NO
        se salta la RLS,
      - el SAVEPOINT envolvente de db_session, para que el test no deje nada
        escrito.

    Es la unica forma de ejercitar una peticion HTTP COMPLETA bajo politicas
    que se evaluan de verdad. Con db_session la peticion corre como `flash`,
    superusuario, y cualquier prueba de RLS pasaria en falso.

    Es tambien el ensayo de produccion: el dia que la API se conecte como
    `flash_app` en vez de `flash`, sera exactamente este escenario. Si el
    registro o el login se rompen bajo RLS, se descubre aqui y no en el
    despliegue.
    """
    if not TEST_DATABASE_URL:
        pytest.skip("falta TEST_DATABASE_URL")

    _ensure_rls_role()

    motor = create_async_engine(_async_url(_rls_url()), poolclass=NullPool)
    try:
        async with motor.connect() as conexion:
            transaccion = await conexion.begin()
            sesion = AsyncSession(
                bind=conexion,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )

            yield sesion

            await sesion.close()
            await transaccion.rollback()
    finally:
        await motor.dispose()
