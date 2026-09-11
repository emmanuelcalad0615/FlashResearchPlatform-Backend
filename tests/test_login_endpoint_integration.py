"""Login contra el grafo REAL: Postgres, repositorios y cableado de verdad.

Es el unico nivel que ejercita dependencies.py de punta a punta y que verifica
lo que solo la base puede confirmar: que la sesion quede persistida, que el
hash del refresh token sea lo unico guardado, y que un login rechazado no deje
rastro.
"""

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.dependencies import get_email_sender, get_session
from apps.api.infrastructure.cookies import ACCESS_COOKIE, REFRESH_COOKIE
from apps.api.main import app
from packages.core.domain.policies.tokens import hash_opaque_token
from tests.conftest import requiere_bd
from tests.fakes import InMemoryEmailSender

pytestmark = [pytest.mark.integration, requiere_bd]

SIGNUP = "/api/auth/signup"
VERIFY = "/api/auth/verify-email"
LOGIN = "/api/auth/login"
PASSWORD = "una-frase-larga-y-seguraB"


class _CorreoConEnlace(InMemoryEmailSender):
    def build_verification_link(self, token: str) -> str:
        return f"https://app.test/verify?token={token}"


@pytest.fixture
def correo() -> _CorreoConEnlace:
    return _CorreoConEnlace()


@pytest_asyncio.fixture
async def client(db_session, correo) -> AsyncClient:
    app.dependency_overrides[get_session] = lambda: db_session
    app.dependency_overrides[get_email_sender] = lambda: correo
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as cliente:
        yield cliente
    app.dependency_overrides.clear()


async def _cuenta_activa(client, correo) -> str:
    """Registra y verifica una cuenta. Devuelve su email."""
    email = f"login-int-{uuid.uuid4()}@ejemplo.com"
    await client.post(SIGNUP, json={"email": email, "password": PASSWORD})
    token = correo.verificaciones[-1][1].split("token=")[1]
    await client.post(VERIFY, json={"token": token})
    return email


# ---- Camino feliz ----------------------------------------------------------


async def test_a_full_signup_verify_login_cycle(client, correo):
    email = await _cuenta_activa(client, correo)

    r = await client.post(LOGIN, json={"email": email, "password": PASSWORD})

    assert r.status_code == 200
    assert ACCESS_COOKIE in r.cookies
    assert REFRESH_COOKIE in r.cookies


async def test_the_session_is_persisted(client, db_session, correo):
    email = await _cuenta_activa(client, correo)

    await client.post(LOGIN, json={"email": email, "password": PASSWORD})

    sesiones = await db_session.scalar(
        text(
            "SELECT count(*) FROM refresh_tokens rt "
            "JOIN users u ON u.id = rt.user_id WHERE u.email = :e"
        ),
        {"e": email},
    )
    assert sesiones == 1


async def test_only_the_refresh_hash_reaches_the_database(client, db_session, correo):
    """SEGURIDAD: un volcado de la base no puede abrir sesiones."""
    email = await _cuenta_activa(client, correo)

    r = await client.post(LOGIN, json={"email": email, "password": PASSWORD})
    refresh = r.cookies[REFRESH_COOKIE]

    guardado = await db_session.scalar(
        text(
            "SELECT token_hash FROM refresh_tokens rt "
            "JOIN users u ON u.id = rt.user_id WHERE u.email = :e"
        ),
        {"e": email},
    )
    assert guardado == hash_opaque_token(refresh)
    assert guardado != refresh


async def test_the_user_agent_is_persisted(client, db_session, correo):
    email = await _cuenta_activa(client, correo)

    await client.post(
        LOGIN,
        json={"email": email, "password": PASSWORD},
        headers={"User-Agent": "Firefox en Windows"},
    )

    guardado = await db_session.scalar(
        text(
            "SELECT user_agent FROM refresh_tokens rt "
            "JOIN users u ON u.id = rt.user_id WHERE u.email = :e"
        ),
        {"e": email},
    )
    assert guardado == "Firefox en Windows"


async def test_each_login_opens_its_own_family(client, db_session, correo):
    """Tres dispositivos, tres cadenas independientes."""
    email = await _cuenta_activa(client, correo)

    for _ in range(3):
        await client.post(LOGIN, json={"email": email, "password": PASSWORD})

    familias = await db_session.scalar(
        text(
            "SELECT count(DISTINCT family_id) FROM refresh_tokens rt "
            "JOIN users u ON u.id = rt.user_id WHERE u.email = :e"
        ),
        {"e": email},
    )
    assert familias == 3


# ---- Rechazos --------------------------------------------------------------


async def test_an_unverified_account_cannot_log_in(client):
    """Solo el signup, sin verificar."""
    email = f"login-int-{uuid.uuid4()}@ejemplo.com"
    await client.post(SIGNUP, json={"email": email, "password": PASSWORD})

    r = await client.post(LOGIN, json={"email": email, "password": PASSWORD})

    assert r.status_code == 403
    assert r.json()["error"]["code"] == "email_not_verified"


async def test_a_rejected_login_leaves_no_session(client, db_session, correo):
    email = await _cuenta_activa(client, correo)

    r = await client.post(
        LOGIN, json={"email": email, "password": "otra-frase-distinta"}
    )

    assert r.status_code == 401
    sesiones = await db_session.scalar(
        text(
            "SELECT count(*) FROM refresh_tokens rt "
            "JOIN users u ON u.id = rt.user_id WHERE u.email = :e"
        ),
        {"e": email},
    )
    assert sesiones == 0
