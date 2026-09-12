"""Smoke test del cableado.

Construye cada proveedor de dependencias y comprueba que no revienta. Existe
por un fallo real: `Settings` se quedo sin `email_verification_hours` y la app
devolvia 500 en /api/auth/signup con los 142 tests en verde, porque ninguno
armaba el grafo real.

Funciona con un MagicMock por sesion porque los repositorios solo la GUARDAN en
su constructor; no la usan hasta que se llama un metodo. Y construir es justo
donde vivia el bug: los settings se leen al armar el caso de uso, no al
ejecutarlo.

Cubre toda una clase de fallos: un setting que falte, una firma que cambie, una
dependencia mal enchufada.
"""

from unittest.mock import MagicMock

from apps.api.dependencies import (
    get_email_sender,
    get_login_use_case,
    get_signup_use_case,
    get_verify_email_use_case,
)
from packages.core.application.usecases.auth.login import LoginUseCase
from packages.core.application.usecases.auth.signup import SignupUseCase
from packages.core.application.usecases.auth.verify_email import VerifyEmailUseCase


def test_the_email_sender_builds_from_settings():
    sender = get_email_sender()

    # Si faltara un setting, esto habria explotado al construirlo.
    assert sender.build_verification_link("abc").endswith("/verify?token=abc")


def test_the_signup_use_case_builds():
    caso = get_signup_use_case(MagicMock(), get_email_sender())

    assert isinstance(caso, SignupUseCase)


def test_the_login_use_case_builds():
    caso = get_login_use_case(MagicMock())

    assert isinstance(caso, LoginUseCase)


def test_the_verify_email_use_case_builds():
    caso = get_verify_email_use_case(MagicMock())

    assert isinstance(caso, VerifyEmailUseCase)


def test_the_app_registers_the_auth_routes():
    """Que el router este incluido y con el prefijo correcto."""
    from fastapi.testclient import TestClient

    from apps.api.main import app

    rutas = TestClient(app).get("/openapi.json").json()["paths"]

    assert "/api/auth/signup" in rutas
    assert "/api/auth/verify-email" in rutas
    assert "/api/auth/login" in rutas
