"""Tests HTTP de POST /api/auth/resend-verification, con y sin Postgres.

La parte rapida comprueba la forma de la respuesta; la de integracion
comprueba lo unico que no se ve desde fuera: que el enlace reenviado sirva de
verdad para activar la cuenta y que el anterior deje de servir.
"""

import uuid

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from apps.api.config import settings
from apps.api.dependencies import (
    get_email_sender,
    get_resend_verification_use_case,
    get_session,
)
from apps.api.main import app
from packages.core.application.usecases.auth.resend_verification import (
    ResendVerificationUseCase,
)
from packages.core.domain.policies.passwords import hash_password
from tests.conftest import requiere_bd
from tests.fakes import (
    InMemoryEmailSender,
    InMemoryEmailVerificationRepository,
    InMemoryUnitOfWork,
    InMemoryUserRepository,
)

RESEND = "/api/auth/resend-verification"
SIGNUP = "/api/auth/signup"
VERIFY = "/api/auth/verify-email"
LOGIN = "/api/auth/login"
PASSWORD = "una-frase-larga-y-seguraB"
EMAIL = "ana@ejemplo.com"


class _CorreoConEnlace(InMemoryEmailSender):
    def build_verification_link(self, token: str) -> str:
        return f"https://app.test/verify?token={token}"


# ---------------------------------------------------------------------------
# Rapido: la forma de la respuesta
# ---------------------------------------------------------------------------


class _Dobles:
    def __init__(self) -> None:
        self.users = InMemoryUserRepository()
        self.verifications = InMemoryEmailVerificationRepository()
        self.emails = _CorreoConEnlace()
        self.uow = InMemoryUnitOfWork()

    def caso(self) -> ResendVerificationUseCase:
        return ResendVerificationUseCase(
            users=self.users,
            verifications=self.verifications,
            emails=self.emails,
            uow=self.uow,
            verification_hours=24,
            cooldown_seconds=60,
            build_link=self.emails.build_verification_link,
        )


@pytest.fixture
async def dobles() -> _Dobles:
    d = _Dobles()
    await d.users.create(uuid.uuid4(), EMAIL, hash_password(PASSWORD))
    return d


@pytest.fixture
def client(dobles) -> TestClient:
    app.dependency_overrides[get_resend_verification_use_case] = dobles.caso
    with TestClient(app) as cliente:
        yield cliente
    app.dependency_overrides.clear()


def test_responde_200_a_una_cuenta_pendiente(client) -> None:
    r = client.post(RESEND, json={"email": EMAIL})

    assert r.status_code == 200


def test_responde_lo_mismo_a_un_email_desconocido(client) -> None:
    """La propiedad que impide usar el endpoint como comprobador de correos."""
    conocido = client.post(RESEND, json={"email": EMAIL})
    desconocido = client.post(RESEND, json={"email": "nadie@ejemplo.com"})

    assert conocido.status_code == desconocido.status_code == 200
    assert conocido.json() == desconocido.json()


def test_la_respuesta_no_devuelve_el_email(client) -> None:
    """Devolverlo confirmaria que se proceso esa direccion."""
    cuerpo = client.post(RESEND, json={"email": EMAIL}).json()

    assert EMAIL not in str(cuerpo)


def test_un_email_malformado_es_422(client) -> None:
    """Aqui SI se valida el formato, al reves que en el login.

    No filtra nada: un texto sin arroba no es una direccion, exista o no la
    cuenta. Y rechazarlo evita trabajo inutil.
    """
    r = client.post(RESEND, json={"email": "no-soy-un-email"})

    assert r.status_code == 422


def test_get_no_esta_permitido(client) -> None:
    assert client.get(RESEND).status_code == 405


# ---------------------------------------------------------------------------
# Integracion: el enlace reenviado sirve
# ---------------------------------------------------------------------------

pytestmark_integration = [pytest.mark.integration, requiere_bd]


@pytest.fixture
def correo() -> _CorreoConEnlace:
    return _CorreoConEnlace()


@pytest.fixture
def sin_enfriamiento(monkeypatch) -> None:
    """Apaga el enfriamiento para los tests de integracion.

    El registro acaba de emitir un token, asi que un reenvio inmediato se
    frena: es el comportamiento correcto, pero convierte estos tests en una
    comprobacion del enfriamiento en vez del reenvio. Peor aun, los haria pasar
    por la razon equivocada —no llega correo porque esta frenado, no porque la
    rama haga lo que dice.

    El enfriamiento se prueba entero en test_resend_verification_usecase.py,
    que puede envejecer el token anterior sin esperar un minuto real.
    """
    monkeypatch.setattr(settings, "resend_verification_cooldown_seconds", 0)


@pytest_asyncio.fixture
async def http(db_session, correo, sin_enfriamiento) -> AsyncClient:
    app.dependency_overrides[get_session] = lambda: db_session
    app.dependency_overrides[get_email_sender] = lambda: correo
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as cliente:
        yield cliente
    app.dependency_overrides.clear()


def _token(correo) -> str:
    return correo.verificaciones[-1][1].split("token=")[1]


@pytest.mark.integration
@requiere_bd
async def test_el_enlace_reenviado_activa_la_cuenta(http, correo) -> None:
    """El recorrido completo de quien perdio su primer correo."""
    email = f"resend-{uuid.uuid4()}@ejemplo.com"
    await http.post(SIGNUP, json={"email": email, "password": PASSWORD})
    primero = _token(correo)

    await http.post(RESEND, json={"email": email})
    segundo = _token(correo)

    assert segundo != primero
    assert (await http.post(VERIFY, json={"token": segundo})).status_code == 200
    assert (
        await http.post(LOGIN, json={"email": email, "password": PASSWORD})
    ).status_code == 200


@pytest.mark.integration
@requiere_bd
async def test_el_enlace_anterior_deja_de_servir(http, correo) -> None:
    """Solo el ultimo vale.

    Sin esto, cada reenvio sumaria un enlace valido mas circulando por el buzon
    del usuario y por los logs del proveedor de correo.
    """
    email = f"resend-{uuid.uuid4()}@ejemplo.com"
    await http.post(SIGNUP, json={"email": email, "password": PASSWORD})
    primero = _token(correo)

    await http.post(RESEND, json={"email": email})

    r = await http.post(VERIFY, json={"token": primero})
    assert r.status_code == 400


@pytest.mark.integration
@requiere_bd
async def test_una_cuenta_ya_verificada_no_recibe_enlace(http, correo) -> None:
    email = f"resend-{uuid.uuid4()}@ejemplo.com"
    await http.post(SIGNUP, json={"email": email, "password": PASSWORD})
    await http.post(VERIFY, json={"token": _token(correo)})
    enviados = len(correo.verificaciones)

    await http.post(RESEND, json={"email": email})

    assert len(correo.verificaciones) == enviados
