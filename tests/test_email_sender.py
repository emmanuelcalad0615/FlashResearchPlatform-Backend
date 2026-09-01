"""Tests del adapter SMTP.

No levantan Mailpit: se sustituye aiosmtplib.send con un mock, que no es red
sino reemplazar una funcion. Lo que NO se cubre aqui es que Mailpit reciba el
correo de verdad; eso necesita un test de integracion (HU-A15).
"""

from email.message import EmailMessage
from unittest.mock import AsyncMock, patch

import aiosmtplib
import pytest

from packages.core.domain.errors import EmailDeliveryError
from packages.core.infrastructure.email.smtp_sender import SmtpEmailSender

TOKEN = "aBcD3f9xK2mN-token-de-prueba"


def _sender(**kwargs) -> SmtpEmailSender:
    opciones = {
        "host": "localhost",
        "port": 1025,
        "username": "",
        "password": "",
        "sender": "no-reply@flashresearch.local",
        "frontend_base_url": "http://localhost:5173",
        "verification_hours": 24,
    } | kwargs
    return SmtpEmailSender(**opciones)


# ---- El enlace -------------------------------------------------------------


def test_verification_link_points_at_the_frontend():
    assert _sender().build_verification_link(TOKEN) == (
        f"http://localhost:5173/verify?token={TOKEN}"
    )


def test_trailing_slash_does_not_produce_a_double_slash():
    """https://x.com/ + /verify daria //verify, que rompe el enrutado."""
    remitente = _sender(frontend_base_url="https://flashresearch.com/")

    assert remitente.build_verification_link(TOKEN).startswith(
        "https://flashresearch.com/verify"
    )


# ---- El mensaje ------------------------------------------------------------


async def _capturar_mensaje(remitente: SmtpEmailSender, **kwargs) -> EmailMessage:
    with patch("aiosmtplib.send", new=AsyncMock()) as enviar:
        await remitente.send_verification(
            to="ana@ejemplo.com", link=remitente.build_verification_link(TOKEN)
        )
    return enviar.call_args.args[0]


async def test_message_has_sender_recipient_and_subject():
    mensaje = await _capturar_mensaje(_sender())

    assert mensaje["From"] == "no-reply@flashresearch.local"
    assert mensaje["To"] == "ana@ejemplo.com"
    assert "Flash Research" in mensaje["Subject"]


async def test_body_contains_the_link():
    mensaje = await _capturar_mensaje(_sender())

    assert f"http://localhost:5173/verify?token={TOKEN}" in mensaje.get_content()


async def test_body_never_shows_the_token_on_its_own():
    """SEGURIDAD: el token solo puede aparecer dentro del enlace.

    Suelto, invita a copiarlo o a que un asistente de correo lo indexe aparte.
    """
    cuerpo = (await _capturar_mensaje(_sender())).get_content()

    assert cuerpo.count(TOKEN) == 1
    assert f"token={TOKEN}" in cuerpo


# ---- TLS -------------------------------------------------------------------


async def test_without_credentials_it_does_not_negotiate_tls():
    """El caso de Mailpit: no pide autenticacion y no sale nada a internet."""
    with patch("aiosmtplib.send", new=AsyncMock()) as enviar:
        await _sender().send_verification(to="ana@ejemplo.com", link="http://x/y")

    assert enviar.call_args.kwargs["start_tls"] is False
    assert "username" not in enviar.call_args.kwargs


async def test_with_credentials_it_negotiates_tls():
    """El caso de produccion: sin TLS las credenciales viajarian en claro."""
    remitente = _sender(
        host="smtp.proveedor.com", port=587, username="usuario", password="clave"
    )

    with patch("aiosmtplib.send", new=AsyncMock()) as enviar:
        await remitente.send_verification(to="ana@ejemplo.com", link="http://x/y")

    assert enviar.call_args.kwargs["start_tls"] is True
    assert enviar.call_args.kwargs["username"] == "usuario"


# ---- Errores ---------------------------------------------------------------


@pytest.mark.parametrize(
    "fallo",
    [
        aiosmtplib.SMTPException("rechazado por el servidor"),
        ConnectionRefusedError("no hay nadie escuchando"),
    ],
)
async def test_delivery_failures_surface_as_a_domain_error(fallo):
    """El puerto no filtra aiosmtplib: quien llama nunca lo importa.

    Mismo patron que el adapter de Polygon con httpx.
    """
    with (
        patch("aiosmtplib.send", new=AsyncMock(side_effect=fallo)),
        pytest.raises(EmailDeliveryError) as capturado,
    ):
        await _sender().send_verification(to="ana@ejemplo.com", link="http://x/y")

    assert capturado.value.code == "email_delivery_failed"
    # La causa tecnica se conserva para el log, sin salir del adapter.
    assert capturado.value.__cause__ is fallo


async def test_delivery_error_does_not_leak_the_link():
    """SEGURIDAD: el enlace es una credencial de un solo uso; no puede acabar
    en los detalles de un error que viajan a los logs o al cliente."""
    enlace = f"http://localhost:5173/verify?token={TOKEN}"

    with (
        patch("aiosmtplib.send", new=AsyncMock(side_effect=aiosmtplib.SMTPException("fallo de entrega"))),
        pytest.raises(EmailDeliveryError) as capturado,
    ):
        await _sender().send_verification(to="ana@ejemplo.com", link=enlace)

    rendido = f"{capturado.value.message} {capturado.value.details}"
    assert TOKEN not in rendido
