"""Tests del caso de uso de verificacion de correo. Sin base de datos."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from packages.core.application.usecases.auth.verify_email import VerifyEmailUseCase
from packages.core.domain.errors import InvalidTokenError, TokenExpiredError
from packages.core.domain.policies.tokens import (
    generate_opaque_token,
    hash_opaque_token,
)
from tests.fakes import (
    InMemoryEmailVerificationRepository,
    InMemoryUnitOfWork,
    InMemoryUserRepository,
)

EMAIL = "ana@ejemplo.com"


class _Contexto:
    def __init__(self) -> None:
        self.users = InMemoryUserRepository()
        self.verifications = InMemoryEmailVerificationRepository()
        self.uow = InMemoryUnitOfWork()
        self.caso = VerifyEmailUseCase(
            users=self.users, verifications=self.verifications, uow=self.uow
        )

    async def preparar(self, *, horas: int = 24) -> str:
        """Crea un usuario sin verificar con su token. Devuelve el token."""
        self.user_id = uuid4()
        await self.users.create(self.user_id, EMAIL, "hash")
        self.token = generate_opaque_token()
        self.verificacion = await self.verifications.create(
            user_id=self.user_id,
            token_hash=hash_opaque_token(self.token),
            expires_at=datetime.now(UTC) + timedelta(hours=horas),
        )
        return self.token


@pytest.fixture
async def ctx() -> _Contexto:
    contexto = _Contexto()
    await contexto.preparar()
    return contexto


# ---- Camino feliz ----------------------------------------------------------


async def test_a_valid_token_activates_the_account(ctx):
    assert (await ctx.users.get_by_id(ctx.user_id)).email_verified is False

    await ctx.caso.execute(ctx.token)

    assert (await ctx.users.get_by_id(ctx.user_id)).email_verified is True


async def test_the_token_is_spent(ctx):
    await ctx.caso.execute(ctx.token)

    assert ctx.verifications.por_id[ctx.verificacion.id].is_used is True


async def test_both_writes_are_committed_together(ctx):
    """Si la primera confirmara sola y la segunda fallara, el token quedaria
    reutilizable y cualquiera con esa cadena podria re-verificar la cuenta."""
    await ctx.caso.execute(ctx.token)

    assert ctx.uow.commits == 1


# ---- Rechazos --------------------------------------------------------------


async def test_an_unknown_token_is_rejected(ctx):
    inventado = generate_opaque_token()

    with pytest.raises(InvalidTokenError):
        await ctx.caso.execute(inventado)


async def test_an_expired_token_raises_token_expired(ctx):
    """410 y no 400: al cliente le sirve saber que existio pero caduco, para
    ofrecer el reenvio en vez de mandar al login."""
    vencida = replace(
        ctx.verificacion, expires_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    ctx.verifications.por_id[ctx.verificacion.id] = vencida

    with pytest.raises(TokenExpiredError):
        await ctx.caso.execute(ctx.token)


async def test_an_expired_token_does_not_activate_the_account(ctx):
    vencida = replace(
        ctx.verificacion, expires_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    ctx.verifications.por_id[ctx.verificacion.id] = vencida

    with pytest.raises(TokenExpiredError):
        await ctx.caso.execute(ctx.token)

    assert (await ctx.users.get_by_id(ctx.user_id)).email_verified is False


# ---- Token ya usado --------------------------------------------------------


async def test_clicking_twice_succeeds(ctx):
    """Idempotente: repetir la operacion no cambia nada, asi que no es un
    error. Cubre el doble clic y los clientes que pre-visitan enlaces."""
    await ctx.caso.execute(ctx.token)

    await ctx.caso.execute(ctx.token)      # no lanza

    assert (await ctx.users.get_by_id(ctx.user_id)).email_verified is True


async def test_clicking_twice_does_not_commit_again(ctx):
    await ctx.caso.execute(ctx.token)

    await ctx.caso.execute(ctx.token)

    assert ctx.uow.commits == 1


async def test_a_spent_token_on_an_unverified_account_is_rejected(ctx):
    """Estado imposible: el token se gasto pero la cuenta no quedo verificada.
    Solo puede venir de datos corruptos, asi que no se trata como exito."""
    await ctx.verifications.mark_used(ctx.verificacion.id)

    with pytest.raises(InvalidTokenError):
        await ctx.caso.execute(ctx.token)


# ---- Seguridad -------------------------------------------------------------


async def test_the_plaintext_token_is_never_stored(ctx):
    """Se busca por hash porque es lo unico guardado. Si el token en claro
    estuviera en la base, un volcado permitiria verificar cuentas ajenas."""
    assert ctx.token not in ctx.verifications.id_por_hash
    assert hash_opaque_token(ctx.token) in ctx.verifications.id_por_hash
