"""Tests HTTP de POST /api/auth/logout y /api/auth/logout-all.

Sin Postgres. Cubren lo que el caso de uso no ve: que las rutas esten
protegidas, que el family_id salga del token firmado, y que las cookies se
borren con el Path correcto.
"""

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from apps.api.config import settings
from apps.api.dependencies import (
    get_current_user,
    get_logout_all_use_case,
    get_logout_use_case,
)
from apps.api.infrastructure.cookies import (
    ACCESS_COOKIE,
    ACCESS_COOKIE_PATH,
    REFRESH_COOKIE,
    REFRESH_COOKIE_PATH,
)
from apps.api.main import app
from packages.core.application.usecases.auth.logout import LogoutUseCase
from packages.core.application.usecases.auth.logout_all import LogoutAllUseCase
from packages.core.domain.entities import User
from packages.core.domain.policies.tokens import create_access_token
from tests.fakes import InMemoryRefreshTokenRepository, InMemoryUnitOfWork

LOGOUT = "/api/auth/logout"
LOGOUT_ALL = "/api/auth/logout-all"

USER_ID = uuid.uuid4()
FAMILY_ID = uuid.uuid4()


class _Dobles:
    def __init__(self) -> None:
        self.tokens = InMemoryRefreshTokenRepository()
        self.uow = InMemoryUnitOfWork()
        self.revocadas: list[uuid.UUID] = []
        self.usuarios_cerrados: list[uuid.UUID] = []

        # Se envuelven los metodos del doble para dejar constancia de CON QUE
        # argumento se llamaron: es lo unico que distingue "revoco la familia
        # correcta" de "revoco alguna".
        revoke_family = self.tokens.revoke_family
        revoke_all = self.tokens.revoke_all_for_user

        async def espiar_family(family_id):
            self.revocadas.append(family_id)
            await revoke_family(family_id)

        async def espiar_all(user_id):
            self.usuarios_cerrados.append(user_id)
            await revoke_all(user_id)

        self.tokens.revoke_family = espiar_family
        self.tokens.revoke_all_for_user = espiar_all

    def logout(self) -> LogoutUseCase:
        return LogoutUseCase(tokens=self.tokens, uow=self.uow)

    def logout_all(self) -> LogoutAllUseCase:
        return LogoutAllUseCase(tokens=self.tokens, uow=self.uow)


def _usuario() -> User:
    ahora = datetime.now(UTC)
    return User(
        id=USER_ID,
        email="ana@ejemplo.com",
        password_hash="$argon2id$irrelevante",
        email_verified=True,
        created_at=ahora,
        updated_at=ahora,
    )


def _access(*, family_id: str | None = str(FAMILY_ID), minutos: int = 15) -> str:
    return create_access_token(
        str(USER_ID),
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        expires_minutes=minutos,
        family_id=family_id,
    )


@pytest.fixture
def dobles() -> _Dobles:
    return _Dobles()


@pytest.fixture
def client(dobles) -> TestClient:
    app.dependency_overrides[get_logout_use_case] = dobles.logout
    app.dependency_overrides[get_logout_all_use_case] = dobles.logout_all
    app.dependency_overrides[get_current_user] = _usuario
    with TestClient(app) as cliente:
        yield cliente
    app.dependency_overrides.clear()


def _cabecera(respuesta, nombre: str) -> str:
    return next(c for c in respuesta.headers.get_list("set-cookie") if c.startswith(nombre))


# ---------------------------------------------------------------------------
# /logout
# ---------------------------------------------------------------------------


def test_revoca_la_familia_del_token(client, dobles) -> None:
    """El family_id sale del token FIRMADO, no de la peticion.

    Es toda la seguridad del endpoint: si llegara del cuerpo, cualquiera
    cerraria la sesion de otro escribiendo un UUID ajeno.
    """
    client.cookies.set(ACCESS_COOKIE, _access())

    r = client.post(LOGOUT)

    assert r.status_code == 200
    assert dobles.revocadas == [FAMILY_ID]


def test_no_acepta_el_family_id_por_el_cuerpo(client, dobles) -> None:
    ajena = uuid.uuid4()
    client.cookies.set(ACCESS_COOKIE, _access())

    client.post(LOGOUT, json={"family_id": str(ajena)})

    assert ajena not in dobles.revocadas


def test_borra_las_dos_cookies(client) -> None:
    client.cookies.set(ACCESS_COOKIE, _access())

    r = client.post(LOGOUT)

    borradas = {c.split("=")[0] for c in r.headers.get_list("set-cookie")}
    assert borradas == {ACCESS_COOKIE, REFRESH_COOKIE}


def test_borra_cada_cookie_con_su_path(client) -> None:
    """Un Path distinto del de creacion y el navegador la deja donde estaba.

    La respuesta diria que se cerro sesion y la cookie seguiria ahi. Es un
    fallo clasico y silencioso.
    """
    client.cookies.set(ACCESS_COOKIE, _access())

    r = client.post(LOGOUT)

    assert f"Path={ACCESS_COOKIE_PATH}" in _cabecera(r, ACCESS_COOKIE)
    assert f"Path={REFRESH_COOKIE_PATH}" in _cabecera(r, REFRESH_COOKIE)


def test_sin_cookie_responde_401(client, dobles) -> None:
    r = client.post(LOGOUT)

    assert r.status_code == 401
    assert dobles.revocadas == []


def test_con_token_expirado_responde_410(client, dobles) -> None:
    """Decision tomada: logout es ruta protegida y exige token vigente.

    El cliente que recibe token_expired pide un refresh y reintenta.
    """
    client.cookies.set(ACCESS_COOKIE, _access(minutos=-1))

    r = client.post(LOGOUT)

    assert r.status_code == 410
    assert dobles.revocadas == []


def test_con_token_de_otro_secreto_responde_400(client, dobles) -> None:
    ajeno = create_access_token(
        str(USER_ID),
        secret="otro-secreto-de-al-menos-32-caracteres-aqui",
        algorithm=settings.jwt_algorithm,
        expires_minutes=15,
        family_id=str(uuid.uuid4()),
    )
    client.cookies.set(ACCESS_COOKIE, ajeno)

    r = client.post(LOGOUT)

    assert r.status_code == 400
    assert dobles.revocadas == []


def test_token_sin_fid_cierra_igual(client, dobles) -> None:
    """Compatibilidad con los access tokens emitidos antes del claim.

    No hay familia que revocar, pero las cookies se borran y la respuesta es
    200: el usuario pidio quedarse fuera y se queda fuera.
    """
    client.cookies.set(ACCESS_COOKIE, _access(family_id=None))

    r = client.post(LOGOUT)

    assert r.status_code == 200
    assert dobles.revocadas == []
    assert len(r.headers.get_list("set-cookie")) == 2


def test_cerrar_dos_veces_seguidas_no_falla(client) -> None:
    client.cookies.set(ACCESS_COOKIE, _access())

    primera = client.post(LOGOUT)
    client.cookies.set(ACCESS_COOKIE, _access())
    segunda = client.post(LOGOUT)

    assert primera.status_code == segunda.status_code == 200


def test_get_no_esta_permitido(client) -> None:
    assert client.get(LOGOUT).status_code == 405


# ---------------------------------------------------------------------------
# /logout-all
# ---------------------------------------------------------------------------


def test_logout_all_cierra_por_usuario_y_no_por_familia(client, dobles) -> None:
    """La diferencia entre los dos endpoints, en una asercion.

    logout-all no mira la familia: revoca por usuario, asi que alcanza a
    sesiones abiertas en dispositivos que este token no conoce.
    """
    client.cookies.set(ACCESS_COOKIE, _access())

    r = client.post(LOGOUT_ALL)

    assert r.status_code == 200
    assert dobles.usuarios_cerrados == [USER_ID]
    assert dobles.revocadas == []


def test_logout_all_borra_las_cookies(client) -> None:
    client.cookies.set(ACCESS_COOKIE, _access())

    r = client.post(LOGOUT_ALL)

    borradas = {c.split("=")[0] for c in r.headers.get_list("set-cookie")}
    assert borradas == {ACCESS_COOKIE, REFRESH_COOKIE}


def test_logout_all_sin_cookie_responde_401(client, dobles) -> None:
    # Se retira el doble de get_current_user: con el puesto, la ruta nunca
    # llegaria a mirar la cookie y este test no podria fallar. Los demas tests
    # del archivo lo necesitan para no depender de la base.
    app.dependency_overrides.pop(get_current_user)

    r = client.post(LOGOUT_ALL)

    assert r.status_code == 401
    assert dobles.usuarios_cerrados == []


def test_logout_all_usa_el_usuario_del_token_y_no_uno_enviado(
    client, dobles
) -> None:
    otro = uuid.uuid4()
    client.cookies.set(ACCESS_COOKIE, _access())

    client.post(LOGOUT_ALL, json={"user_id": str(otro)})

    assert dobles.usuarios_cerrados == [USER_ID]
