"""Registro y verificacion contra el grafo REAL.

Postgres de verdad, repositorios reales, cableado real. Es el unico nivel que
ejercita dependencies.py de punta a punta: si falta un setting o una firma
cambia, aqui se nota.

El correo si se sustituye por un doble. Levantar Mailpit en los tests anadiria
una dependencia mas sin cubrir nada nuevo: el adapter SMTP ya tiene sus propios
tests, y lo que aqui interesa es la cadena HTTP -> caso de uso -> Postgres.
"""

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from apps.api.dependencies import get_email_sender, get_session
from apps.api.main import app
from tests.conftest import requiere_bd
from tests.fakes import InMemoryEmailSender

pytestmark = [pytest.mark.integration, requiere_bd]

SIGNUP = "/api/auth/signup"
VERIFY = "/api/auth/verify-email"
PASSWORD = "una-frase-larga-y-seguraB"


class _CorreoConEnlaceReal(InMemoryEmailSender):
    """El doble tiene que saber armar el enlace: el caso de uso lo recibe del
    adapter, y aqui sustituimos el adapter."""

    def build_verification_link(self, token: str) -> str:
        return f"https://app.test/verify?token={token}"


@pytest.fixture
def correo() -> _CorreoConEnlaceReal:
    return _CorreoConEnlaceReal()


@pytest_asyncio.fixture
async def client(db_session, correo) -> AsyncClient:
    """httpx.AsyncClient y NO TestClient.

    TestClient es sincrono y corre la app en un event loop propio, mientras la
    sesion de db_session nacio en el loop de pytest. Las conexiones de asyncpg
    quedan atadas a su loop, asi que cruzarlas revienta con "attached to a
    different loop".

    AsyncClient con ASGITransport corre en el MISMO loop que el test, y ahi la
    sesion inyectada funciona.
    """
    # get_session se sustituye por la sesion con rollback de la fixture, para
    # que nada de lo que escriba el test sobreviva.
    app.dependency_overrides[get_session] = lambda: db_session
    app.dependency_overrides[get_email_sender] = lambda: correo

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as cliente:
        yield cliente

    app.dependency_overrides.clear()


def _email() -> str:
    return f"int-{uuid.uuid4()}@ejemplo.com"


async def test_signup_persists_user_profile_and_token(client, db_session, correo):
    from sqlalchemy import text

    email = _email()

    r = await client.post(SIGNUP, json={"email": email, "password": PASSWORD})

    assert r.status_code == 201
    fila = (await db_session.execute(
        text("SELECT id, email_verified FROM users WHERE email = :e"), {"e": email}
    )).one()
    assert fila.email_verified is False
    perfiles = await db_session.scalar(
        text("SELECT count(*) FROM profiles WHERE id = :i"), {"i": fila.id}
    )
    assert perfiles == 1
    assert len(correo.verificaciones) == 1


async def test_the_full_signup_and_verify_cycle(client, db_session, correo):
    from sqlalchemy import text

    email = _email()
    await client.post(SIGNUP, json={"email": email, "password": PASSWORD})
    token = correo.verificaciones[-1][1].split("token=")[1]

    r = await client.post(VERIFY, json={"token": token})

    assert r.status_code == 200
    verificado = await db_session.scalar(
        text("SELECT email_verified FROM users WHERE email = :e"), {"e": email}
    )
    assert verificado is True


async def test_a_short_password_persists_nothing(client, db_session):
    from sqlalchemy import text

    email = _email()

    r = await client.post(SIGNUP, json={"email": email, "password": "corta"})

    assert r.status_code == 422
    total = await db_session.scalar(
        text("SELECT count(*) FROM users WHERE email = :e"), {"e": email}
    )
    assert total == 0
