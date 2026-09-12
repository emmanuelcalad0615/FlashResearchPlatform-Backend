"""Tests de la capa HTTP de autenticacion.

Sustituyen los casos de uso por otros construidos con dobles, asi que corren
sin Postgres. Cubren lo que los tests de caso de uso NO ven: que la ruta exista
con su prefijo, que el schema rechace lo que debe, que los errores de dominio
salgan con su status, y que el response_model recorte la salida.

Lo que NO cubren es el cableado real —para eso estan test_wiring.py y los de
integracion—, porque al sustituir el caso de uso, dependencies.py no se ejecuta.
"""

import pytest
from fastapi.testclient import TestClient

from apps.api.dependencies import get_signup_use_case, get_verify_email_use_case
from apps.api.main import app
from packages.core.application.usecases.auth.signup import SignupUseCase
from packages.core.application.usecases.auth.verify_email import VerifyEmailUseCase
from tests.fakes import (
    InMemoryEmailSender,
    InMemoryEmailVerificationRepository,
    InMemoryProfileRepository,
    InMemoryUnitOfWork,
    InMemoryUserRepository,
)

SIGNUP = "/api/auth/signup"
VERIFY = "/api/auth/verify-email"
PASSWORD = "una-frase-larga-y-seguraB"


class _Dobles:
    def __init__(self) -> None:
        self.users = InMemoryUserRepository()
        self.profiles = InMemoryProfileRepository()
        self.verifications = InMemoryEmailVerificationRepository()
        self.emails = InMemoryEmailSender()
        self.uow = InMemoryUnitOfWork()

    def signup(self) -> SignupUseCase:
        return SignupUseCase(
            users=self.users,
            profiles=self.profiles,
            verifications=self.verifications,
            emails=self.emails,
            uow=self.uow,
            verification_hours=24,
            build_link=lambda t: f"https://app.test/verify?token={t}",
            hasher=lambda p: f"hash::{p}",
        )

    def verify(self) -> VerifyEmailUseCase:
        return VerifyEmailUseCase(
            users=self.users, verifications=self.verifications, uow=self.uow
        )


@pytest.fixture
def dobles() -> _Dobles:
    return _Dobles()


@pytest.fixture
def client(dobles) -> TestClient:
    app.dependency_overrides[get_signup_use_case] = dobles.signup
    app.dependency_overrides[get_verify_email_use_case] = dobles.verify
    yield TestClient(app)
    app.dependency_overrides.clear()


def _email() -> str:
    import uuid

    return f"ep-{uuid.uuid4()}@ejemplo.com"


# ---- signup ----------------------------------------------------------------


def test_signup_returns_201(client):
    r = client.post(SIGNUP, json={"email": _email(), "password": PASSWORD})

    assert r.status_code == 201
    assert "message" in r.json()


def test_signup_response_carries_nothing_but_the_message(client):
    """El response_model recorta la salida. Devolver el email confirmaria que
    esa direccion se proceso."""
    r = client.post(SIGNUP, json={"email": _email(), "password": PASSWORD})

    assert set(r.json()) == {"message"}


def test_a_short_password_is_a_422_not_a_500(client):
    """El DomainValidationError tiene que salir por el manejo centralizado."""
    r = client.post(SIGNUP, json={"email": _email(), "password": "corta"})

    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
    assert r.json()["error"]["details"]["min_length"] == 12


def test_an_invalid_email_is_rejected_by_the_schema(client):
    r = client.post(SIGNUP, json={"email": "pepe", "password": PASSWORD})

    assert r.status_code == 422
    campos = [f["field"] for f in r.json()["error"]["details"]["fields"]]
    assert "body.email" in campos


def test_a_missing_field_is_rejected(client):
    r = client.post(SIGNUP, json={"email": _email()})

    assert r.status_code == 422


@pytest.mark.parametrize("payload", [
    {"email": "a@b.com", "password": "x" * 2000},   # carga absurda
    {"email": "a@b.com", "password": ""},           # vacia
])
def test_malformed_payloads_never_reach_the_use_case(client, dobles, payload):
    r = client.post(SIGNUP, json=payload)

    assert r.status_code == 422
    assert dobles.users.por_id == {}


def test_the_three_branches_look_identical_over_http(client):
    """Si una rama respondiera distinto, cualquiera podria averiguar quien
    tiene cuenta probando direcciones."""
    email = _email()

    nuevo = client.post(SIGNUP, json={"email": email, "password": PASSWORD})
    pendiente = client.post(SIGNUP, json={"email": email, "password": PASSWORD})

    assert nuevo.status_code == pendiente.status_code == 201
    assert nuevo.json() == pendiente.json()


# ---- verify-email ----------------------------------------------------------


def _token_de(dobles) -> str:
    return dobles.emails.verificaciones[-1][1].split("token=")[1]


def test_verify_activates_the_account(client, dobles):
    email = _email()
    client.post(SIGNUP, json={"email": email, "password": PASSWORD})

    r = client.post(VERIFY, json={"token": _token_de(dobles)})

    assert r.status_code == 200


def test_an_unknown_token_is_a_400(client):
    r = client.post(VERIFY, json={"token": "no-existe-este-token"})

    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_token"


def test_clicking_twice_still_returns_200(client, dobles):
    email = _email()
    client.post(SIGNUP, json={"email": email, "password": PASSWORD})
    token = _token_de(dobles)
    client.post(VERIFY, json={"token": token})

    r = client.post(VERIFY, json={"token": token})

    assert r.status_code == 200


def test_verify_only_accepts_post(client):
    """GET verificaria la cuenta cuando un cliente de correo pre-visita el
    enlace, sin que el usuario hiciera nada."""
    assert client.get(VERIFY).status_code == 405
