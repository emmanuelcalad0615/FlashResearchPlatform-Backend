"""Logout contra el grafo REAL, con varias sesiones abiertas a la vez.

Es el unico nivel donde se puede demostrar el alcance de cada cierre: se abren
tres sesiones del mismo usuario y se comprueba, contra las filas de
refresh_tokens, cuales quedan vivas despues de cada operacion.
"""

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.dependencies import get_email_sender, get_session
from apps.api.infrastructure.cookies import ACCESS_COOKIE, REFRESH_COOKIE
from apps.api.main import app
from tests.conftest import requiere_bd
from tests.fakes import InMemoryEmailSender

pytestmark = [pytest.mark.integration, requiere_bd]

SIGNUP = "/api/auth/signup"
VERIFY = "/api/auth/verify-email"
LOGIN = "/api/auth/login"
REFRESH = "/api/auth/refresh"
ME = "/api/auth/me"
LOGOUT = "/api/auth/logout"
LOGOUT_ALL = "/api/auth/logout-all"
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
    email = f"logout-int-{uuid.uuid4()}@ejemplo.com"
    await client.post(SIGNUP, json={"email": email, "password": PASSWORD})
    token = correo.verificaciones[-1][1].split("token=")[1]
    await client.post(VERIFY, json={"token": token})
    return email


async def _login(client, email: str) -> dict[str, str]:
    """Inicia sesion y devuelve las cookies de ESA sesion.

    Se guardan aparte porque el cliente solo puede llevar un juego a la vez:
    para simular tres dispositivos hay que reponerlas a mano.
    """
    await client.post(LOGIN, json={"email": email, "password": PASSWORD})
    return {
        ACCESS_COOKIE: client.cookies[ACCESS_COOKIE],
        REFRESH_COOKIE: client.cookies[REFRESH_COOKIE],
    }


def _usar(client, sesion: dict[str, str]) -> None:
    client.cookies.clear()
    client.cookies.set(ACCESS_COOKIE, sesion[ACCESS_COOKIE], domain="", path="/")
    client.cookies.set(
        REFRESH_COOKIE, sesion[REFRESH_COOKIE], domain="", path="/api/auth/refresh"
    )


async def _vivas(db_session, email: str) -> int:
    return await db_session.scalar(
        text(
            "SELECT count(*) FROM refresh_tokens t JOIN users u ON u.id = t.user_id "
            "WHERE u.email = :email AND t.revoked_at IS NULL"
        ),
        {"email": email},
    )


# ---------------------------------------------------------------------------
# /logout — cierra UNA
# ---------------------------------------------------------------------------


async def test_cierra_la_sesion_y_deja_las_otras_vivas(
    client, correo, db_session
) -> None:
    """Tres dispositivos, se cierra uno. Los otros dos siguen dentro."""
    email = await _cuenta_activa(client, correo)
    movil = await _login(client, email)
    portatil = await _login(client, email)
    tablet = await _login(client, email)

    _usar(client, portatil)
    assert (await client.post(LOGOUT)).status_code == 200

    assert await _vivas(db_session, email) == 2
    _usar(client, movil)
    assert (await client.post(REFRESH)).status_code == 200
    _usar(client, tablet)
    assert (await client.post(REFRESH)).status_code == 200


async def test_la_sesion_cerrada_ya_no_puede_renovar(client, correo) -> None:
    """Lo que de verdad significa cerrar sesion.

    Borrar la cookie no basta: si alguien copio el refresh token, su copia
    seguiria sirviendo. Lo que lo impide es el revoked_at en la base.
    """
    email = await _cuenta_activa(client, correo)
    sesion = await _login(client, email)

    await client.post(LOGOUT)

    _usar(client, sesion)
    assert (await client.post(REFRESH)).status_code == 400


async def test_el_access_token_sobrevive_hasta_caducar(client, correo) -> None:
    """El limite honesto del JWT sin estado, dejado por escrito.

    Tras cerrar sesion, el access token que ya estaba emitido sigue abriendo
    rutas protegidas hasta que expira. No es un descuido: invalidarlo antes
    exigiria consultar la base en cada peticion. Ver HU-A16.
    """
    email = await _cuenta_activa(client, correo)
    sesion = await _login(client, email)

    await client.post(LOGOUT)

    _usar(client, sesion)
    assert (await client.get(ME)).status_code == 200


async def test_cierra_la_cadena_completa_tras_varias_renovaciones(
    client, correo, db_session
) -> None:
    """Una sesion renovada dos veces tiene tres filas. Se revocan las tres."""
    email = await _cuenta_activa(client, correo)
    await _login(client, email)
    await client.post(REFRESH)
    await client.post(REFRESH)

    await client.post(LOGOUT)

    assert await _vivas(db_session, email) == 0


# ---------------------------------------------------------------------------
# /logout-all — cierra TODAS
# ---------------------------------------------------------------------------


async def test_logout_all_cierra_los_tres_dispositivos(
    client, correo, db_session
) -> None:
    email = await _cuenta_activa(client, correo)
    movil = await _login(client, email)
    portatil = await _login(client, email)
    await _login(client, email)

    assert (await client.post(LOGOUT_ALL)).status_code == 200

    assert await _vivas(db_session, email) == 0
    _usar(client, movil)
    assert (await client.post(REFRESH)).status_code == 400
    _usar(client, portatil)
    assert (await client.post(REFRESH)).status_code == 400


async def test_logout_all_no_toca_a_otro_usuario(
    client, correo, db_session
) -> None:
    """El fallo que convertiria un cierre de sesion en una caida del servicio."""
    ana = await _cuenta_activa(client, correo)
    beto = await _cuenta_activa(client, correo)
    await _login(client, beto)
    await _login(client, ana)

    await client.post(LOGOUT_ALL)

    assert await _vivas(db_session, ana) == 0
    assert await _vivas(db_session, beto) == 1


async def test_logout_all_incluye_la_sesion_desde_la_que_se_pide(
    client, correo
) -> None:
    """Deliberado: quien sospecha de un intruso no sabe cual sesion es la suya."""
    email = await _cuenta_activa(client, correo)
    sesion = await _login(client, email)

    await client.post(LOGOUT_ALL)

    _usar(client, sesion)
    assert (await client.post(REFRESH)).status_code == 400
