"""Ningun log puede contener credenciales.

Es el punto explicito del repaso de seguridad de la HU-A07. Hoy
RequestIDMiddleware loguea solo metodo, ruta, status y duracion —lo correcto—
pero nada lo impide cambiar: basta que alguien anada `headers=dict(...)` al
log buscando depurar algo, y a partir de ese dia cada peticion deja una cookie
de sesion escrita en disco, replicada en el agregador de logs y en sus copias.

Estos tests son la barrera. Corren peticiones reales por la aplicacion entera
—middlewares incluidos— y revisan TODO lo que se escribio.
"""

import logging
import uuid

import pytest
from fastapi.testclient import TestClient

from apps.api.config import settings
from apps.api.dependencies import get_login_use_case
from apps.api.infrastructure.cookies import ACCESS_COOKIE, REFRESH_COOKIE
from apps.api.main import app
from packages.core.application.usecases.auth.login import LoginUseCase
from packages.core.domain.policies.passwords import hash_password
from packages.core.domain.policies.tokens import (
    create_access_token,
    generate_opaque_token,
)
from tests.fakes import (
    InMemoryRefreshTokenRepository,
    InMemoryUnitOfWork,
    InMemoryUserRepository,
)

EMAIL = "ana@ejemplo.com"
PASSWORD = "una-frase-larga-y-secretaB"


class _Dobles:
    def __init__(self) -> None:
        self.users = InMemoryUserRepository()
        self.tokens = InMemoryRefreshTokenRepository()
        self.uow = InMemoryUnitOfWork()
        self.user_id = uuid.uuid4()

    def caso(self) -> LoginUseCase:
        return LoginUseCase(
            users=self.users,
            tokens=self.tokens,
            uow=self.uow,
            jwt_secret=settings.jwt_secret,
            jwt_algorithm=settings.jwt_algorithm,
            access_token_minutes=15,
            refresh_token_days=30,
        )


@pytest.fixture
async def dobles() -> _Dobles:
    d = _Dobles()
    await d.users.create(d.user_id, EMAIL, hash_password(PASSWORD))
    await d.users.mark_email_verified(d.user_id)
    return d


@pytest.fixture
def client(dobles) -> TestClient:
    app.dependency_overrides[get_login_use_case] = dobles.caso
    with TestClient(app) as cliente:
        yield cliente
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Lo que entra en la peticion
# ---------------------------------------------------------------------------


def test_el_login_no_escribe_la_contrasena_en_el_log(client, caplog) -> None:
    """El fallo mas caro de todos.

    Una contrasena en un log sobrevive a su rotacion, viaja a los backups, y
    sigue sirviendo para entrar en las otras cuentas del usuario, porque la
    gente reutiliza contrasenas.
    """
    with caplog.at_level(logging.DEBUG):
        client.post(
            "/api/auth/login", json={"email": EMAIL, "password": PASSWORD}
        )

    assert PASSWORD not in caplog.text


def test_un_login_fallido_tampoco_la_escribe(client, caplog) -> None:
    """El camino de error es donde mas se tiende a loguear 'todo por si acaso'."""
    with caplog.at_level(logging.DEBUG):
        client.post(
            "/api/auth/login",
            json={"email": EMAIL, "password": "esta-no-es-la-correctaB"},
        )

    assert "esta-no-es-la-correctaB" not in caplog.text


def test_no_se_loguean_las_cookies_que_llegan(client, caplog) -> None:
    """Un access token en el log vale como una sesion abierta."""
    token = create_access_token(
        str(uuid.uuid4()),
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        expires_minutes=15,
    )
    opaco = generate_opaque_token()
    client.cookies.set(ACCESS_COOKIE, token)
    client.cookies.set(REFRESH_COOKIE, opaco)

    with caplog.at_level(logging.DEBUG):
        client.get("/api/auth/me")

    assert token not in caplog.text
    assert opaco not in caplog.text


def test_no_se_loguea_la_cabecera_authorization(client, caplog) -> None:
    """Aunque hoy no se use, un cliente puede mandarla igual."""
    secreto = "Bearer un-token-de-otro-sistema"

    with caplog.at_level(logging.DEBUG):
        client.get("/api/auth/me", headers={"Authorization": secreto})

    assert secreto not in caplog.text


def test_no_se_loguea_el_cuerpo_de_la_peticion(client, caplog) -> None:
    """Loguear el cuerpo captura la contrasena sin que nadie lo pretenda."""
    with caplog.at_level(logging.DEBUG):
        client.post(
            "/api/auth/signup", json={"email": EMAIL, "password": PASSWORD}
        )

    assert PASSWORD not in caplog.text


# ---------------------------------------------------------------------------
# Lo que sale en la respuesta
# ---------------------------------------------------------------------------


def test_no_se_loguean_las_cookies_emitidas(client, caplog) -> None:
    """El login EMITE tokens: si se logueara la respuesta, saldrian ahi."""
    with caplog.at_level(logging.DEBUG):
        respuesta = client.post(
            "/api/auth/login", json={"email": EMAIL, "password": PASSWORD}
        )

    emitidas = respuesta.headers.get_list("set-cookie")
    assert emitidas, "el login deberia emitir cookies; si no, el test no prueba nada"
    for cookie in emitidas:
        valor = cookie.split("=", 1)[1].split(";")[0]
        assert valor not in caplog.text


def test_el_secreto_de_firma_no_aparece_nunca(client, caplog) -> None:
    """Con JWT_SECRET se fabrican tokens validos para cualquier usuario."""
    with caplog.at_level(logging.DEBUG):
        client.post(
            "/api/auth/login", json={"email": EMAIL, "password": PASSWORD}
        )
        client.get("/api/auth/me")

    assert settings.jwt_secret not in caplog.text


# ---------------------------------------------------------------------------
# Lo que SI tiene que estar
# ---------------------------------------------------------------------------


def test_el_log_de_acceso_sigue_sirviendo(client, caplog) -> None:
    """Un log que no filtra nada porque no escribe nada no vale.

    Sin esta comprobacion, borrar el logging entero dejaria todos los tests de
    arriba en verde.
    """
    with caplog.at_level(logging.INFO):
        client.post(
            "/api/auth/login", json={"email": EMAIL, "password": PASSWORD}
        )

    assert "http_request" in caplog.text
    assert "/api/auth/login" in caplog.text
    assert "200" in caplog.text


def test_el_email_no_aparece_en_el_log_de_acceso(client, caplog) -> None:
    """Dato personal. Con el user_id se investiga igual."""
    with caplog.at_level(logging.DEBUG):
        client.post(
            "/api/auth/login",
            json={"email": EMAIL, "password": "esta-no-es-la-correctaB"},
        )

    assert EMAIL not in caplog.text


@pytest.mark.integration
def test_ninguna_ruta_de_auth_filtra_credenciales(client, caplog) -> None:
    """Barrido: las ocho rutas de auth, en una sola pasada.

    Los tests de arriba miran rutas concretas. Este recorre TODAS las que
    cuelgan de /api/auth, para que una ruta nueva quede cubierta sin que nadie
    se acuerde de anadirle su test.
    """
    marcador = "contrasena-secreta-que-no-debe-salirB"
    cuerpo = {"email": EMAIL, "password": marcador, "token": marcador}
    rutas = [
        r for r in app.openapi()["paths"] if r.startswith("/api/auth")
    ]
    assert len(rutas) >= 8, "faltan rutas por barrer"

    with caplog.at_level(logging.DEBUG):
        for ruta in rutas:
            client.post(ruta, json=cuerpo)
            client.get(ruta)

    assert marcador not in caplog.text
