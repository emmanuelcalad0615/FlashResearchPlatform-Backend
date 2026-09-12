"""Tests de ResendVerificationUseCase.

Dos cosas se prueban aqui por encima del resto: que las cuatro ramas sean
indistinguibles desde fuera, y que el enfriamiento por cuenta funcione. La
primera evita que el endpoint sirva para averiguar que correos estan
registrados; la segunda evita que sirva para llenarle el buzon a alguien.
"""

import logging
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from packages.core.application.usecases.auth.resend_verification import (
    ResendVerificationUseCase,
)
from packages.core.domain.errors import EmailDeliveryError
from packages.core.domain.policies.passwords import hash_password
from tests.fakes import (
    InMemoryEmailSender,
    InMemoryEmailVerificationRepository,
    InMemoryUnitOfWork,
    InMemoryUserRepository,
)

EMAIL = "ana@ejemplo.com"
PASSWORD = "una-frase-larga-y-seguraB"
COOLDOWN = 60


class _CorreoQueFalla(InMemoryEmailSender):
    async def send_verification(self, *, to: str, link: str) -> None:
        raise EmailDeliveryError


class _Dobles:
    def __init__(self, emails=None) -> None:
        self.users = InMemoryUserRepository()
        self.verifications = InMemoryEmailVerificationRepository()
        self.emails = emails or InMemoryEmailSender()
        self.uow = InMemoryUnitOfWork()
        self.user_id = uuid.uuid4()

    def caso(self) -> ResendVerificationUseCase:
        return ResendVerificationUseCase(
            users=self.users,
            verifications=self.verifications,
            emails=self.emails,
            uow=self.uow,
            verification_hours=24,
            cooldown_seconds=COOLDOWN,
            build_link=lambda token: f"https://app.test/verify?token={token}",
        )

    async def crear_usuario(self, *, verificado: bool = False) -> None:
        await self.users.create(self.user_id, EMAIL, hash_password(PASSWORD))
        if verificado:
            await self.users.mark_email_verified(self.user_id)

    async def token_previo(self, *, hace_segundos: int) -> None:
        """Un token emitido hace N segundos, como lo dejaria el registro."""
        creado = await self.verifications.create(
            user_id=self.user_id,
            token_hash=uuid.uuid4().hex,
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
        self.verifications.por_id[creado.id] = replace(
            creado, created_at=datetime.now(UTC) - timedelta(seconds=hace_segundos)
        )

    @property
    def correos_enviados(self) -> int:
        return len(self.emails.verificaciones)


@pytest.fixture
async def dobles() -> _Dobles:
    d = _Dobles()
    await d.crear_usuario()
    return d


# ---------------------------------------------------------------------------
# Camino feliz
# ---------------------------------------------------------------------------


async def test_envia_un_enlace_nuevo(dobles) -> None:
    await dobles.caso().execute(EMAIL)

    assert dobles.correos_enviados == 1
    destino, enlace = dobles.emails.verificaciones[-1]
    assert destino == EMAIL
    assert "token=" in enlace


async def test_guarda_solo_el_hash_del_token(dobles) -> None:
    """Un volcado de la base no puede verificar cuentas ajenas."""
    await dobles.caso().execute(EMAIL)

    token = dobles.emails.verificaciones[-1][1].split("token=")[1]
    guardados = list(dobles.verifications.por_id.values())
    assert len(guardados) == 1
    assert token not in repr(guardados[0])


async def test_invalida_los_enlaces_anteriores(dobles) -> None:
    """Solo el ultimo enlace debe servir.

    Si cada reenvio dejara vivo el anterior, un usuario que pide tres reenvios
    tendria tres enlaces validos circulando por su buzon, por los reenvios que
    haga y por los logs del proveedor de correo. Bastaria con que uno se
    filtrara.
    """
    await dobles.token_previo(hace_segundos=COOLDOWN + 1)

    await dobles.caso().execute(EMAIL)

    vivos = [v for v in dobles.verifications.por_id.values() if not v.is_used]
    assert len(vivos) == 1


async def test_confirma_antes_de_enviar_el_correo(dobles) -> None:
    await dobles.caso().execute(EMAIL)

    assert dobles.uow.commits == 1


async def test_un_fallo_de_correo_no_revienta(dobles, caplog) -> None:
    """El token ya quedo emitido y confirmado.

    Devolver un error haria que el usuario reintentara y chocara con el
    enfriamiento, que es peor que no avisar.
    """
    dobles.emails = _CorreoQueFalla()

    with caplog.at_level(logging.ERROR):
        await dobles.caso().execute(EMAIL)

    assert "resend_verification_email_failed" in caplog.text


# ---------------------------------------------------------------------------
# Las cuatro ramas son indistinguibles desde fuera
# ---------------------------------------------------------------------------


async def test_email_desconocido_no_hace_nada_ni_falla(dobles) -> None:
    await dobles.caso().execute("nadie@ejemplo.com")

    assert dobles.correos_enviados == 0
    assert dobles.uow.commits == 0


async def test_cuenta_ya_verificada_no_recibe_nada(dobles) -> None:
    """Ni enlace ni aviso: el usuario ya puede entrar y un correo inesperado
    solo confundiria."""
    await dobles.users.mark_email_verified(dobles.user_id)

    await dobles.caso().execute(EMAIL)

    assert dobles.correos_enviados == 0


async def test_ninguna_rama_lanza_error(dobles) -> None:
    """La propiedad que sostiene todo lo demas.

    Si alguna rama lanzara, el controller devolveria un codigo distinto y el
    endpoint se convertiria en un comprobador de correos registrados: bastaria
    mirar el status para saber si una direccion tiene cuenta.
    """
    await dobles.users.mark_email_verified(dobles.user_id)

    await dobles.caso().execute("nadie@ejemplo.com")
    await dobles.caso().execute(EMAIL)
    await dobles.caso().execute(EMAIL)


# ---------------------------------------------------------------------------
# Enfriamiento por cuenta
# ---------------------------------------------------------------------------


async def test_no_reenvia_dentro_del_enfriamiento(dobles) -> None:
    await dobles.token_previo(hace_segundos=5)

    await dobles.caso().execute(EMAIL)

    assert dobles.correos_enviados == 0


async def test_reenvia_pasado_el_enfriamiento(dobles) -> None:
    await dobles.token_previo(hace_segundos=COOLDOWN + 1)

    await dobles.caso().execute(EMAIL)

    assert dobles.correos_enviados == 1


async def test_el_enfriamiento_no_emite_token_nuevo(dobles) -> None:
    """Frenado significa frenado: ni correo, ni fila, ni commit."""
    await dobles.token_previo(hace_segundos=5)
    antes = len(dobles.verifications.por_id)

    await dobles.caso().execute(EMAIL)

    assert len(dobles.verifications.por_id) == antes
    assert dobles.uow.commits == 0


async def test_el_enfriamiento_es_por_cuenta_y_no_global(dobles) -> None:
    """Frenar a Ana no puede frenar a Beto.

    Es la diferencia con el rate limit del middleware, que cuenta por IP: si
    este fuera global, un solo usuario pidiendo reenvios dejaria sin reenvio a
    todos los demas.
    """
    await dobles.token_previo(hace_segundos=5)
    beto_id, beto_email = uuid.uuid4(), "beto@ejemplo.com"
    await dobles.users.create(beto_id, beto_email, hash_password(PASSWORD))

    await dobles.caso().execute(EMAIL)
    await dobles.caso().execute(beto_email)

    assert dobles.correos_enviados == 1
    assert dobles.emails.verificaciones[-1][0] == beto_email


async def test_deja_rastro_del_frenado(dobles, caplog) -> None:
    await dobles.token_previo(hace_segundos=5)

    with caplog.at_level(logging.WARNING):
        await dobles.caso().execute(EMAIL)

    assert "resend_verification_throttled" in caplog.text
    assert EMAIL not in caplog.text


async def test_dos_reenvios_seguidos_solo_mandan_uno(dobles) -> None:
    """El escenario real: el usuario impaciente que pulsa dos veces."""
    await dobles.caso().execute(EMAIL)
    await dobles.caso().execute(EMAIL)

    assert dobles.correos_enviados == 1
