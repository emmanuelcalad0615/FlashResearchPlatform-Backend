"""Tests de las cookies de sesion.

Cada bandera de estas cookies es una defensa concreta, y ninguna es evidente al
leer el codigo. Un test por bandera para que quitarla rompa algo.
"""

import pytest
from fastapi import Response

from apps.api.config import settings
from apps.api.infrastructure.cookies import (
    ACCESS_COOKIE,
    ACCESS_COOKIE_PATH,
    REFRESH_COOKIE,
    REFRESH_COOKIE_PATH,
    clear_session_cookies,
    set_session_cookies,
)


def _cookies(response: Response) -> dict[str, str]:
    """Devuelve {nombre: cabecera completa} de las Set-Cookie emitidas."""
    salida = {}
    for clave, valor in response.raw_headers:
        if clave == b"set-cookie":
            cabecera = valor.decode()
            salida[cabecera.split("=", 1)[0]] = cabecera
    return salida


@pytest.fixture
def emitidas() -> dict[str, str]:
    response = Response()
    set_session_cookies(response, access_token="ACCESS123", refresh_token="REFRESH456")
    return _cookies(response)


# ---- Al abrir sesion -------------------------------------------------------


def test_both_cookies_are_issued(emitidas):
    assert set(emitidas) == {ACCESS_COOKIE, REFRESH_COOKIE}


def test_the_tokens_travel_in_them(emitidas):
    assert "ACCESS123" in emitidas[ACCESS_COOKIE]
    assert "REFRESH456" in emitidas[REFRESH_COOKIE]


@pytest.mark.parametrize("cookie", [ACCESS_COOKIE, REFRESH_COOKIE])
def test_every_cookie_is_httponly(emitidas, cookie):
    """SEGURIDAD: sin HttpOnly, un XSS lee el token con una linea de
    JavaScript y se lo lleva para usarlo desde otra maquina."""
    assert "HttpOnly" in emitidas[cookie]


@pytest.mark.parametrize("cookie", [ACCESS_COOKIE, REFRESH_COOKIE])
def test_every_cookie_declares_samesite(emitidas, cookie):
    """SEGURIDAD: es lo que corta el CSRF. Sin SameSite, un sitio ajeno puede
    provocar peticiones a la API y la cookie viaja igual."""
    assert "SameSite=lax" in emitidas[cookie]


def test_the_access_cookie_travels_everywhere(emitidas):
    assert f"Path={ACCESS_COOKIE_PATH};" in emitidas[ACCESS_COOKIE] or emitidas[
        ACCESS_COOKIE
    ].endswith(f"Path={ACCESS_COOKIE_PATH}")


def test_the_refresh_cookie_is_scoped_to_its_route(emitidas):
    """La llave larga no viaja en las cientos de peticiones de un dashboard.
    Menos veces viaja, menos oportunidades hay de que se filtre."""
    assert f"Path={REFRESH_COOKIE_PATH}" in emitidas[REFRESH_COOKIE]
    assert "Path=/;" not in emitidas[REFRESH_COOKIE]


def test_each_max_age_matches_the_token_it_carries(emitidas):
    """Si la cookie durara mas que el token, el navegador seguiria mandando uno
    muerto y la API respondería 410 hasta que se cerrara el navegador."""
    assert f"Max-Age={settings.access_token_minutes * 60}" in emitidas[ACCESS_COOKIE]
    esperado = settings.refresh_token_days * 24 * 60 * 60
    assert f"Max-Age={esperado}" in emitidas[REFRESH_COOKIE]


def test_secure_follows_the_setting(emitidas):
    """En desarrollo va apagada porque no hay HTTPS. Una guarda de arranque
    impide que llegue asi a produccion."""
    presente = "Secure" in emitidas[ACCESS_COOKIE]
    assert presente is settings.cookie_secure


# ---- Al cerrar sesion ------------------------------------------------------


@pytest.fixture
def borradas() -> dict[str, str]:
    response = Response()
    clear_session_cookies(response)
    return _cookies(response)


def test_clearing_expires_both_cookies(borradas):
    assert "Max-Age=0" in borradas[ACCESS_COOKIE]
    assert "Max-Age=0" in borradas[REFRESH_COOKIE]


def test_clearing_uses_the_same_paths(borradas):
    """EL fallo clasico: para eliminar una cookie hay que reenviarla con el
    MISMO path. Si no coincide, el navegador la deja donde estaba, la respuesta
    dice que se cerro sesion, y la cookie sigue viva."""
    assert f"Path={REFRESH_COOKIE_PATH}" in borradas[REFRESH_COOKIE]
    assert f"Path={ACCESS_COOKIE_PATH}" in borradas[ACCESS_COOKIE]


def test_clearing_carries_no_token(borradas):
    assert 'access_token=""' in borradas[ACCESS_COOKIE]
    assert 'refresh_token=""' in borradas[REFRESH_COOKIE]
