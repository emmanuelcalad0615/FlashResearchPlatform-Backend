"""Refresh contra el grafo REAL: Postgres, repositorios y cableado de verdad.

Lo que solo se puede afirmar aqui: que la rotacion deja rastro en la tabla, que
revoke_family escribe de verdad en todas las filas de la cadena, y que una
sesion renovada sigue sirviendo para entrar en una ruta protegida.
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
REFRESH = "/api/auth/refresh"
ME = "/api/auth/me"
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


async def _sesion_iniciada(client, correo) -> str:
    email = f"refresh-int-{uuid.uuid4()}@ejemplo.com"
    await client.post(SIGNUP, json={"email": email, "password": PASSWORD})
    token = correo.verificaciones[-1][1].split("token=")[1]
    await client.post(VERIFY, json={"token": token})
    await client.post(LOGIN, json={"email": email, "password": PASSWORD})
    return email


async def _fila(db_session, refresh_token: str):
    return (
        await db_session.execute(
            text(
                "SELECT family_id, used_at, revoked_at FROM refresh_tokens "
                "WHERE token_hash = :h"
            ),
            {"h": hash_opaque_token(refresh_token)},
        )
    ).one_or_none()


# ---------------------------------------------------------------------------
# El ciclo completo
# ---------------------------------------------------------------------------


async def test_renueva_la_sesion_y_entrega_las_dos_cookies(client, correo) -> None:
    await _sesion_iniciada(client, correo)

    r = await client.post(REFRESH)

    assert r.status_code == 200
    entregadas = {c.split("=")[0] for c in r.headers.get_list("set-cookie")}
    assert entregadas == {ACCESS_COOKIE, REFRESH_COOKIE}


async def test_la_sesion_renovada_sirve_para_una_ruta_protegida(
    client, correo
) -> None:
    """La prueba de que el par nuevo es utilizable de verdad.

    Sin esto, un refresh que devolviera tokens bien formados pero invalidos
    pasaria todos los demas tests.
    """
    email = await _sesion_iniciada(client, correo)
    await client.post(REFRESH)

    r = await client.get(ME)

    assert r.status_code == 200
    assert r.json()["email"] == email


async def test_la_rotacion_queda_escrita_en_la_tabla(
    client, correo, db_session
) -> None:
    await _sesion_iniciada(client, correo)
    viejo = client.cookies[REFRESH_COOKIE]

    await client.post(REFRESH)
    nuevo = client.cookies[REFRESH_COOKIE]

    fila_vieja = await _fila(db_session, viejo)
    fila_nueva = await _fila(db_session, nuevo)
    assert fila_vieja.used_at is not None
    assert fila_nueva.used_at is None
    # Misma familia: la cadena es rastreable de punta a punta.
    assert fila_nueva.family_id == fila_vieja.family_id


async def test_solo_se_guarda_el_hash(client, correo, db_session) -> None:
    """Un volcado de la base no puede abrir sesiones."""
    await _sesion_iniciada(client, correo)
    await client.post(REFRESH)
    nuevo = client.cookies[REFRESH_COOKIE]

    encontrado = await db_session.scalar(
        text("SELECT count(*) FROM refresh_tokens WHERE token_hash = :t"),
        {"t": nuevo},
    )

    assert encontrado == 0
    assert await _fila(db_session, nuevo) is not None


async def test_renovar_varias_veces_seguidas(client, correo, db_session) -> None:
    """Tres renovaciones encadenadas, todas en la misma familia."""
    await _sesion_iniciada(client, correo)
    inicial = await _fila(db_session, client.cookies[REFRESH_COOKIE])

    for _ in range(3):
        assert (await client.post(REFRESH)).status_code == 200

    ultima = await _fila(db_session, client.cookies[REFRESH_COOKIE])
    assert ultima.family_id == inicial.family_id
    assert (await client.get(ME)).status_code == 200


# ---------------------------------------------------------------------------
# Deteccion de reutilizacion, sobre la base de verdad
# ---------------------------------------------------------------------------


async def test_reutilizar_revoca_toda_la_cadena_en_la_base(
    client, correo, db_session
) -> None:
    """El robo, con las filas a la vista.

    Se renueva dos veces para que la familia tenga tres eslabones, y despues se
    reutiliza el primero. Al terminar, NINGUNA fila de la familia puede quedar
    sin revocar: si solo se revocara el token presentado, el ladron conservaria
    el que obtuvo en su canje.
    """
    await _sesion_iniciada(client, correo)
    primero = client.cookies[REFRESH_COOKIE]
    await client.post(REFRESH)
    await client.post(REFRESH)

    familia = (await _fila(db_session, primero)).family_id
    client.cookies.set(REFRESH_COOKIE, primero, domain="", path="/api/auth/refresh")

    r = await client.post(REFRESH)

    assert r.status_code == 400
    vivas = await db_session.scalar(
        text(
            "SELECT count(*) FROM refresh_tokens "
            "WHERE family_id = :f AND revoked_at IS NULL"
        ),
        {"f": familia},
    )
    assert vivas == 0


async def test_tras_la_deteccion_no_se_puede_renovar_mas(
    client, correo
) -> None:
    """La victima tambien queda fuera, y tiene que volver a iniciar sesion.

    Es el coste aceptado del diseno: preferimos echar a los dos que dejar
    dentro a quien robo el token.
    """
    await _sesion_iniciada(client, correo)
    primero = client.cookies[REFRESH_COOKIE]
    await client.post(REFRESH)
    ultimo_legitimo = client.cookies[REFRESH_COOKIE]

    client.cookies.set(REFRESH_COOKIE, primero, domain="", path="/api/auth/refresh")
    await client.post(REFRESH)

    client.cookies.set(
        REFRESH_COOKIE, ultimo_legitimo, domain="", path="/api/auth/refresh"
    )
    r = await client.post(REFRESH)

    assert r.status_code == 400


async def test_no_toca_las_sesiones_de_otro_dispositivo(
    client, correo, db_session
) -> None:
    """Dos logins, dos familias. Comprometer una no cierra la otra."""
    email = await _sesion_iniciada(client, correo)
    comprometido = client.cookies[REFRESH_COOKIE]
    await client.post(REFRESH)

    # Segundo login del mismo usuario: abre su propia familia.
    await client.post(LOGIN, json={"email": email, "password": PASSWORD})
    del_otro_movil = client.cookies[REFRESH_COOKIE]

    client.cookies.set(
        REFRESH_COOKIE, comprometido, domain="", path="/api/auth/refresh"
    )
    assert (await client.post(REFRESH)).status_code == 400

    client.cookies.set(
        REFRESH_COOKIE, del_otro_movil, domain="", path="/api/auth/refresh"
    )
    assert (await client.post(REFRESH)).status_code == 200
