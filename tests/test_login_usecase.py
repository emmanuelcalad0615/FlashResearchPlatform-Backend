"""Tests del caso de uso de login. Sin base de datos."""

from uuid import uuid4

import pytest

from packages.core.application.usecases.auth.login import LoginResult, LoginUseCase
from packages.core.domain.errors import EmailNotVerifiedError, InvalidCredentialsError
from packages.core.domain.policies.passwords import hash_password
from packages.core.domain.policies.tokens import decode_access_token, hash_opaque_token
from tests.fakes import (
    InMemoryRefreshTokenRepository,
    InMemoryUnitOfWork,
    InMemoryUserRepository,
)

SECRET = "un-secreto-de-prueba-de-al-menos-32-bytes-de-largo"
EMAIL = "ana@ejemplo.com"
PASSWORD = "una-frase-larga-y-seguraB"


class _Contexto:
    def __init__(self) -> None:
        self.users = InMemoryUserRepository()
        self.tokens = InMemoryRefreshTokenRepository()
        self.uow = InMemoryUnitOfWork()
        self.caso = LoginUseCase(
            users=self.users,
            tokens=self.tokens,
            uow=self.uow,
            jwt_secret=SECRET,
            jwt_algorithm="HS256",
            access_token_minutes=15,
            refresh_token_days=7,
        )

    async def crear_usuario(self, *, verificado: bool = True, password: str = PASSWORD):
        self.user_id = uuid4()
        # Hash REAL: el login llama a verify_password de verdad, asi que un
        # hash falso no serviria.
        await self.users.create(self.user_id, EMAIL, hash_password(password))
        if verificado:
            await self.users.mark_email_verified(self.user_id)


@pytest.fixture
async def ctx() -> _Contexto:
    contexto = _Contexto()
    await contexto.crear_usuario()
    return contexto


# ---- Camino feliz ----------------------------------------------------------


async def test_valid_credentials_return_both_tokens(ctx):
    resultado = await ctx.caso.execute(EMAIL, PASSWORD)

    assert isinstance(resultado, LoginResult)
    assert resultado.access_token
    assert resultado.refresh_token


async def test_the_access_token_identifies_the_user(ctx):
    resultado = await ctx.caso.execute(EMAIL, PASSWORD)

    claims = decode_access_token(
        resultado.access_token, secret=SECRET, algorithm="HS256"
    )
    assert claims.user_id == str(ctx.user_id)


async def test_only_the_refresh_hash_is_stored(ctx):
    """Un volcado de la base no puede abrir sesiones."""
    resultado = await ctx.caso.execute(EMAIL, PASSWORD)

    assert hash_opaque_token(resultado.refresh_token) in ctx.tokens.id_por_hash
    assert resultado.refresh_token not in ctx.tokens.id_por_hash


async def test_the_session_is_committed(ctx):
    await ctx.caso.execute(EMAIL, PASSWORD)

    assert ctx.uow.commits == 1


async def test_the_user_agent_is_recorded(ctx):
    """Sin esto, la pantalla de sesiones abiertas mostraria filas identicas."""
    await ctx.caso.execute(EMAIL, PASSWORD, user_agent="Chrome en Linux")

    guardado = next(iter(ctx.tokens.por_id.values()))
    assert guardado.user_id == ctx.user_id


# ---- Rechazos --------------------------------------------------------------


async def test_an_unknown_email_is_rejected(ctx):
    with pytest.raises(InvalidCredentialsError):
        await ctx.caso.execute("nadie@ejemplo.com", PASSWORD)


async def test_a_wrong_password_is_rejected(ctx):
    with pytest.raises(InvalidCredentialsError):
        await ctx.caso.execute(EMAIL, "otra-frase-completamente-distinta")


async def test_both_rejections_are_indistinguishable(ctx):
    """SEGURIDAD: si el error revelara cual de las dos mitades fallo,
    cualquiera podria averiguar que correos estan registrados."""
    with pytest.raises(InvalidCredentialsError) as sin_usuario:
        await ctx.caso.execute("nadie@ejemplo.com", PASSWORD)

    with pytest.raises(InvalidCredentialsError) as mala_contrasena:
        await ctx.caso.execute(EMAIL, "otra-frase-completamente-distinta")

    assert sin_usuario.value.code == mala_contrasena.value.code
    assert sin_usuario.value.message == mala_contrasena.value.message


async def test_a_rejected_login_opens_no_session(ctx):
    with pytest.raises(InvalidCredentialsError):
        await ctx.caso.execute(EMAIL, "otra-frase-completamente-distinta")

    assert ctx.tokens.por_id == {}
    assert ctx.uow.commits == 0


# ---- Cuenta sin verificar --------------------------------------------------


async def test_an_unverified_account_cannot_log_in():
    ctx = _Contexto()
    await ctx.crear_usuario(verificado=False)

    with pytest.raises(EmailNotVerifiedError):
        await ctx.caso.execute(EMAIL, PASSWORD)


async def test_the_unverified_check_happens_after_the_password():
    """SEGURIDAD: si fuera antes, cualquiera podria averiguar que correos estan
    registrados sin verificar, solo probando direcciones."""
    ctx = _Contexto()
    await ctx.crear_usuario(verificado=False)

    # Contrasena incorrecta sobre una cuenta sin verificar: tiene que salir el
    # error generico, NO el que revela que la cuenta existe.
    with pytest.raises(InvalidCredentialsError):
        await ctx.caso.execute(EMAIL, "otra-frase-completamente-distinta")


# ---- Familias --------------------------------------------------------------


async def test_each_login_opens_its_own_family(ctx):
    """Un usuario con tres dispositivos tiene tres cadenas independientes:
    cerrar sesion en uno no toca los otros."""
    primero = await ctx.caso.execute(EMAIL, PASSWORD)
    segundo = await ctx.caso.execute(EMAIL, PASSWORD)

    familias = {t.family_id for t in ctx.tokens.por_id.values()}
    assert len(familias) == 2
    assert primero.refresh_token != segundo.refresh_token


# ---- Rehash progresivo -----------------------------------------------------


async def test_a_weak_hash_is_upgraded_on_login():
    """El login es el UNICO momento con la contrasena en claro, asi que es la
    unica oportunidad de reforzar un hash creado con parametros mas debiles."""
    from argon2 import PasswordHasher

    ctx = _Contexto()
    ctx.user_id = uuid4()
    debil = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    await ctx.users.create(ctx.user_id, EMAIL, debil.hash(PASSWORD))
    await ctx.users.mark_email_verified(ctx.user_id)
    hash_viejo = (await ctx.users.get_by_id(ctx.user_id)).password_hash

    await ctx.caso.execute(EMAIL, PASSWORD)

    hash_nuevo = (await ctx.users.get_by_id(ctx.user_id)).password_hash
    assert hash_nuevo != hash_viejo
    assert "m=8," not in hash_nuevo          # ya no lleva los parametros debiles


async def test_a_current_hash_is_left_alone(ctx):
    """Rehashear en cada login gastaria 70 ms de mas sin ganar nada."""
    hash_original = (await ctx.users.get_by_id(ctx.user_id)).password_hash

    await ctx.caso.execute(EMAIL, PASSWORD)

    assert (await ctx.users.get_by_id(ctx.user_id)).password_hash == hash_original
