"""Tests HTTP de GET /api/auth/me.

Corren sin Postgres, y eso NO es un atajo: todos los rechazos que se prueban
aqui ocurren en get_current_user ANTES de consultar la base. Si alguno llegara
a tocarla, este archivo fallaria, y ese fallo seria informacion.

Lo que si necesita la base —el camino feliz de punta a punta y el filtrado por
RLS— vive en test_me_endpoint_integration.py.
"""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from apps.api.config import settings
from apps.api.dependencies import get_current_user, get_me_use_case
from apps.api.infrastructure.cookies import ACCESS_COOKIE, REFRESH_COOKIE
from apps.api.main import app
from packages.core.application.usecases.auth.me import GetMeUseCase
from packages.core.domain.entities import User
from packages.core.domain.policies.tokens import (
    TOKEN_TYPE_ACCESS,
    generate_opaque_token,
)
from tests.fakes import InMemoryProfileRepository

ME = "/api/auth/me"


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as cliente:
        yield cliente
    app.dependency_overrides.clear()


def _firmar(payload: dict, *, secret: str | None = None) -> str:
    """Un JWT a medida, para fabricar tokens que el emisor real nunca produce."""
    return jwt.encode(payload, secret or settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _payload_base(**extra) -> dict:
    ahora = datetime.now(UTC)
    return {
        "sub": str(uuid.uuid4()),
        "iat": ahora,
        "exp": ahora + timedelta(minutes=15),
        "jti": str(uuid.uuid4()),
        "type": TOKEN_TYPE_ACCESS,
        **extra,
    }


# ---------------------------------------------------------------------------
# Rechazos. Ninguno llega a consultar la base.
# ---------------------------------------------------------------------------


def test_sin_cookie_responde_401(client) -> None:
    """Ausencia de sesion, no token invalido. 401 y no 400."""
    r = client.get(ME)

    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_con_solo_la_cookie_de_refresh_responde_401(client) -> None:
    """El escenario real de una sesion caducada.

    El refresh token vive con Path=/api/auth/refresh, asi que el navegador NO
    lo manda a /me. Aunque llegara, no es la cookie que esta ruta lee.
    """
    client.cookies.set(REFRESH_COOKIE, generate_opaque_token())

    r = client.get(ME)

    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_token_expirado_responde_410(client) -> None:
    """410 y no 401, a proposito: es la senal de la que depende el refresh.

    El cliente que recibe token_expired pide un refresh y reintenta sin que el
    usuario se entere. Si respondiera 401 lo mandaria al login cada vez que
    caduca el access token, o sea cada quince minutos.
    """
    ahora = datetime.now(UTC)
    token = _firmar(
        _payload_base(iat=ahora - timedelta(hours=2), exp=ahora - timedelta(hours=1))
    )
    client.cookies.set(ACCESS_COOKIE, token)

    r = client.get(ME)

    assert r.status_code == 410
    assert r.json()["error"]["code"] == "token_expired"


def test_firmado_con_otro_secreto_responde_400(client) -> None:
    """El token esta bien formado pero la firma no cuadra: es una falsificacion."""
    token = _firmar(_payload_base(), secret="otro-secreto-de-al-menos-32-caracteres-aqui")
    client.cookies.set(ACCESS_COOKIE, token)

    r = client.get(ME)

    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_token"


def test_token_de_otro_tipo_responde_400(client) -> None:
    """Firma nuestra y sin caducar, pero no es un access token.

    Es la razon de ser del claim `type`: si manana existe un token de reset de
    contrasena firmado con el mismo secreto, no puede colarse como credencial
    de sesion.
    """
    token = _firmar(_payload_base(type="password_reset"))
    client.cookies.set(ACCESS_COOKIE, token)

    r = client.get(ME)

    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_token"


def test_token_sin_sub_responde_400(client) -> None:
    payload = _payload_base()
    del payload["sub"]
    client.cookies.set(ACCESS_COOKIE, _firmar(payload))

    r = client.get(ME)

    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_token"


def test_sub_que_no_es_uuid_responde_400(client) -> None:
    """Firma valida no implica contenido con sentido.

    Sin la conversion defensiva a UUID en get_current_user, este `sub` llegaria
    al repositorio y saldria un 500: un error del servidor por culpa de una
    peticion del cliente.
    """
    client.cookies.set(ACCESS_COOKIE, _firmar(_payload_base(sub="no-soy-un-uuid")))

    r = client.get(ME)

    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_token"


def test_una_cadena_cualquiera_responde_400(client) -> None:
    """Ni siquiera es un JWT."""
    client.cookies.set(ACCESS_COOKIE, "esto-no-es-un-jwt")

    r = client.get(ME)

    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_token"


def test_un_token_opaco_en_la_cookie_de_acceso_responde_400(client) -> None:
    """Un refresh token colocado donde va el access token.

    No puede colarse porque no es un JWT: el riesgo desaparece por construccion
    en vez de mitigarse con una comprobacion.
    """
    client.cookies.set(ACCESS_COOKIE, generate_opaque_token())

    r = client.get(ME)

    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_token"


# ---------------------------------------------------------------------------
# Forma de la respuesta. Aqui SI se sustituye get_current_user: lo que se
# comprueba es lo que sale, no como se autentico.
# ---------------------------------------------------------------------------


USER_ID = uuid.uuid4()
EMAIL = "ana@ejemplo.com"


def _usuario(*, verificado: bool = True) -> User:
    ahora = datetime.now(UTC)
    return User(
        id=USER_ID,
        email=EMAIL,
        password_hash="$argon2id$no-deberia-salir-nunca",
        email_verified=verificado,
        created_at=ahora,
        updated_at=ahora,
    )


@pytest.fixture
def perfiles() -> InMemoryProfileRepository:
    return InMemoryProfileRepository()


@pytest.fixture
def autenticado(client, perfiles) -> TestClient:
    app.dependency_overrides[get_current_user] = _usuario
    app.dependency_overrides[get_me_use_case] = lambda: GetMeUseCase(profiles=perfiles)
    return client


async def test_devuelve_los_cuatro_campos(autenticado, perfiles) -> None:
    await perfiles.create(USER_ID, display_name="Ana")

    r = autenticado.get(ME)

    assert r.status_code == 200
    assert r.json() == {
        "id": str(USER_ID),
        "email": EMAIL,
        "email_verified": True,
        "display_name": "Ana",
    }


def test_sin_perfil_responde_200_con_display_name_nulo(autenticado) -> None:
    """El perfil no aparece: la sesion sigue en pie.

    Es un estado que no deberia darse, pero /auth/me es lo que el frontend
    pregunta en cada carga de pagina: un 500 aqui echaria de la aplicacion a un
    usuario con credenciales validas.
    """
    r = autenticado.get(ME)

    assert r.status_code == 200
    assert r.json()["display_name"] is None


def test_nunca_devuelve_el_hash_de_la_contrasena(autenticado) -> None:
    """La barrera es response_model, que recorta la salida a lo declarado.

    Este test protege de que alguien anada un campo a MeResponse sin pensar: el
    dia que se devuelva el User entero, falla aqui y no en produccion.
    """
    cuerpo = autenticado.get(ME).json()

    assert "password_hash" not in cuerpo
    assert "$argon2id$" not in autenticado.get(ME).text


def test_email_sin_verificar_tambien_recibe_200(autenticado) -> None:
    """No se vuelve a comprobar email_verified aqui, y es correcto.

    Quien tiene un access token es porque paso por el login, y el login ya lo
    exige. Repetir la comprobacion en cada ruta protegida seria trabajo
    duplicado que ademas puede quedar desincronizado.
    """
    app.dependency_overrides[get_current_user] = lambda: _usuario(verificado=False)

    r = autenticado.get(ME)

    assert r.status_code == 200
    assert r.json()["email_verified"] is False
