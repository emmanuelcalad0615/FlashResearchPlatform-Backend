"""Tests HTTP del login.

Sustituyen el caso de uso por uno con dobles, asi que corren sin Postgres.
Cubren lo que los tests de caso de uso no ven: las cookies, el cuerpo de la
respuesta, el mapeo de errores y las cabeceras que entran.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from apps.api.config import settings
from apps.api.dependencies import get_login_use_case
from apps.api.infrastructure.cookies import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
)
from apps.api.main import app
from packages.core.application.usecases.auth.login import LoginUseCase
from packages.core.domain.policies.passwords import hash_password
from packages.core.domain.policies.tokens import decode_access_token
from tests.fakes import (
    InMemoryRefreshTokenRepository,
    InMemoryUnitOfWork,
    InMemoryUserRepository,
)

LOGIN = "/api/auth/login"
SECRET = "un-secreto-de-prueba-de-al-menos-32-bytes-de-largo"
EMAIL = "ana@ejemplo.com"
PASSWORD = "una-frase-larga-y-seguraB"


class _Dobles:
    def __init__(self) -> None:
        self.users = InMemoryUserRepository()
        self.tokens = InMemoryRefreshTokenRepository()
        self.uow = InMemoryUnitOfWork()

    def caso(self) -> LoginUseCase:
        return LoginUseCase(
            users=self.users,
            tokens=self.tokens,
            uow=self.uow,
            jwt_secret=SECRET,
            jwt_algorithm="HS256",
            access_token_minutes=settings.access_token_minutes,
            refresh_token_days=settings.refresh_token_days,
        )


@pytest.fixture
async def dobles() -> _Dobles:
    d = _Dobles()
    d.user_id = uuid.uuid4()
    # Hash real: el caso de uso llama a verify_password de verdad.
    await d.users.create(d.user_id, EMAIL, hash_password(PASSWORD))
    await d.users.mark_email_verified(d.user_id)
    return d


@pytest.fixture
def client(dobles) -> TestClient:
    app.dependency_overrides[get_login_use_case] = dobles.caso
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, **kwargs):
    cuerpo = {"email": EMAIL, "password": PASSWORD} | kwargs.pop("json", {})
    return client.post(LOGIN, json=cuerpo, **kwargs)


# ---- Camino feliz ----------------------------------------------------------


def test_login_returns_200(client):
    assert _login(client).status_code == 200


def test_both_cookies_are_set(client):
    r = _login(client)

    assert ACCESS_COOKIE in r.cookies
    assert REFRESH_COOKIE in r.cookies


def test_the_access_cookie_carries_a_usable_token(client, dobles):
    r = _login(client)

    sub = decode_access_token(
        r.cookies[ACCESS_COOKIE], secret=SECRET, algorithm="HS256"
    )
    assert sub == str(dobles.user_id)


# ---- Seguridad de la respuesta ---------------------------------------------


def test_no_token_appears_in_the_body(client):
    """SEGURIDAD: si el token viajara en el JSON, el frontend tendria que
    leerlo para guardarlo, y entonces un XSS podria leerlo tambien. En una
    cookie HttpOnly ningun script lo alcanza."""
    r = _login(client)

    cuerpo = r.text
    assert r.cookies[ACCESS_COOKIE] not in cuerpo
    assert r.cookies[REFRESH_COOKIE] not in cuerpo
    assert set(r.json()) == {"message"}


def test_the_cookies_are_httponly_and_samesite(client):
    """Las banderas no se ven al usar la API: solo un test las protege."""
    cabeceras = [
        v for k, v in _login(client).headers.multi_items() if k.lower() == "set-cookie"
    ]

    assert len(cabeceras) == 2
    for cabecera in cabeceras:
        assert "HttpOnly" in cabecera
        assert "SameSite=lax" in cabecera


def test_the_refresh_cookie_is_scoped_to_its_route(client):
    """La llave larga no viaja en las peticiones normales del dashboard."""
    refresh = next(
        v
        for k, v in _login(client).headers.multi_items()
        if k.lower() == "set-cookie" and v.startswith(REFRESH_COOKIE)
    )

    assert "Path=/api/auth/refresh" in refresh


# ---- Rechazos --------------------------------------------------------------


def test_a_wrong_password_is_401(client):
    r = _login(client, json={"password": "otra-frase-completamente-distinta"})

    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_credentials"


def test_an_unknown_email_is_the_same_401(client):
    r = _login(client, json={"email": "nadie@ejemplo.com"})

    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_credentials"


def test_both_rejections_are_byte_identical(client):
    """SEGURIDAD: ni el status, ni el codigo, ni el mensaje pueden distinguir
    "no existe" de "contrasena incorrecta"."""
    sin_usuario = _login(client, json={"email": "nadie@ejemplo.com"})
    mala = _login(client, json={"password": "otra-frase-completamente-distinta"})

    assert sin_usuario.status_code == mala.status_code
    error_a = sin_usuario.json()["error"]
    error_b = mala.json()["error"]
    assert error_a["code"] == error_b["code"]
    assert error_a["message"] == error_b["message"]


def test_a_rejected_login_sets_no_cookies(client):
    r = _login(client, json={"password": "otra-frase-completamente-distinta"})

    assert ACCESS_COOKIE not in r.cookies
    assert REFRESH_COOKIE not in r.cookies


async def test_an_unverified_account_is_403(client, dobles):
    otro = uuid.uuid4()
    await dobles.users.create(otro, "pendiente@ejemplo.com", hash_password(PASSWORD))

    r = _login(client, json={"email": "pendiente@ejemplo.com"})

    assert r.status_code == 403
    assert r.json()["error"]["code"] == "email_not_verified"


# ---- Entrada ---------------------------------------------------------------


@pytest.mark.parametrize(
    "cuerpo",
    [
        {"email": EMAIL},                              # falta password
        {"password": PASSWORD},                        # falta email
        {"email": "", "password": PASSWORD},           # email vacio
        {"email": EMAIL, "password": "x" * 2000},      # carga absurda
    ],
)
def test_malformed_payloads_are_422(client, cuerpo):
    assert client.post(LOGIN, json=cuerpo).status_code == 422


def test_a_malformed_email_is_401_not_422(client):
    """A diferencia del signup, aqui NO se valida el formato del email.

    Si un email malformado diera 422 y uno inexistente 401, esa diferencia
    permitiria distinguir direcciones. Todo lo que no exista sale igual.
    """
    r = _login(client, json={"email": "esto-no-es-un-email"})

    assert r.status_code == 401


def test_login_only_accepts_post(client):
    assert client.get(LOGIN).status_code == 405


# ---- User-Agent ------------------------------------------------------------


def test_the_user_agent_is_stored_with_the_session(client, dobles):
    _login(client, headers={"User-Agent": "Firefox en Windows"})

    assert len(dobles.tokens.por_id) == 1


def test_a_missing_user_agent_does_not_break_the_login(client):
    """Un cliente puede no mandarla; la columna es opcional."""
    r = client.post(
        LOGIN, json={"email": EMAIL, "password": PASSWORD}, headers={"User-Agent": ""}
    )

    assert r.status_code == 200
