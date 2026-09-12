"""Tests HTTP de POST /api/auth/refresh.

Sin Postgres: el caso de uso se sustituye por uno con dobles. Cubren lo que los
tests del caso de uso no ven —las cookies, el Path, el mapeo de errores— y una
cosa que solo se puede comprobar aqui: que la renovacion encadena.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from apps.api.dependencies import get_refresh_use_case
from apps.api.infrastructure.cookies import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    REFRESH_COOKIE_PATH,
)
from apps.api.main import app
from packages.core.application.usecases.auth.refresh import RefreshUseCase
from packages.core.domain.policies.tokens import (
    generate_opaque_token,
    hash_opaque_token,
)
from tests.fakes import InMemoryRefreshTokenRepository, InMemoryUnitOfWork

REFRESH = "/api/auth/refresh"
SECRET = "un-secreto-de-prueba-de-al-menos-32-bytes-de-largo"


class _Dobles:
    def __init__(self) -> None:
        self.tokens = InMemoryRefreshTokenRepository()
        self.uow = InMemoryUnitOfWork()
        self.user_id = uuid.uuid4()
        self.family_id = uuid.uuid4()

    def caso(self) -> RefreshUseCase:
        return RefreshUseCase(
            tokens=self.tokens,
            uow=self.uow,
            jwt_secret=SECRET,
            jwt_algorithm="HS256",
            access_token_minutes=15,
            refresh_token_days=30,
        )

    async def emitir(self, *, expira_en_dias: int = 30) -> str:
        token = generate_opaque_token()
        await self.tokens.create(
            user_id=self.user_id,
            token_hash=hash_opaque_token(token),
            family_id=self.family_id,
            expires_at=datetime.now(UTC) + timedelta(days=expira_en_dias),
            user_agent=None,
        )
        return token


@pytest.fixture
async def dobles() -> _Dobles:
    return _Dobles()


def _sembrar(client: TestClient, token: str) -> None:
    """Deja la cookie con el MISMO Path con el que la emite el servidor.

    Sin el path, httpx la guarda en "/" y convive con la que devuelve el
    refresh, que va en /api/auth/refresh. Serian dos cookies con el mismo
    nombre y el cliente mandaria la vieja: la segunda renovacion fallaria por
    culpa del andamiaje del test, no del codigo.
    """
    # Vaciar primero: una peticion anterior pudo dejar en el jar la cookie que
    # devolvio el servidor, con dominio "testserver". Convivirian dos cookies
    # del mismo nombre y la peticion mandaria las dos, con lo que el servidor
    # atenderia la valida y el test quedaria probando lo contrario de lo que
    # dice su nombre.
    client.cookies.clear()

    # domain="" y no "testserver": el jar de httpx sigue las reglas de
    # http.cookiejar, que no da por buena una coincidencia exacta de host sin
    # punto inicial. Con el dominio vacio la cookie vale para cualquier host y
    # el path sigue siendo el que importa aqui.
    client.cookies.set(REFRESH_COOKIE, token, domain="", path=REFRESH_COOKIE_PATH)


@pytest.fixture
def client(dobles) -> TestClient:
    app.dependency_overrides[get_refresh_use_case] = dobles.caso
    with TestClient(app) as cliente:
        yield cliente
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Camino feliz
# ---------------------------------------------------------------------------


async def test_devuelve_200_y_reemplaza_las_dos_cookies(client, dobles) -> None:
    viejo = await dobles.emitir()
    _sembrar(client, viejo)

    r = client.post(REFRESH)

    assert r.status_code == 200
    entregadas = {c.split("=")[0] for c in r.headers.get_list("set-cookie")}
    assert entregadas == {ACCESS_COOKIE, REFRESH_COOKIE}


async def test_la_cookie_de_refresh_sale_con_su_path(client, dobles) -> None:
    """Si el Path cambiara, el navegador dejaria de mandarla a esta ruta y la
    renovacion moriria en silencio."""
    _sembrar(client, await dobles.emitir())

    r = client.post(REFRESH)

    cabecera = next(
        c for c in r.headers.get_list("set-cookie") if c.startswith(REFRESH_COOKIE)
    )
    assert f"Path={REFRESH_COOKIE_PATH}" in cabecera


async def test_la_cookie_entregada_sirve_para_renovar_otra_vez(client, dobles) -> None:
    """La cadena: renovar dos veces seguidas con lo que devuelve el servidor.

    Es lo que fallaria si el controller reemplazara solo la cookie de acceso:
    la segunda renovacion mandaria el refresh ya gastado y la deteccion de robo
    saltaria contra el propio usuario. No se ve en el primer refresh.
    """
    _sembrar(client, await dobles.emitir())

    primera = client.post(REFRESH)
    segunda = client.post(REFRESH)

    assert primera.status_code == 200
    assert segunda.status_code == 200


async def test_el_token_nuevo_es_distinto_del_presentado(client, dobles) -> None:
    viejo = await dobles.emitir()
    _sembrar(client, viejo)

    r = client.post(REFRESH)

    cabecera = next(
        c for c in r.headers.get_list("set-cookie") if c.startswith(REFRESH_COOKIE)
    )
    assert viejo not in cabecera


# ---------------------------------------------------------------------------
# Rechazos
# ---------------------------------------------------------------------------


def test_sin_cookie_responde_401(client) -> None:
    r = client.post(REFRESH)

    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_token_desconocido_responde_400(client) -> None:
    _sembrar(client, generate_opaque_token())

    r = client.post(REFRESH)

    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_token"


async def test_token_expirado_responde_400(client, dobles) -> None:
    _sembrar(client, await dobles.emitir(expira_en_dias=-1))

    r = client.post(REFRESH)

    assert r.status_code == 400


async def test_el_token_reutilizado_no_se_distingue_de_los_demas(
    client, dobles
) -> None:
    """La decision de no delatar la deteccion.

    Si reutilizar diera un error propio, quien robo el token sabria que fue
    descubierto y que la familia esta quemada. Respondiendo igual que a una
    cadena inventada, no aprende nada.
    """
    viejo = await dobles.emitir()
    _sembrar(client, viejo)
    client.post(REFRESH)

    _sembrar(client, viejo)
    reutilizado = client.post(REFRESH)

    _sembrar(client, generate_opaque_token())
    inventado = client.post(REFRESH)

    def sin_request_id(respuesta) -> dict:
        # El request_id es distinto en cada peticion por diseno: sirve para
        # cruzar la respuesta con el log. Todo lo demas tiene que coincidir.
        return {
            k: v for k, v in respuesta.json()["error"].items() if k != "request_id"
        }

    assert reutilizado.status_code == inventado.status_code
    assert sin_request_id(reutilizado) == sin_request_id(inventado)


async def test_un_rechazo_no_entrega_cookies(client, dobles) -> None:
    """Que una renovacion fallida no pise la sesion que el usuario ya tenia."""
    _sembrar(client, await dobles.emitir(expira_en_dias=-1))

    r = client.post(REFRESH)

    assert r.headers.get_list("set-cookie") == []


def test_no_acepta_el_token_en_el_cuerpo(client) -> None:
    """Solo la cookie autentica.

    Aceptarlo por el cuerpo abriria una via que el navegador no protege con
    SameSite y reabriria el CSRF que las cookies cierran.
    """
    r = client.post(REFRESH, json={"refresh_token": generate_opaque_token()})

    assert r.status_code == 401


def test_get_no_esta_permitido(client) -> None:
    """POST y no GET: gasta un token y emite otro, asi que no es repetible."""
    r = client.get(REFRESH)

    assert r.status_code == 405
